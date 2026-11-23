"""anyio's pytest plugin, for a session that has it installed.

A port of the parts of ``anyio/pytest_plugin.py`` (4.x) that decide outcomes and ids: an
``async def`` test marked ``anyio`` gets the ``anyio_backend`` fixture, which is parametrized
by the installed backends (``test_x[asyncio]``, ``test_x[trio]``); the test, and every async
fixture it uses, runs on anyio's own test runner for that backend.  The runner is anyio's
(``create_test_runner``), so task groups, cancel scopes and trio behave as under pytest.

Not ported, and still pytest's (``_hookshim._PLUGIN_USES``, ``_fallback.autoload_reasons``):
``anyio_mode = auto``, the ``free_*_port`` fixtures, and ``@given`` on an anyio test.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
from collections.abc import Callable, Generator, Iterator, Mapping
from typing import Any, cast

from .decorators import fixture

__all__ = ["FIXTURES", "available", "fixture_generator", "run_test"]

_runner: Any = None
_stack: contextlib.ExitStack | None = None
_leases = 0


def available() -> bool:
    """Whether the installed anyio has the runner API this module drives."""
    try:
        return hasattr(_eventloop(), "get_async_backend")
    except Exception:  # noqa: BLE001 - no anyio, or one laid out differently: pytest's then
        return False


def _eventloop() -> Any:
    # anyio is not a dependency of rustest: imported by name, only when its plugin is loaded.
    return importlib.import_module("anyio._core._eventloop")


def _backend_and_options(backend: object) -> tuple[str, dict[str, Any]]:
    """``extract_backend_and_options``."""
    if isinstance(backend, str):
        return backend, {}
    if isinstance(backend, tuple):
        pair = cast("tuple[object, ...]", backend)
        if len(pair) == 2 and isinstance(pair[0], str) and isinstance(pair[1], dict):
            return pair[0], cast("dict[str, Any]", pair[1])
    raise TypeError("anyio_backend must be either a string or tuple of (string, dict)")


@contextlib.contextmanager
def _get_runner(backend: object) -> Iterator[Any]:
    """``get_runner``: one runner for as long as anything holds a lease on it, so a test and
    the async fixtures still alive around it share an event loop."""
    global _runner, _stack, _leases
    name, options = _backend_and_options(backend)
    if _runner is None:
        eventloop = _eventloop()
        stack = contextlib.ExitStack()
        current = getattr(eventloop, "current_async_library", None)
        if current is not None:  # anyio >= 4.12; before it the backend sets sniffio's itself
            if current() is None:
                token = eventloop.set_current_async_library(name)
                _ = stack.callback(eventloop.reset_current_async_library, token)
        _runner = stack.enter_context(
            eventloop.get_async_backend(name).create_test_runner(options or {})
        )
        _stack = stack
    _leases += 1
    try:
        yield _runner
    finally:
        _leases -= 1
        if not _leases:
            stack_, _stack, _runner = _stack, None, None
            if stack_ is not None:
                stack_.close()


def fixture_generator(
    func: Callable[..., Any], kwargs: Mapping[str, object], backend: object
) -> Generator[object]:
    """``pytest_fixture_setup``'s wrapper: an async fixture as a plain generator fixture, whose
    lease on the runner lasts until the fixture is torn down."""
    with _get_runner(backend) as runner:
        if runner.is_running():
            raise RuntimeError(
                "Cannot schedule a coroutine in the test runner while another is already "
                + "running; likely caused by request.getfixturevalue() on an async fixture."
            )
        if inspect.isasyncgenfunction(func):
            yield from runner.run_asyncgen_fixture(func, dict(kwargs))
        else:
            yield runner.run_fixture(func, dict(kwargs))


def run_test(func: Callable[..., Any], kwargs: Mapping[str, object], backend: object) -> None:
    """``pytest_pyfunc_call`` for a coroutine test."""
    with _get_runner(backend) as runner:
        try:
            runner.run_test(func, dict(kwargs))
        except BaseExceptionGroup as group:
            for exc in _leaves(group):
                if isinstance(exc, (KeyboardInterrupt, SystemExit)) or (
                    type(exc).__name__ == "Exit" and type(exc).__module__.endswith("outcomes")
                ):
                    raise exc from group
            raise


def _leaves(group: BaseExceptionGroup[BaseException]) -> Iterator[BaseException]:
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from _leaves(cast("BaseExceptionGroup[BaseException]", exc))
        else:
            yield exc


def _backends() -> list[str]:
    try:
        return list(importlib.import_module("anyio").get_available_backends())
    except Exception:  # noqa: BLE001 - the fixtures are registered only when `available()`
        return ["asyncio"]


@fixture(scope="module", params=_backends())
def anyio_backend(request: Any) -> Any:
    return request.param


@fixture
def anyio_backend_name(anyio_backend: Any) -> str:
    return anyio_backend if isinstance(anyio_backend, str) else anyio_backend[0]


@fixture
def anyio_backend_options(anyio_backend: Any) -> dict[str, Any]:
    return {} if isinstance(anyio_backend, str) else anyio_backend[1]


FIXTURES = ("anyio_backend", "anyio_backend_name", "anyio_backend_options")
