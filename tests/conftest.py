"""Pytest compatibility for tests using rustest decorators.

This allows tests written with 'from rustest import parametrize, fixture'
to run under pytest by redirecting to pytest's native decorators.
"""

import sys

# Only activate when running under pytest
try:
    import pytest
except ImportError:
    # Not running under pytest, do nothing
    pass
else:

    class RustestCompatModule:
        """Mock rustest module that exports pytest-compatible decorators."""

        @staticmethod
        def fixture(func=None, *, scope="function"):
            """Redirect to pytest.fixture."""
            if func is None:
                # Called with arguments: @fixture(scope="module")
                return lambda f: pytest.fixture(f, scope=scope)
            # Called without arguments: @fixture
            return pytest.fixture(func, scope=scope)

        @staticmethod
        def parametrize(argnames, argvalues, *, ids=None):
            """Redirect to pytest.mark.parametrize."""
            return pytest.mark.parametrize(argnames, argvalues, ids=ids)

        @staticmethod
        def skip(reason=None):
            """Redirect to pytest.mark.skip."""
            return pytest.mark.skip(reason=reason or "skipped via rustest.skip")

        # Add mark attribute for pytest.mark compatibility
        mark = pytest.mark

    def pytest_configure(config):
        """Inject rustest compatibility shim when pytest starts."""
        # Replace rustest module with our compatibility shim for this test session
        sys.modules["rustest"] = RustestCompatModule()
