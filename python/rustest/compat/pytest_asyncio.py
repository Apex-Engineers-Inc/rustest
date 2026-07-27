"""``pytest_asyncio`` for rustest — the decorator half of the plugin.

rustest *is* the async plugin: ``_v2_worker.py`` ports pytest-asyncio's loop-scope model
(``asyncio_mode``, ``asyncio_default_fixture_loop_scope``,
``asyncio_default_test_loop_scope``, ``@mark.asyncio(loop_scope=...)``) directly, so a suite
importing ``pytest_asyncio`` needs no plugin — it needs this module's *markers*, because the
worker reads exactly the two attributes pytest-asyncio's own decorator leaves behind:

* ``_force_asyncio_fixture`` — the flag ``strict`` mode turns on.  In ``strict`` mode an
  ``async def`` fixture declared with plain ``@pytest.fixture`` is **not** awaited and the
  test receives a coroutine object (`pytest_asyncio/plugin.py::pytest_fixture_setup` l.
  728-735, probed); one declared with ``@pytest_asyncio.fixture`` is.  Without this flag the
  shim would silently turn every strict-mode suite's async fixtures into coroutine objects.
* ``_loop_scope`` — ``@pytest_asyncio.fixture(loop_scope="session")``, which is the *only*
  way to give a fixture a loop scope that differs from its caching scope
  (l. 736-741: ``mark ?? asyncio_default_fixture_loop_scope ?? fixturedef.scope``).

Both are set by ``_make_asyncio_fixture_function`` (l. 191-197), reproduced below.

``rustest.compat.pytest.fixture`` does the actual registration; this only decorates what it
returns, which is the same layering as the original (``fixture`` l. 165-183 calls
``pytest.fixture`` after marking the function).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, ParamSpec, TypeVar, overload

# Import rustest's fixture decorator which already supports async
from rustest.compat.pytest import fixture as _pytest_fixture

__all__ = ["fixture"]

P = ParamSpec("P")
R = TypeVar("R")


def _make_asyncio_fixture_function(obj: object, loop_scope: str | None) -> None:
    """Port of `pytest_asyncio/plugin.py::_make_asyncio_fixture_function` (l. 191-197).

    The attributes go on the **underlying function** for a bound/static method
    (``obj.__func__``), because that is the object the worker sees when it later reads them
    off a ``FixtureDef.func`` — and it is what the oracle does, for the same reason.
    """
    obj = getattr(obj, "__func__", obj)
    obj._force_asyncio_fixture = True  # pyright: ignore[reportAttributeAccessIssue]
    obj._loop_scope = loop_scope  # pyright: ignore[reportAttributeAccessIssue]


@overload
def fixture(
    func: Callable[P, R],
    *,
    scope: str = "function",
    loop_scope: str | None = None,
    autouse: bool = False,
    name: str | None = None,
    params: Sequence[Any] | None = None,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
) -> Callable[P, R]: ...


@overload
def fixture(
    *,
    scope: str = "function",
    loop_scope: str | None = None,
    params: Sequence[Any] | None = None,
    autouse: bool = False,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
    name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def fixture(
    func: Callable[P, R] | None = None,
    *,
    scope: str = "function",
    loop_scope: str | None = None,
    params: Sequence[Any] | None = None,
    autouse: bool = False,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
    name: str | None = None,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """pytest-asyncio's fixture decorator.

    Args:
        func: The fixture function (when used without parentheses)
        scope: Fixture *caching* scope (function/class/module/package/session)
        loop_scope: The scope of the event loop the fixture's body runs on. Defaults to
            ``asyncio_default_fixture_loop_scope`` and, failing that, to ``scope`` — so a
            ``scope="module"`` fixture runs on a module-lived loop unless told otherwise.
        params: Optional list of parameter values
        autouse: If True, fixture runs automatically
        ids: Optional IDs for parameter values
        name: Override fixture name

    Examples:
        @pytest_asyncio.fixture
        async def async_value():
            return 42

        @pytest_asyncio.fixture(scope="session", loop_scope="session")
        async def session_resource():
            yield "shared"
    """
    if func is None:

        def decorator(inner: Callable[P, R]) -> Callable[P, R]:
            return fixture(
                inner,
                scope=scope,
                loop_scope=loop_scope,
                params=params,
                autouse=autouse,
                ids=ids,
                name=name,
            )

        return decorator

    # Marked BEFORE registration, so `rustest.compat.pytest.fixture` returns a function that
    # already carries both attributes however it chooses to wrap it.
    _make_asyncio_fixture_function(func, loop_scope)
    return _pytest_fixture(func, scope=scope, params=params, autouse=autouse, ids=ids, name=name)
