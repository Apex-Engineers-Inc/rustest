"""Global fixture registry for runtime fixture resolution.

This module provides a thread-safe global registry that stores fixture information
and enables dynamic fixture resolution via request.getfixturevalue().
"""

from __future__ import annotations

import inspect
import threading
from typing import Any

_registry_lock = threading.Lock()
_fixture_registry: dict[str, Any] = {}
_fixture_cache: dict[str, Any] = {}


def register_fixtures(fixtures: dict[str, Any]) -> None:
    """Register fixtures for the current test context.

    Args:
        fixtures: Dictionary mapping fixture names to fixture callables
    """
    with _registry_lock:
        _fixture_registry.clear()
        _fixture_registry.update(fixtures)


def clear_registry() -> None:
    """Clear the fixture registry and cache."""
    with _registry_lock:
        _fixture_registry.clear()
        _fixture_cache.clear()


def get_fixture(name: str) -> Any:
    """Get a fixture callable by name.

    Args:
        name: Name of the fixture

    Returns:
        The fixture callable

    Raises:
        ValueError: If the fixture is not found
    """
    with _registry_lock:
        if name not in _fixture_registry:
            raise ValueError(f"fixture '{name}' not found")
        return _fixture_registry[name]


def resolve_fixture(
    name: str,
    _executed_fixtures: dict[str, Any] | None = None,
    *,
    request_obj: Any | None = None,
) -> Any:
    """Resolve and execute a fixture by name.

    This handles fixture dependencies recursively and caches results per test.

    Args:
        name: Name of the fixture to resolve
        _executed_fixtures: Internal cache of already-executed fixtures for this test

    Returns:
        The fixture value

    Raises:
        ValueError: If the fixture is not found
        NotImplementedError: If the fixture is async (not yet supported)
    """
    if _executed_fixtures is None:
        _executed_fixtures = {}

    # Check if already executed for this test
    if name in _executed_fixtures:
        return _executed_fixtures[name]

    # Get the fixture callable
    fixture_func = get_fixture(name)

    # Check if it's async (either async function or async generator)
    if inspect.iscoroutinefunction(fixture_func) or inspect.isasyncgenfunction(fixture_func):
        # Raise a clear, helpful error explaining the issue and how to fix it
        raise NotImplementedError(
            f"\nCannot use async fixture '{name}' with request.getfixturevalue().\n\n"
            + "Why this fails:\n"
            + "  • getfixturevalue() is a synchronous function that returns values immediately\n"
            + "  • Async fixtures must be awaited, but we can't await in a sync context\n"
            + "  • Calling the async fixture returns a coroutine object, not the actual value\n\n"
            + "Good news: Async fixtures work perfectly with normal injection!\n\n"
            + "How to fix:\n"
            + f"  ❌ Don't use: request.getfixturevalue('{name}')\n"
            + f"  ✅ Instead use: def test_something({name}):\n\n"
            + "Example:\n"
            + "  # This works perfectly:\n"
            + f"  async def test_my_feature({name}):\n"
            + f"      assert {name} is not None\n"
        )

    # Get fixture parameters
    sig = inspect.signature(fixture_func)
    params = sig.parameters

    # Resolve dependencies recursively
    resolved_args = {}
    for param_name in params:
        # Use get_fixture() which has lock protection, instead of checking registry directly
        try:
            # Try to get the fixture (thread-safe with lock)
            get_fixture(param_name)
            # It's a fixture - resolve it recursively
            resolved_args[param_name] = resolve_fixture(
                param_name,
                _executed_fixtures,
                request_obj=request_obj,
            )
        except ValueError:
            if param_name == "request":
                resolved_args[param_name] = _resolve_request_argument(request_obj)

    # Execute the fixture
    result = fixture_func(**resolved_args)

    # Handle generator fixtures
    if inspect.isgenerator(result):
        result = next(result)
        # TODO: Store generator for teardown

    # Cache the result
    _executed_fixtures[name] = result

    return result


def _resolve_request_argument(request_obj: Any | None) -> Any:
    if request_obj is not None:
        return request_obj

    # Lazy import to avoid circular dependency during module import
    from rustest.compat.pytest import FixtureRequest

    # This mirrors pytest's behaviour where a best-effort request object is created
    # so fixtures that reference `request` can still introspect basic metadata.
    return FixtureRequest()
