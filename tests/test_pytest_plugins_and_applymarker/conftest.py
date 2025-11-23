"""
Conftest file that loads fixtures via pytest_plugins.

This demonstrates Issue #1: pytest_plugins fixture loading.
"""

import pytest

# Load fixtures from external module
pytest_plugins = "fixtures_module"


@pytest.fixture(scope="session")
async def session_resource():
    """Session-scoped async fixture with no cleanup (Issue #3)."""
    yield "session_value"
    # No cleanup code after yield (intentional - Issue #3)


@pytest.fixture
def local_conftest_fixture():
    """Fixture defined in conftest.py itself."""
    return "conftest_value"
