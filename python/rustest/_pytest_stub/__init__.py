"""
Minimal _pytest stub for rustest pytest-compat mode.

WARNING: This is NOT a full pytest implementation.
_pytest is pytest's internal API and is not officially supported by rustest.

This stub provides minimal compatibility for common imports to help projects
transition to rustest. You should migrate away from _pytest imports to either:
1. Use rustest's native API (preferred)
2. Use pytest's public API (pytest.fixture, pytest.mark, etc.)

For details on migration, see: https://github.com/anthropics/rustest
"""

import warnings

# Import submodules so they're available as attributes
from . import monkeypatch, config, outcomes, nodes, mark, assertion

# Show deprecation warning when _pytest is imported
warnings.warn(
    "_pytest is pytest's internal API and is not fully supported by rustest. "
    "Please migrate to rustest's public API or pytest's public API. "
    "See https://github.com/anthropics/rustest for migration guide.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["monkeypatch", "config", "outcomes", "nodes", "mark", "assertion"]
