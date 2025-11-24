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


def resolve_fixture(name: str, _executed_fixtures: dict[str, Any] | None = None) -> Any:
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
        raise NotImplementedError(
            f"Fixture '{name}' is async. request.getfixturevalue() currently only supports sync fixtures."
        )

    # Get fixture parameters
    sig = inspect.signature(fixture_func)
    params = sig.parameters

    # Resolve dependencies recursively
    resolved_args = {}
    for param_name in params:
        if param_name in _fixture_registry:
            # This parameter is itself a fixture - resolve it recursively
            resolved_args[param_name] = resolve_fixture(param_name, _executed_fixtures)
        elif param_name == "request":
            # Skip 'request' parameter - will be handled by caller
            # For now, we pass None
            resolved_args[param_name] = None

    # Execute the fixture
    result = fixture_func(**resolved_args)

    # Handle generator fixtures
    if inspect.isgenerator(result):
        result = next(result)
        # TODO: Store generator for teardown

    # Cache the result
    _executed_fixtures[name] = result

    return result
