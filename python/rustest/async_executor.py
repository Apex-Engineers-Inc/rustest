"""Parallel async test execution module.

This module provides the infrastructure for running multiple async tests
concurrently within the same event loop scope. Tests that share a loop scope
(class, module, or session) can run in parallel using asyncio.gather().

The key insight is that async tests spend most of their time awaiting I/O,
so running them concurrently allows other tests to make progress during
those await points.

Architecture:
- Tests with function scope: Cannot benefit from parallelism (each needs own loop)
- Tests with class/module/session scope: Can batch within that scope

Limitations:
- stdout/stderr capture may interleave between tests since all coroutines run
  on the same thread and output redirection uses thread-local storage. Output
  captured per-test is best-effort when tests print at overlapping await points.

This module is called from Rust via PyO3 when a batch of async tests is ready.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import time
import traceback
from typing import Any, Coroutine


async def _wrap_test_for_gather(
    test_id: str,
    coro: Coroutine[Any, Any, Any],
    capture_output: bool,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Wrap a single test coroutine for use with asyncio.gather.

    This is an alternative implementation that can be used when the
    coroutines are already created (rather than callables).

    Args:
        test_id: Unique identifier for the test.
        coro: The test coroutine to execute.
        capture_output: Whether to capture stdout/stderr.
        timeout: Optional timeout in seconds. If specified, the coroutine
            will be cancelled with asyncio.TimeoutError after this duration.

    Returns:
        Result dictionary with test execution info.
    """
    start_time = time.perf_counter()
    stdout_capture = io.StringIO() if capture_output else None
    stderr_capture = io.StringIO() if capture_output else None

    # Wrap with timeout if specified
    if timeout is not None:
        coro = asyncio.wait_for(coro, timeout=timeout)

    try:
        if capture_output:
            with (
                contextlib.redirect_stdout(stdout_capture),
                contextlib.redirect_stderr(stderr_capture),
            ):
                await coro
        else:
            await coro

        duration = time.perf_counter() - start_time
        return {
            "test_id": test_id,
            "success": True,
            "error_message": None,
            "stdout": stdout_capture.getvalue() if stdout_capture else None,
            "stderr": stderr_capture.getvalue() if stderr_capture else None,
            "duration": duration,
        }

    except asyncio.TimeoutError:
        # Handle timeout specifically for clearer error message
        duration = time.perf_counter() - start_time
        error_message = f"Test timed out after {timeout} seconds"

        return {
            "test_id": test_id,
            "success": False,
            "error_message": error_message,
            "stdout": stdout_capture.getvalue() if stdout_capture else None,
            "stderr": stderr_capture.getvalue() if stderr_capture else None,
            "duration": duration,
        }

    except BaseException as e:
        # Catch BaseException to handle CancelledError, SystemExit, KeyboardInterrupt
        # This ensures we capture stdout/stderr even for these cases
        duration = time.perf_counter() - start_time
        error_message = "".join(traceback.format_exception(type(e), e, e.__traceback__))

        return {
            "test_id": test_id,
            "success": False,
            "error_message": error_message,
            "stdout": stdout_capture.getvalue() if stdout_capture else None,
            "stderr": stderr_capture.getvalue() if stderr_capture else None,
            "duration": duration,
        }


def run_coroutines_parallel(
    event_loop: asyncio.AbstractEventLoop,
    coroutines: list[tuple[str, Coroutine[Any, Any, Any], float | None]],
    capture_output: bool = True,
) -> list[dict[str, Any]]:
    """Run pre-created coroutines in parallel.

    This variant is used when coroutines are already created (e.g., from
    calling async test functions with their resolved arguments).

    Note: Results are returned in the same order as input coroutines.
    This is guaranteed by asyncio.gather() which preserves order.

    Args:
        event_loop: The asyncio event loop to use.
        coroutines: List of (test_id, coroutine, timeout) tuples. Timeout is
            in seconds and can be None for no timeout.
        capture_output: Whether to capture stdout/stderr.

    Returns:
        List of result dictionaries.
    """
    if not coroutines:
        return []

    async def run_all() -> list[dict[str, Any] | BaseException]:
        tasks = [
            _wrap_test_for_gather(test_id, coro, capture_output, timeout)
            for test_id, coro, timeout in coroutines
        ]
        # return_exceptions=True ensures all tests complete even if some fail
        # unexpectedly (e.g., in the wrapper before try block)
        return await asyncio.gather(*tasks, return_exceptions=True)

    raw_results = event_loop.run_until_complete(run_all())

    # Convert any unexpected exceptions to result dictionaries
    results: list[dict[str, Any]] = []
    for i, result in enumerate(raw_results):
        if isinstance(result, BaseException):
            # Unexpected exception in wrapper - convert to failure result
            results.append(
                {
                    "test_id": coroutines[i][0],
                    "success": False,
                    "error_message": "".join(
                        traceback.format_exception(type(result), result, result.__traceback__)
                    ),
                    "stdout": None,
                    "stderr": None,
                    "duration": 0.0,
                }
            )
        else:
            results.append(result)
    return results
