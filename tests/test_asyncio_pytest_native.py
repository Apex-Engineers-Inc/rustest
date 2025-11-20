"""Pytest-native async tests for running with actual pytest.

This file uses real pytest imports so it can be run with pytest directly,
while also serving as validation that rustest's async support is compatible.
"""

import asyncio
import pytest


async def async_add(a, b):
    """Helper async function."""
    await asyncio.sleep(0.001)
    return a + b


# Basic async tests
@pytest.mark.asyncio
async def test_basic_async():
    """Test basic async function execution."""
    await asyncio.sleep(0.001)
    assert True


@pytest.mark.asyncio
async def test_async_with_assertion():
    """Test async function with assertion."""
    result = await async_add(1, 2)
    assert result == 3


@pytest.mark.asyncio
async def test_async_with_multiple_awaits():
    """Test async function with multiple await calls."""
    result1 = await async_add(1, 2)
    result2 = await async_add(3, 4)
    result3 = await async_add(result1, result2)
    assert result3 == 10


# Async fixtures - using sync fixture for pytest compatibility
@pytest.fixture
def async_value():
    """Fixture that returns a value for async tests."""
    return 42


@pytest.mark.asyncio
async def test_async_with_fixture(async_value):
    """Test async function with regular fixture."""
    assert async_value == 42
    await asyncio.sleep(0.001)


# Async parametrized tests
@pytest.mark.asyncio
@pytest.mark.parametrize("a,b,expected", [
    (1, 2, 3),
    (5, 5, 10),
    (10, 20, 30),
])
async def test_async_parametrized(a, b, expected):
    """Test async function with parametrization."""
    result = await async_add(a, b)
    assert result == expected


# Exception handling
@pytest.mark.asyncio
async def test_async_exception():
    """Test async function that raises exception."""
    async def failing_func():
        await asyncio.sleep(0.001)
        raise ValueError("Test error")

    with pytest.raises(ValueError, match="Test error"):
        await failing_func()


@pytest.mark.asyncio
async def test_async_zero_division():
    """Test async function with ZeroDivisionError."""
    async def divide_async(a, b):
        await asyncio.sleep(0.001)
        return a / b

    with pytest.raises(ZeroDivisionError):
        await divide_async(10, 0)


# Async assertions
@pytest.mark.asyncio
async def test_async_assertion_failure():
    """Test async assertion failure for debugging output."""
    result = await async_add(2, 3)
    assert result == 5


# Advanced async patterns
@pytest.mark.asyncio
async def test_async_gather():
    """Test async gather pattern."""
    results = await asyncio.gather(
        async_add(1, 2),
        async_add(3, 4),
        async_add(5, 6),
    )
    assert results == [3, 7, 11]


@pytest.mark.asyncio
async def test_async_create_task():
    """Test async create_task pattern."""
    task1 = asyncio.create_task(async_add(1, 2))
    task2 = asyncio.create_task(async_add(3, 4))

    result1 = await task1
    result2 = await task2

    assert result1 == 3
    assert result2 == 7


# Async context managers
class AsyncContextManager:
    """Test async context manager."""

    async def __aenter__(self):
        await asyncio.sleep(0.001)
        return "context_value"

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await asyncio.sleep(0.001)
        return False


@pytest.mark.asyncio
async def test_async_context_manager():
    """Test async context manager."""
    async with AsyncContextManager() as value:
        assert value == "context_value"


# Async generators
async def async_range(n):
    """Async generator."""
    for i in range(n):
        await asyncio.sleep(0.001)
        yield i


@pytest.mark.asyncio
async def test_async_generator():
    """Test async generator."""
    result = []
    async for i in async_range(5):
        result.append(i)
    assert result == [0, 1, 2, 3, 4]


# Nested async calls
async def outer_async():
    """Outer async function."""
    result = await inner_async()
    return result * 2


async def inner_async():
    """Inner async function."""
    await asyncio.sleep(0.001)
    return 21


@pytest.mark.asyncio
async def test_nested_async_calls():
    """Test nested async function calls."""
    result = await outer_async()
    assert result == 42


# Async with timeout
@pytest.mark.asyncio
async def test_async_with_timeout():
    """Test async with timeout."""
    async def quick_operation():
        await asyncio.sleep(0.001)
        return "done"

    result = await asyncio.wait_for(quick_operation(), timeout=1.0)
    assert result == "done"


# Test classes with async methods
class TestAsyncClass:
    """Test class with async methods."""

    @pytest.mark.asyncio
    async def test_async_method_one(self):
        """Test async method in class."""
        result = await async_add(10, 20)
        assert result == 30

    @pytest.mark.asyncio
    async def test_async_method_two(self):
        """Test another async method in class."""
        result = await async_add(5, 15)
        assert result == 20


class TestMixedClass:
    """Test class with both sync and async methods."""

    def test_sync_method(self):
        """Test sync method in mixed class."""
        assert 1 + 1 == 2

    @pytest.mark.asyncio
    async def test_async_method(self):
        """Test async method in mixed class."""
        result = await async_add(7, 8)
        assert result == 15
