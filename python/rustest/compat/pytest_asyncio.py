"""
pytest-asyncio compatibility shim for rustest.

This module provides a compatibility layer for pytest-asyncio, translating its
decorators to rustest's native async support.

Supported:
- @pytest_asyncio.fixture() with all scopes (function/class/module/session)
- Async generator fixtures (with setup/teardown)
- All pytest_asyncio configuration options (ignored but accepted for compatibility)

Usage:
    # When using --pytest-compat mode, this automatically works:
    import pytest_asyncio

    @pytest_asyncio.fixture(scope="session")
    async def async_resource():
        yield "resource"

    # Gets translated to rustest's native async fixture support
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, ParamSpec, TypeVar, overload

# Import rustest's fixture decorator which already supports async
from rustest.compat.pytest import fixture as _pytest_fixture

__all__ = ["fixture"]

P = ParamSpec("P")
R = TypeVar("R")


@overload
def fixture(
    func: Callable[P, R],
    *,
    scope: str = "function",
    autouse: bool = False,
    name: str | None = None,
    params: Sequence[Any] | None = None,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
) -> Callable[P, R]: ...


@overload
def fixture(
    *,
    scope: str = "function",
    params: Sequence[Any] | None = None,
    autouse: bool = False,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
    name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def fixture(
    func: Callable[P, R] | None = None,
    *,
    scope: str = "function",
    params: Sequence[Any] | None = None,
    autouse: bool = False,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
    name: str | None = None,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """
    pytest-asyncio compatible fixture decorator.

    This is a compatibility shim that translates pytest_asyncio.fixture to
    rustest's native async fixture support. All async fixtures work seamlessly
    in rustest without any plugin.

    Args:
        func: The fixture function (when used without parentheses)
        scope: Fixture scope (function/class/module/session)
        params: Optional list of parameter values
        autouse: If True, fixture runs automatically
        ids: Optional IDs for parameter values
        name: Override fixture name

    Examples:
        @pytest_asyncio.fixture
        async def async_value():
            return 42

        @pytest_asyncio.fixture(scope="session")
        async def session_resource():
            yield "shared"

        @pytest_asyncio.fixture(params=[1, 2, 3])
        async def parametrized(request):
            return request.param
    """
    # Delegate directly to pytest fixture decorator
    # Rustest's fixture decorator already supports async functions
    return _pytest_fixture(func, scope=scope, params=params, autouse=autouse, ids=ids, name=name)
