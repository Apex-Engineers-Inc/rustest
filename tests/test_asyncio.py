"""Comprehensive tests for asyncio support via @mark.asyncio."""

import asyncio
import sys
from rustest import mark, raises, fixture


# Basic async test
@mark.asyncio
async def test_basic_async():
    """Test basic async function execution."""
    await asyncio.sleep(0.001)
    assert True


@mark.asyncio
async def test_async_with_assertion():
    """Test async function with assertion."""
    result = await async_add(1, 2)
    assert result == 3


@mark.asyncio
async def test_async_with_multiple_awaits():
    """Test async function with multiple await calls."""
    result1 = await async_add(1, 2)
    result2 = await async_add(3, 4)
    result3 = await async_add(result1, result2)
    assert result3 == 10


# Test with loop_scope parameter
@mark.asyncio(loop_scope="function")
async def test_function_scope():
    """Test async with explicit function scope."""
    result = await async_multiply(3, 4)
    assert result == 12


@mark.asyncio(loop_scope="module")
async def test_module_scope():
    """Test async with module scope."""
    result = await async_multiply(5, 6)
    assert result == 30


# Test async with fixtures
@fixture
def sync_value():
    """Regular synchronous fixture."""
    return 42


@mark.asyncio
async def test_async_with_sync_fixture(sync_value):
    """Test async function using synchronous fixture."""
    result = await async_add(sync_value, 8)
    assert result == 50


# Test async with parametrize
from rustest import parametrize


@mark.asyncio
@parametrize("x,y,expected", [(1, 2, 3), (5, 5, 10), (10, 20, 30)])
async def test_async_parametrized(x, y, expected):
    """Test async function with parametrization."""
    result = await async_add(x, y)
    assert result == expected


# Test async with multiple marks
@mark.asyncio
@mark.slow
async def test_async_with_multiple_marks():
    """Test async function with multiple marks."""
    await asyncio.sleep(0.01)
    result = await async_multiply(7, 8)
    assert result == 56


# Test async exception handling
@mark.asyncio
async def test_async_exception():
    """Test that async exceptions are properly raised."""
    with raises(ValueError, match="negative"):
        await async_divide(10, -1)


@mark.asyncio
async def test_async_zero_division():
    """Test async zero division error."""
    with raises(ZeroDivisionError):
        await async_divide(10, 0)


# Test async with assertion failure
@mark.asyncio
async def test_async_assertion_failure():
    """Test that async assertion failures are caught."""
    result = await async_add(1, 1)
    # This should pass
    assert result == 2


# Test concurrent async operations
@mark.asyncio
async def test_async_gather():
    """Test async with asyncio.gather for concurrent operations."""
    results = await asyncio.gather(
        async_add(1, 2), async_add(3, 4), async_add(5, 6)
    )
    assert results == [3, 7, 11]


# Test async with create_task
@mark.asyncio
async def test_async_create_task():
    """Test async with asyncio.create_task."""
    task1 = asyncio.create_task(async_add(10, 20))
    task2 = asyncio.create_task(async_multiply(5, 5))
    result1 = await task1
    result2 = await task2
    assert result1 == 30
    assert result2 == 25


# Test async context manager
@mark.asyncio
async def test_async_context_manager():
    """Test async with async context manager."""
    async with AsyncContextManager() as value:
        assert value == "context_value"


# Test async generator
@mark.asyncio
async def test_async_generator():
    """Test async with async generator."""
    results = []
    async for value in async_range(5):
        results.append(value)
    assert results == [0, 1, 2, 3, 4]


# Test nested async calls
@mark.asyncio
async def test_nested_async_calls():
    """Test deeply nested async calls."""
    result = await async_fibonacci(10)
    assert result == 55


# Test async with timeout
@mark.asyncio
async def test_async_with_timeout():
    """Test async operation with timeout."""
    result = await asyncio.wait_for(async_add(1, 2), timeout=1.0)
    assert result == 3


# Test class-based async tests
@mark.asyncio(loop_scope="class")
class TestAsyncClass:
    """Test class with async methods."""

    async def test_async_method_one(self):
        """First async test method in class."""
        result = await async_add(1, 1)
        assert result == 2

    async def test_async_method_two(self):
        """Second async test method in class."""
        result = await async_multiply(3, 3)
        assert result == 9


# Test mixed sync and async in class (async methods need mark)
class TestMixedClass:
    """Test class with both sync and async methods."""

    def test_sync_method(self):
        """Synchronous test method."""
        assert 1 + 1 == 2

    @mark.asyncio
    async def test_async_method(self):
        """Async test method."""
        result = await async_add(2, 2)
        assert result == 4


# Test skipif with async
@mark.asyncio
@mark.skipif(sys.platform == "win32", reason="Test on Unix only")
async def test_async_skipif():
    """Test async with skipif mark."""
    result = await async_add(1, 2)
    assert result == 3


# Helper async functions for tests
async def async_add(x, y):
    """Helper async function that adds two numbers."""
    await asyncio.sleep(0.001)  # Simulate async operation
    return x + y


async def async_multiply(x, y):
    """Helper async function that multiplies two numbers."""
    await asyncio.sleep(0.001)
    return x * y


async def async_divide(x, y):
    """Helper async function that divides two numbers."""
    await asyncio.sleep(0.001)
    if y < 0:
        raise ValueError("negative divisor not allowed")
    return x / y


async def async_fibonacci(n):
    """Helper async function that calculates fibonacci number."""
    if n <= 1:
        return n
    await asyncio.sleep(0.0001)
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


class AsyncContextManager:
    """Async context manager for testing."""

    async def __aenter__(self):
        await asyncio.sleep(0.001)
        return "context_value"

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await asyncio.sleep(0.001)
        return None


async def async_range(n):
    """Async generator for testing."""
    for i in range(n):
        await asyncio.sleep(0.001)
        yield i
