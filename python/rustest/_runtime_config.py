"""Runtime configuration storage for rustest.

This module provides thread-safe storage for runtime configuration
that fixtures can access during test execution.
"""

from __future__ import annotations

import threading
from typing import Any

# Thread-local storage for runtime configuration
_storage = threading.local()


def set_runtime_config(
    *,
    verbose: int = 0,
    capture: str = "fd",
    pytest_compat: bool = False,
    ascii: bool = False,
    no_color: bool = False,
    workers: int | None = None,
    fail_fast: bool = False,
) -> None:
    """Store runtime configuration for access by fixtures.

    Args:
        verbose: Verbosity level (0 = normal, 1 = verbose, 2+ = very verbose)
        capture: Output capture mode ("fd", "sys", "no")
        pytest_compat: Whether pytest compatibility mode is enabled
        ascii: Whether to use ASCII output
        no_color: Whether colors are disabled
        workers: Number of worker processes
        fail_fast: Whether to fail fast on first error
    """
    _storage.config = {
        "verbose": verbose,
        "capture": capture,
        "pytest_compat": pytest_compat,
        "ascii": ascii,
        "no_color": no_color,
        "workers": workers,
        "fail_fast": fail_fast,
        "assertmode": "rewrite",  # rustest always uses assertion rewriting
        "tb": "short",  # Default traceback style
        "strict": False,  # Not strict by default
    }


def get_runtime_config() -> dict[str, Any]:
    """Get the current runtime configuration.

    Returns:
        Dictionary of configuration options, or defaults if not set.
    """
    if not hasattr(_storage, "config"):
        # Return defaults if no config has been set
        return {
            "verbose": 0,
            "capture": "fd",
            "pytest_compat": False,
            "ascii": False,
            "no_color": False,
            "workers": None,
            "fail_fast": False,
            "assertmode": "rewrite",
            "tb": "short",
            "strict": False,
        }
    return _storage.config.copy()


def is_pytest_compat_mode() -> bool:
    """Check if pytest compatibility mode is active.

    This is more reliable than checking sys.modules paths.

    Returns:
        True if pytest-compat mode is active, False otherwise.
    """
    config = get_runtime_config()
    return config.get("pytest_compat", False)


def clear_runtime_config() -> None:
    """Clear the runtime configuration.

    This is primarily for testing purposes.
    """
    if hasattr(_storage, "config"):
        delattr(_storage, "config")
