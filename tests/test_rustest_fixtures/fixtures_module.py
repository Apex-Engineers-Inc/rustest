"""
Fixtures module to be loaded via rustest_fixtures.

This demonstrates the preferred rustest-native approach for loading
fixtures from external modules.
"""

import pytest


@pytest.fixture
def rustest_native_fixture():
    """Fixture loaded via rustest_fixtures field."""
    return "rustest_native_value"


@pytest.fixture
def database_fixture():
    """Example database fixture."""
    return {"connected": True, "name": "test_db"}


@pytest.fixture(scope="module")
def shared_resource():
    """Module-scoped fixture."""
    return "shared_value"
