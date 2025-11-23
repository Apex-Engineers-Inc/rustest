"""Test async fixture support in pytest-compat mode."""

import pytest


@pytest.fixture
async def async_value():
    """Simple async fixture that returns a value."""
    return 42


@pytest.fixture
async def async_generator_fixture():
    """Async generator fixture with setup and teardown."""
    # Setup
    value = {"initialized": True, "count": 0}
    yield value
    # Teardown
    value["count"] += 1


@pytest.fixture(scope="session")
async def async_session_fixture():
    """Session-scoped async fixture."""
    return "session_data"


async def test_async_fixture_basic(async_value):
    """Test that async fixtures are properly awaited."""
    assert async_value == 42


async def test_async_generator_fixture(async_generator_fixture):
    """Test that async generator fixtures work."""
    assert async_generator_fixture["initialized"] is True
    async_generator_fixture["count"] = 5


async def test_async_session_fixture(async_session_fixture):
    """Test session-scoped async fixtures."""
    assert async_session_fixture == "session_data"


async def test_multiple_async_fixtures(async_value, async_generator_fixture, async_session_fixture):
    """Test multiple async fixtures together."""
    assert async_value == 42
    assert async_generator_fixture["initialized"] is True
    assert async_session_fixture == "session_data"
