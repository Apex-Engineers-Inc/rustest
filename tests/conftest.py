"""Pytest compatibility for tests using rustest decorators.

This allows tests written with 'from rustest import parametrize, fixture'
to run under pytest by redirecting to pytest's native decorators.
"""

import sys

# Only activate when pytest is actually running (not just installed)
# Detection: check if _pytest is already loaded (pytest loads it early during startup)
# This is more reliable than PYTEST_CURRENT_TEST which is only set during test execution
# Also skip if we're running inside rustest's code block execution
if "_pytest" in sys.modules and "rustest" not in sys.modules:
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

        def _fixture(func=None, *, scope="function", autouse=False):
            """Redirect to pytest.fixture."""
            if func is None:
                # Called with arguments: @fixture(scope="module", autouse=True)
                return lambda f: pytest.fixture(f, scope=scope, autouse=autouse)
            # Called without arguments: @fixture
            return pytest.fixture(func, scope=scope, autouse=autouse)

        def _parametrize(argnames, argvalues, *, ids=None):
            """Redirect to pytest.mark.parametrize."""
            return pytest.mark.parametrize(argnames, argvalues, ids=ids)

        def _skip(reason=None):
            """Redirect to pytest.mark.skip."""
            return pytest.mark.skip(reason=reason or "skipped via rustest.skip")

        # Import approx and raises from rustest.approx and rustest.decorators
        # These need to come from the real rustest package
        try:
            # Try to import from installed rustest package
            import importlib.util

            # Find the real rustest module (not this shim)
            spec = importlib.util.find_spec("rustest")
            if spec and spec.origin and "tests/conftest.py" not in str(spec.origin):
                # Load the real module temporarily to get approx and raises
                real_rustest = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(real_rustest)
                compat_module.approx = real_rustest.approx
                compat_module.raises = real_rustest.raises
            else:
                # No real rustest found, use fallback
                raise ImportError("Real rustest module not found")
        except Exception:
            # Fallback: define minimal versions
            from math import isclose

            class _Approx:
                def __init__(self, expected, *, rel_tol=1e-6, abs_tol=1e-12):
                    self.expected = expected
                    self.rel_tol = rel_tol
                    self.abs_tol = abs_tol

                def __eq__(self, actual):
                    return isclose(
                        actual, self.expected, rel_tol=self.rel_tol, abs_tol=self.abs_tol
                    )

            compat_module.approx = _Approx
            compat_module.raises = pytest.raises

        compat_module.fixture = _fixture
        compat_module.parametrize = _parametrize
        compat_module.skip = _skip
        compat_module.mark = pytest.mark

        # Inject rustest compatibility shim immediately at import time
        # This MUST happen before subdirectory conftest files are loaded
        # (pytest loads conftest files before calling pytest_configure)
        sys.modules["rustest"] = compat_module
