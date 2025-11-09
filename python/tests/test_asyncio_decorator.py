"""Unit tests for @mark.asyncio decorator."""

import asyncio
import pytest
from rustest import mark


def test_asyncio_mark_basic_usage():
    """Test that @mark.asyncio correctly marks async functions."""

    @mark.asyncio
    async def test_func():
        await asyncio.sleep(0.001)
        return 42

    # Check that the mark was applied
    assert hasattr(test_func, "__rustest_marks__")
    marks = test_func.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "asyncio"
    assert marks[0]["kwargs"]["loop_scope"] == "function"


def test_asyncio_mark_with_loop_scope():
    """Test @mark.asyncio with custom loop_scope."""

    @mark.asyncio(loop_scope="module")
    async def test_func():
        await asyncio.sleep(0.001)

    marks = test_func.__rustest_marks__
    assert marks[0]["kwargs"]["loop_scope"] == "module"


def test_asyncio_mark_invalid_scope():
    """Test that invalid loop_scope raises ValueError."""

    with pytest.raises(ValueError, match="Invalid loop_scope"):

        @mark.asyncio(loop_scope="invalid")
        async def test_func():
            pass


def test_asyncio_mark_on_sync_function():
    """Test that @mark.asyncio raises TypeError on sync functions."""

    with pytest.raises(TypeError, match="can only be applied to async functions"):

        @mark.asyncio
        def test_sync_func():
            pass


def test_asyncio_mark_wraps_function():
    """Test that @mark.asyncio wraps the async function correctly."""

    @mark.asyncio
    async def test_func(x, y):
        await asyncio.sleep(0.001)
        return x + y

    # The wrapper should be callable and return the result
    result = test_func(1, 2)
    assert result == 3


def test_asyncio_mark_preserves_function_name():
    """Test that @mark.asyncio preserves the function name."""

    @mark.asyncio
    async def my_test_function():
        pass

    assert my_test_function.__name__ == "my_test_function"


def test_asyncio_mark_on_class():
    """Test that @mark.asyncio can be applied to test classes."""

    @mark.asyncio(loop_scope="class")
    class TestAsyncClass:
        async def test_method_one(self):
            await asyncio.sleep(0.001)
            return 1

        async def test_method_two(self):
            await asyncio.sleep(0.001)
            return 2

    # Check that the mark was applied to the class
    assert hasattr(TestAsyncClass, "__rustest_marks__")
    marks = TestAsyncClass.__rustest_marks__
    assert marks[0]["name"] == "asyncio"

    # Check that async methods were wrapped
    instance = TestAsyncClass()
    result1 = instance.test_method_one()
    result2 = instance.test_method_two()
    assert result1 == 1
    assert result2 == 2


def test_asyncio_mark_handles_exceptions():
    """Test that @mark.asyncio properly propagates exceptions."""

    @mark.asyncio
    async def test_func():
        await asyncio.sleep(0.001)
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        test_func()


def test_asyncio_mark_with_all_scopes():
    """Test that all valid loop_scope values are accepted."""
    scopes = ["function", "class", "module", "session"]

    for scope in scopes:

        @mark.asyncio(loop_scope=scope)
        async def test_func():
            await asyncio.sleep(0.001)

        marks = test_func.__rustest_marks__
        assert marks[0]["kwargs"]["loop_scope"] == scope


def test_asyncio_mark_executes_async_code():
    """Test that async code actually executes."""
    counter = {"value": 0}

    @mark.asyncio
    async def test_func():
        await asyncio.sleep(0.001)
        counter["value"] = 42

    test_func()
    assert counter["value"] == 42


def test_asyncio_mark_with_multiple_awaits():
    """Test that multiple awaits work correctly."""

    async def async_add(x, y):
        await asyncio.sleep(0.001)
        return x + y

    @mark.asyncio
    async def test_func():
        result1 = await async_add(1, 2)
        result2 = await async_add(3, 4)
        return result1 + result2

    result = test_func()
    assert result == 10


def test_asyncio_mark_with_gather():
    """Test that asyncio.gather works within marked functions."""

    async def async_double(x):
        await asyncio.sleep(0.001)
        return x * 2

    @mark.asyncio
    async def test_func():
        results = await asyncio.gather(async_double(1), async_double(2), async_double(3))
        return results

    result = test_func()
    assert result == [2, 4, 6]


def test_asyncio_mark_cleans_up_loop():
    """Test that event loop is properly cleaned up."""

    @mark.asyncio
    async def test_func():
        await asyncio.sleep(0.001)
        return True

    # Run multiple times to ensure cleanup works
    for _ in range(3):
        result = test_func()
        assert result is True


def test_asyncio_combined_with_other_marks():
    """Test that @mark.asyncio can be combined with other marks."""

    @mark.asyncio
    @mark.slow
    async def test_func():
        await asyncio.sleep(0.001)

    marks = test_func.__rustest_marks__
    mark_names = [m["name"] for m in marks]
    assert "asyncio" in mark_names
    assert "slow" in mark_names
