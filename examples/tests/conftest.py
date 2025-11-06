"""Pytest compatibility for rustest example tests.

This allows example tests to run under both pytest and rustest.
"""

import os
import sys

# Only set up pytest compatibility when running under pytest
if os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
    try:
        import pytest

        class RustestCompatModule:
            """Mock rustest module that exports pytest-compatible decorators."""

            @staticmethod
            def fixture(func):
                return pytest.fixture(func)

            @staticmethod
            def parametrize(argnames, argvalues, *, ids=None):
                return pytest.mark.parametrize(argnames, argvalues, ids=ids)

            @staticmethod
            def skip(reason=None):
                return pytest.mark.skip(reason=reason or "skipped via rustest.skip")

        def pytest_configure(config):
            """Inject rustest compatibility shim when pytest starts."""
            sys.modules["rustest"] = RustestCompatModule()
    except ImportError:
        pass  # pytest not available, skip compatibility layer
