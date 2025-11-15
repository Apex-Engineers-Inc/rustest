"""Test async functionality in pytest compatibility mode."""
import pytest
import asyncio


@pytest.mark.asyncio
async def test_simple_async():
    """Simple async test."""
    await asyncio.sleep(0.001)
    result = 42
    assert result == 42


@pytest.mark.asyncio
async def test_async_with_fixture(sample_async_data):
    """Async test using an async fixture."""
    data = await sample_async_data
    assert data["status"] == "ready"


@pytest.fixture
async def sample_async_data():
    """An async fixture."""
    await asyncio.sleep(0.001)
    return {"status": "ready", "value": 100}


@pytest.mark.asyncio
async def test_async_exception():
    """Test that async exception handling works."""
    async def failing_function():
        raise ValueError("async error")

    with pytest.raises(ValueError, match="async error"):
        await failing_function()


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [0.001, 0.002, 0.003])
async def test_async_parametrized(delay):
    """Parametrized async test."""
    start = asyncio.get_event_loop().time()
    await asyncio.sleep(delay)
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= delay * 0.9  # Allow some variance


class TestAsyncClass:
    """Test class with async methods."""

    @pytest.mark.asyncio
    async def test_async_in_class(self):
        """Async test in a class."""
        await asyncio.sleep(0.001)
        assert True

    @pytest.mark.asyncio
    async def test_another_async_in_class(self):
        """Another async test in the same class."""
        result = await self.async_helper()
        assert result == "done"

    async def async_helper(self):
        """Helper async method."""
        await asyncio.sleep(0.001)
        return "done"
