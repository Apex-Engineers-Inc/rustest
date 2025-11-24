"""
Test fixtures module to be loaded via pytest_plugins.

This module defines fixtures that should be available to all tests
when loaded via pytest_plugins in conftest.py.
"""

import pytest


@pytest.fixture
def external_fixture():
    """Simple fixture from external module."""
    return "external_value"


@pytest.fixture
def number_fixture():
    """Fixture returning a number."""
    return 42


@pytest.fixture
async def async_external_fixture():
    """Async fixture from external module."""
    return "async_external_value"


@pytest.fixture(scope="module")
def module_scoped_fixture():
    """Module-scoped fixture from external module."""
    return "module_scoped_value"
