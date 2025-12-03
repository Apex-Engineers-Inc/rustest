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
    # When loop_scope is not specified, kwargs should be empty
    # This allows smart loop scope detection to work
    assert marks[0]["kwargs"] == {}


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
    """Test that @mark.asyncio accepts sync functions for pytest compatibility."""

    # For pytest compatibility, this should NOT raise - just apply the mark
    @mark.asyncio
    def test_sync_func():
        return "sync_result"

    # Verify the mark was applied
    marks = getattr(test_sync_func, "__rustest_marks__", [])
    asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
    assert len(asyncio_marks) >= 1

    # Verify function runs normally
    assert test_sync_func() == "sync_result"


def test_asyncio_mark_preserves_async_nature():
    """Test that @mark.asyncio preserves the async nature of functions."""

    @mark.asyncio
    async def test_func(x, y):
        await asyncio.sleep(0.001)
        return x + y

    # The decorator should NOT wrap the function - it should remain a coroutine function
    import inspect

    assert inspect.iscoroutinefunction(test_func)

    # Calling it should return a coroutine object
    result = test_func(1, 2)
    assert inspect.iscoroutine(result)

    # Clean up the coroutine to avoid warnings
    result.close()


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

    # Check that async methods have the mark too
    assert hasattr(TestAsyncClass.test_method_one, "__rustest_marks__")
    assert hasattr(TestAsyncClass.test_method_two, "__rustest_marks__")

    # Methods should still be coroutine functions
    import inspect

    assert inspect.iscoroutinefunction(TestAsyncClass.test_method_one)
    assert inspect.iscoroutinefunction(TestAsyncClass.test_method_two)


def test_asyncio_mark_does_not_execute():
    """Test that @mark.asyncio does not execute the function.

    The decorator only applies metadata. Execution is handled by rustest's
    test runner, which will use the loop_scope metadata for smart detection.
    """

    @mark.asyncio
    async def test_func():
        await asyncio.sleep(0.001)
        raise ValueError("test error")

    # Calling the function should return a coroutine, not execute it
    import inspect

    result = test_func()
    assert inspect.iscoroutine(result)

    # Clean up
    result.close()


def test_asyncio_mark_with_all_scopes():
    """Test that all valid loop_scope values are accepted."""
    scopes = ["function", "class", "module", "session"]

    for scope in scopes:

        @mark.asyncio(loop_scope=scope)
        async def test_func():
            await asyncio.sleep(0.001)

        marks = test_func.__rustest_marks__
        assert marks[0]["kwargs"]["loop_scope"] == scope


def test_asyncio_mark_returns_coroutine():
    """Test that decorated async functions return coroutines."""

    @mark.asyncio
    async def test_func():
        await asyncio.sleep(0.001)
        return 42

    # Should return a coroutine object
    import inspect

    result = test_func()
    assert inspect.iscoroutine(result)

    # Clean up
    result.close()


def test_asyncio_mark_with_parameters():
    """Test that decorated functions can accept parameters."""

    @mark.asyncio
    async def test_func(x, y, z=10):
        await asyncio.sleep(0.001)
        return x + y + z

    # Should still be a coroutine function that accepts parameters
    import inspect

    assert inspect.iscoroutinefunction(test_func)

    result = test_func(1, 2, z=3)
    assert inspect.iscoroutine(result)

    # Clean up
    result.close()


def test_asyncio_mark_idempotent():
    """Test that applying the mark multiple times doesn't break anything."""

    @mark.asyncio
    @mark.asyncio  # Apply twice
    async def test_func():
        await asyncio.sleep(0.001)
        return True

    # Should still work (though will have duplicate marks)
    import inspect

    assert inspect.iscoroutinefunction(test_func)

    result = test_func()
    assert inspect.iscoroutine(result)

    # Clean up
    result.close()


def test_asyncio_mark_default_loop_scope():
    """Test that default loop_scope is None (auto-detect)."""

    @mark.asyncio
    async def test_func():
        pass

    marks = test_func.__rustest_marks__
    # When loop_scope is not specified, kwargs should be empty
    # This allows Rust's smart loop scope detection to work
    assert "loop_scope" not in marks[0]["kwargs"]


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


# ============================================================================
# Timeout parameter tests
# ============================================================================


def test_asyncio_mark_with_timeout():
    """Test @mark.asyncio with timeout parameter."""

    @mark.asyncio(timeout=5.0)
    async def test_func():
        await asyncio.sleep(0.001)

    marks = test_func.__rustest_marks__
    assert marks[0]["kwargs"]["timeout"] == 5.0


def test_asyncio_mark_with_timeout_and_loop_scope():
    """Test @mark.asyncio with both timeout and loop_scope."""

    @mark.asyncio(loop_scope="module", timeout=10.0)
    async def test_func():
        await asyncio.sleep(0.001)

    marks = test_func.__rustest_marks__
    assert marks[0]["kwargs"]["loop_scope"] == "module"
    assert marks[0]["kwargs"]["timeout"] == 10.0


def test_asyncio_mark_timeout_integer():
    """Test @mark.asyncio accepts integer timeout."""

    @mark.asyncio(timeout=5)
    async def test_func():
        await asyncio.sleep(0.001)

    marks = test_func.__rustest_marks__
    assert marks[0]["kwargs"]["timeout"] == 5


def test_asyncio_mark_timeout_negative_raises():
    """Test that negative timeout raises ValueError."""

    with pytest.raises(ValueError, match="timeout must be positive"):

        @mark.asyncio(timeout=-1.0)
        async def test_func():
            pass


def test_asyncio_mark_timeout_zero_raises():
    """Test that zero timeout raises ValueError."""

    with pytest.raises(ValueError, match="timeout must be positive"):

        @mark.asyncio(timeout=0)
        async def test_func():
            pass


def test_asyncio_mark_timeout_invalid_type_raises():
    """Test that invalid timeout type raises TypeError."""

    with pytest.raises(TypeError, match="timeout must be a number"):

        @mark.asyncio(timeout="5")  # type: ignore[arg-type]
        async def test_func():
            pass


def test_asyncio_mark_timeout_none_allowed():
    """Test that timeout=None is allowed (default)."""

    @mark.asyncio(timeout=None)
    async def test_func():
        await asyncio.sleep(0.001)

    marks = test_func.__rustest_marks__
    # timeout=None means no timeout, should not be in kwargs
    assert "timeout" not in marks[0]["kwargs"]


def test_asyncio_mark_timeout_on_class():
    """Test @mark.asyncio with timeout on class applies to methods."""

    @mark.asyncio(loop_scope="class", timeout=5.0)
    class TestAsyncClass:
        async def test_method(self):
            await asyncio.sleep(0.001)

    # Check class has the mark
    class_marks = TestAsyncClass.__rustest_marks__
    assert class_marks[0]["kwargs"]["timeout"] == 5.0

    # Check method has the mark propagated
    method_marks = TestAsyncClass.test_method.__rustest_marks__
    # Should have at least one mark with timeout
    timeouts = [
        m["kwargs"].get("timeout") for m in method_marks if "timeout" in m.get("kwargs", {})
    ]
    assert 5.0 in timeouts


def test_asyncio_mark_small_timeout():
    """Test @mark.asyncio with very small timeout value."""

    @mark.asyncio(timeout=0.001)
    async def test_func():
        await asyncio.sleep(0.0001)

    marks = test_func.__rustest_marks__
    assert marks[0]["kwargs"]["timeout"] == 0.001
