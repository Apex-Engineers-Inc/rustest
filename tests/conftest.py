"""Pytest compatibility for tests using rustest decorators.

This allows tests written with 'from rustest import parametrize, fixture'
to run under pytest by redirecting to pytest's native decorators.
"""

import sys

# Only activate when pytest is actually running (not just installed)
# Detection: check if _pytest is already loaded (pytest loads it early during startup)
# This is more reliable than PYTEST_CURRENT_TEST which is only set during test execution
if "_pytest" in sys.modules:
    try:
        import pytest
    except ImportError:
        pass
    else:
        # Create a simple module-like object with fixture/parametrize/skip functions
        import types

        compat_module = types.ModuleType("rustest")
        compat_module.__file__ = __file__
        compat_module.__package__ = "rustest"

        def _fixture(func=None, *, scope="function"):
            """Redirect to pytest.fixture."""
            if func is None:
                # Called with arguments: @fixture(scope="module")
                return lambda f: pytest.fixture(f, scope=scope)
            # Called without arguments: @fixture
            return pytest.fixture(func, scope=scope)

        def _parametrize(argnames, argvalues, *, ids=None):
            """Redirect to pytest.mark.parametrize."""
            return pytest.mark.parametrize(argnames, argvalues, ids=ids)

        def _skip(reason=None):
            """Redirect to pytest.mark.skip."""
            return pytest.mark.skip(reason=reason or "skipped via rustest.skip")

        compat_module.fixture = _fixture
        compat_module.parametrize = _parametrize
        compat_module.skip = _skip
        compat_module.mark = pytest.mark

        # Inject rustest compatibility shim immediately at import time
        # This MUST happen before subdirectory conftest files are loaded
        # (pytest loads conftest files before calling pytest_configure)
        sys.modules["rustest"] = compat_module
