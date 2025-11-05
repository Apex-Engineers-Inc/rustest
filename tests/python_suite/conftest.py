"""Pytest compatibility for tests using rustest decorators.

This allows tests written with 'from rustest import parametrize, fixture'
to run under pytest by redirecting to pytest's native decorators.
"""

import pytest
import sys


class RustestCompatModule:
    """Mock rustest module that exports pytest-compatible decorators."""

    @staticmethod
    def fixture(func):
        """Redirect to pytest.fixture."""
        return pytest.fixture(func)

    @staticmethod
    def parametrize(argnames, argvalues, *, ids=None):
        """Redirect to pytest.mark.parametrize."""
        return pytest.mark.parametrize(argnames, argvalues, ids=ids)

    @staticmethod
    def skip(reason=None):
        """Redirect to pytest.mark.skip."""
        return pytest.mark.skip(reason=reason or "skipped via rustest.skip")


def pytest_configure(config):
    """Inject rustest compatibility shim when pytest starts."""
    # Replace rustest module with our compatibility shim for this test session
    sys.modules['rustest'] = RustestCompatModule()
