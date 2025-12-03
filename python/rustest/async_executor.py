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

This module is called from Rust via PyO3 when a batch of async tests is ready.
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Coroutine

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass
class AsyncTestResult:
    """Result of a single async test execution."""

    test_id: str
    success: bool
    error_message: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    duration: float = 0.0


@dataclass
class AsyncTestSpec:
    """Specification for an async test to be executed."""

    test_id: str
    callable: Callable[..., Coroutine[Any, Any, Any]]
    args: tuple[Any, ...] = field(default_factory=tuple)


class AsyncBatchExecutor:
    """Executes a batch of async tests concurrently.

    This executor runs multiple async tests in parallel using asyncio.gather().
    Each test is wrapped to capture its result, including any exceptions.

    Usage:
        executor = AsyncBatchExecutor(event_loop)
        results = executor.run_batch(tests)

    The executor properly handles:
    - Exception capture per test (one failure doesn't stop others)
    - Stdout/stderr capture per test
    - Timing per test
    - Cancellation on shutdown
    """

    def __init__(self, event_loop: asyncio.AbstractEventLoop) -> None:
        """Initialize with the event loop to use for execution.

        Args:
            event_loop: The asyncio event loop to run tests in.
                       This should be the scoped loop (class/module/session).
        """
        super().__init__()
        self.event_loop = event_loop

    def run_batch(
        self,
        tests: Sequence[AsyncTestSpec],
        capture_output: bool = True,
    ) -> list[AsyncTestResult]:
        """Run a batch of async tests concurrently.

        Args:
            tests: List of test specifications to execute.
            capture_output: Whether to capture stdout/stderr per test.

        Returns:
            List of results, one per test, in the same order as input.
        """
        if not tests:
            return []

        # Create wrapper coroutines for each test
        async def run_all() -> list[AsyncTestResult | BaseException]:
            tasks = [self._run_single_test(spec, capture_output) for spec in tests]
            # return_exceptions=True ensures all tests complete even if some fail
            # unexpectedly (e.g., in the wrapper before try block)
            return await asyncio.gather(*tasks, return_exceptions=True)

        # Run all tests in the event loop
        raw_results = self.event_loop.run_until_complete(run_all())

        # Convert any unexpected exceptions to AsyncTestResult
        results: list[AsyncTestResult] = []
        for i, result in enumerate(raw_results):
            if isinstance(result, BaseException):
                # Unexpected exception in wrapper - convert to failure result
                results.append(
                    AsyncTestResult(
                        test_id=tests[i].test_id,
                        success=False,
                        error_message="".join(
                            traceback.format_exception(type(result), result, result.__traceback__)
                        ),
                        duration=0.0,
                    )
                )
            else:
                results.append(result)
        return results

    async def _run_single_test(
        self,
        spec: AsyncTestSpec,
        capture_output: bool,
    ) -> AsyncTestResult:
        """Run a single async test and capture its result.

        This method wraps the test execution to:
        1. Capture stdout/stderr if requested
        2. Measure execution time
        3. Handle exceptions gracefully

        Args:
            spec: The test specification.
            capture_output: Whether to capture stdout/stderr.

        Returns:
            The test result with all captured information.
        """
        import io
        import contextlib
        import time

        start_time = time.perf_counter()
        stdout_capture = io.StringIO() if capture_output else None
        stderr_capture = io.StringIO() if capture_output else None

        try:
            if capture_output:
                # Use contextlib to redirect stdout/stderr
                with (
                    contextlib.redirect_stdout(stdout_capture),
                    contextlib.redirect_stderr(stderr_capture),
                ):
                    # Call the test function and await the coroutine
                    coro = spec.callable(*spec.args)
                    await coro
            else:
                coro = spec.callable(*spec.args)
                await coro

            duration = time.perf_counter() - start_time
            return AsyncTestResult(
                test_id=spec.test_id,
                success=True,
                duration=duration,
                stdout=stdout_capture.getvalue() if stdout_capture else None,
                stderr=stderr_capture.getvalue() if stderr_capture else None,
            )

        except Exception as e:
            duration = time.perf_counter() - start_time
            # Format the exception with full traceback
            error_message = "".join(traceback.format_exception(type(e), e, e.__traceback__))

            return AsyncTestResult(
                test_id=spec.test_id,
                success=False,
                error_message=error_message,
                duration=duration,
                stdout=stdout_capture.getvalue() if stdout_capture else None,
                stderr=stderr_capture.getvalue() if stderr_capture else None,
            )


def run_async_tests_parallel(
    event_loop: asyncio.AbstractEventLoop,
    test_specs: list[tuple[str, Callable[..., Any], tuple[Any, ...]]],
    capture_output: bool = True,
) -> list[dict[str, Any]]:
    """Run multiple async tests in parallel on the given event loop.

    This is the main entry point called from Rust. It takes a list of test
    specifications and returns a list of results.

    Args:
        event_loop: The asyncio event loop to use.
        test_specs: List of (test_id, callable, args) tuples.
        capture_output: Whether to capture stdout/stderr.

    Returns:
        List of result dictionaries with keys:
        - test_id: str
        - success: bool
        - error_message: Optional[str]
        - stdout: Optional[str]
        - stderr: Optional[str]
        - duration: float
    """
    specs = [
        AsyncTestSpec(test_id=test_id, callable=callable_, args=args)
        for test_id, callable_, args in test_specs
    ]

    executor = AsyncBatchExecutor(event_loop)
    results = executor.run_batch(specs, capture_output)

    # Convert to dictionaries for easier PyO3 handling
    return [
        {
            "test_id": r.test_id,
            "success": r.success,
            "error_message": r.error_message,
            "stdout": r.stdout if r.stdout else None,
            "stderr": r.stderr if r.stderr else None,
            "duration": r.duration,
        }
        for r in results
    ]


async def _wrap_test_for_gather(
    test_id: str,
    coro: Coroutine[Any, Any, Any],
    capture_output: bool,
) -> dict[str, Any]:
    """Wrap a single test coroutine for use with asyncio.gather.

    This is an alternative implementation that can be used when the
    coroutines are already created (rather than callables).

    Args:
        test_id: Unique identifier for the test.
        coro: The test coroutine to execute.
        capture_output: Whether to capture stdout/stderr.

    Returns:
        Result dictionary with test execution info.
    """
    import io
    import contextlib
    import time

    start_time = time.perf_counter()
    stdout_capture = io.StringIO() if capture_output else None
    stderr_capture = io.StringIO() if capture_output else None

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

    except Exception as e:
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
    coroutines: list[tuple[str, Coroutine[Any, Any, Any]]],
    capture_output: bool = True,
) -> list[dict[str, Any]]:
    """Run pre-created coroutines in parallel.

    This variant is used when coroutines are already created (e.g., from
    calling async test functions with their resolved arguments).

    Note: Results are returned in the same order as input coroutines.
    This is guaranteed by asyncio.gather() which preserves order.

    Args:
        event_loop: The asyncio event loop to use.
        coroutines: List of (test_id, coroutine) tuples.
        capture_output: Whether to capture stdout/stderr.

    Returns:
        List of result dictionaries.
    """
    if not coroutines:
        return []

    async def run_all() -> list[dict[str, Any] | BaseException]:
        tasks = [
            _wrap_test_for_gather(test_id, coro, capture_output) for test_id, coro in coroutines
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
