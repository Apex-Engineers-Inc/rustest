# Async Testing

Rustest provides built-in support for testing asynchronous code using the `@mark.asyncio` decorator. This feature is inspired by pytest-asyncio but includes **built-in timeout support** that pytest-asyncio lacks out of the box.

## What is Async? (For Beginners)

If you're new to async programming, here's a simple explanation:

**Regular (synchronous) code** runs one thing at a time. If you're waiting for a slow operation (like downloading a file), your program just sits there waiting.

**Asynchronous code** can start a slow operation and then do other things while waiting. It's like ordering food at a restaurant - you don't stand at the counter waiting; you sit down and do other things until your food is ready.

In Python, async functions use `async def` and you `await` operations that might take time:

```python
# Regular function - blocks while sleeping
def slow_sync():
    time.sleep(1)  # Program freezes for 1 second
    return "done"

# Async function - doesn't block the whole program
async def slow_async():
    await asyncio.sleep(1)  # Other code can run during this wait
    return "done"
```

The key insight: **async lets your tests run faster** because slow operations can overlap instead of running one after another.

## Quick Start

To test async functions, simply decorate them with `@mark.asyncio`:

```python
from rustest import mark

@mark.asyncio
async def test_async_function():
    """Test an async function."""
    result = await some_async_operation()
    assert result == expected_value
```

## Basic Usage

### Simple Async Test

```python
import asyncio
from rustest import mark

@mark.asyncio
async def test_basic_async():
    """Test basic async operation."""
    await asyncio.sleep(0.1)
    assert True
```

### Async Test with Assertions

```python
from rustest import mark

async def fetch_user(user_id: int) -> dict:
    """Simulate async API call."""
    await asyncio.sleep(0.1)
    return {"id": user_id, "name": "Alice"}

@mark.asyncio
async def test_fetch_user():
    """Test async API call."""
    user = await fetch_user(123)
    assert user["id"] == 123
    assert user["name"] == "Alice"
```

### Multiple Await Statements

```python
from rustest import mark

@mark.asyncio
async def test_multiple_operations():
    """Test multiple async operations."""
    result1 = await async_add(1, 2)
    result2 = await async_multiply(result1, 3)
    assert result2 == 9
```

## Loop Scopes

The `loop_scope` parameter controls the lifetime of the event loop used for your async tests. This mirrors pytest-asyncio's behavior.

### Function Scope (Default)

Each test gets its own fresh event loop:

```python
import asyncio
from rustest import mark

@mark.asyncio  # Same as @mark.asyncio(loop_scope="function")
async def test_with_function_loop():
    """Each test gets a fresh event loop."""
    await asyncio.sleep(0.1)
```

### Module Scope

All tests in the module share the same event loop:

```python
import asyncio
from rustest import mark

@mark.asyncio(loop_scope="module")
async def test_one():
    """Shares loop with other module-scoped tests."""
    await asyncio.sleep(0.1)

@mark.asyncio(loop_scope="module")
async def test_two():
    """Shares the same loop as test_one."""
    await asyncio.sleep(0.1)
```

### Class Scope

All async methods in a class share the same event loop:

```python
import asyncio
from rustest import mark

class MockAPI:
    async def get_user(self, id: int):
        return {"id": id, "name": "User"}
    async def create_user(self, data: dict):
        return data

api = MockAPI()

@mark.asyncio(loop_scope="class")
class TestAsyncAPI:
    """All async methods share the same event loop."""

    async def test_get_user(self):
        user = await api.get_user(1)
        assert user is not None

    async def test_create_user(self):
        user = await api.create_user({"name": "Bob"})
        assert user["name"] == "Bob"
```

### Session Scope

All tests in the entire test session share one event loop:

```python
import asyncio
from rustest import mark

async def setup_database():
    pass

@mark.asyncio(loop_scope="session")
async def test_session_scoped():
    """Shares loop with all other session-scoped tests."""
    await setup_database()
```

## Built-in Timeout Support

One of rustest's key advantages over pytest-asyncio is **built-in per-test timeout support**. With pytest-asyncio, you need additional plugins or manual `asyncio.wait_for()` calls. With rustest, it's built right in.

### Basic Timeout

Add a timeout to any async test with the `timeout` parameter:

```python
from rustest import mark

@mark.asyncio(timeout=5.0)
async def test_api_call():
    """This test will fail if it takes longer than 5 seconds."""
    result = await slow_api_call()
    assert result["status"] == "ok"
```

If the test exceeds the timeout, it automatically fails with a clear message:

```
Test timed out after 5.0 seconds
```

### Why Built-in Timeouts Matter

Timeouts are **essential** for async tests because:

1. **Prevent hanging tests**: A bug in your async code might cause it to wait forever. Without a timeout, your entire test suite hangs.

2. **Catch performance regressions**: If an operation that should take 100ms suddenly takes 10 seconds, you want to know.

3. **CI/CD reliability**: Tests that hang can block your entire deployment pipeline.

### Timeout with Loop Scope

Combine timeout with loop scopes for maximum control:

```python
from rustest import mark

@mark.asyncio(loop_scope="module", timeout=10.0)
async def test_database_query():
    """Shares event loop with other module tests, fails after 10s."""
    results = await db.query("SELECT * FROM large_table")
    assert len(results) > 0
```

### Class-Level Timeout

Apply timeout to all methods in a test class:

```python
from rustest import mark

@mark.asyncio(loop_scope="class", timeout=30.0)
class TestSlowOperations:
    """All methods have a 30 second timeout."""

    async def test_operation_one(self):
        await slow_operation()
        assert True

    async def test_operation_two(self):
        await another_slow_operation()
        assert True
```

### Per-Test Timeout Override

When using class decoration, you can override the timeout for specific methods:

```python
from rustest import mark

@mark.asyncio(loop_scope="class", timeout=5.0)
class TestMixedTimeouts:
    """Default 5 second timeout for all methods."""

    async def test_fast_operation(self):
        """Uses the class default of 5 seconds."""
        await fast_operation()

    @mark.asyncio(timeout=60.0)
    async def test_very_slow_operation(self):
        """Override: this test gets 60 seconds."""
        await very_slow_operation()
```

### Timeout Gotchas

!!! warning "Common Timeout Pitfalls"
    Be aware of these common issues when using timeouts.

**1. Timeout only applies to async tests**

If you accidentally apply `timeout` to a synchronous function, it will be silently ignored:

<!--rustest.mark.skip-->
```python
@mark.asyncio(timeout=5.0)
def test_sync():  # NOT async! Timeout is ignored.
    time.sleep(10)  # This will NOT timeout after 5 seconds
```

**2. Timeout must be positive**

Passing zero or negative values raises a `ValueError` at decoration time:

- `@mark.asyncio(timeout=0)` → `ValueError: timeout must be positive`
- `@mark.asyncio(timeout=-1.0)` → `ValueError: timeout must be positive`

**3. Timeouts are per-test, not shared**

Each test has its own independent timeout. If test A times out after 5 seconds, test B (running in parallel) is not affected:

<!--rustest.mark.skip-->
```python
@mark.asyncio(loop_scope="module", timeout=0.1)
async def test_will_timeout():
    await asyncio.sleep(10)  # Times out after 0.1s

@mark.asyncio(loop_scope="module", timeout=60.0)
async def test_will_complete():
    await asyncio.sleep(1)  # Completes normally, not affected by test_will_timeout
```

### Comparison: rustest vs pytest-asyncio

| Feature | rustest | pytest-asyncio |
|---------|---------|----------------|
| **Parallel async test execution** | ✅ Tests run concurrently | ❌ Sequential only |
| **Built-in timeout parameter** | ✅ `@mark.asyncio(timeout=5.0)` | ❌ Not available |
| Per-test timeout | ✅ Built-in | ❌ Requires pytest-timeout plugin |
| Clean timeout message | ✅ "Test timed out after X seconds" | ❌ Raw asyncio.TimeoutError |
| Independent test timeouts | ✅ Each test has own timeout | ⚠️ Depends on plugin |

**Why this matters for performance**: With pytest-asyncio, if you have 10 async tests that each `await asyncio.sleep(1)`, they run sequentially taking ~10 seconds. With rustest, they run in parallel and complete in ~1 second.

With pytest-asyncio, you'd need to write:

```python
# pytest-asyncio: Manual timeout handling
import pytest
import asyncio

@pytest.mark.asyncio
async def test_with_timeout():
    result = await asyncio.wait_for(
        slow_operation(),
        timeout=5.0
    )
    assert result is not None
```

With rustest, it's just:

```python
# rustest: Built-in timeout
from rustest import mark

@mark.asyncio(timeout=5.0)
async def test_with_timeout():
    result = await slow_operation()
    assert result is not None
```

## Advanced Patterns

### Concurrent Operations with gather

```python
from rustest import mark
import asyncio

async def fetch_user(user_id: int):
    await asyncio.sleep(0.001)
    return {"id": user_id, "name": f"User{user_id}"}

@mark.asyncio
async def test_concurrent_operations():
    """Test multiple concurrent async operations."""
    results = await asyncio.gather(
        fetch_user(1),
        fetch_user(2),
        fetch_user(3)
    )
    assert len(results) == 3
    assert all(user["id"] for user in results)
```

### Using create_task

```python
import asyncio
from rustest import mark

async def slow_operation():
    await asyncio.sleep(0.01)
    return "slow"

async def fast_operation():
    return "fast"

@mark.asyncio
async def test_with_tasks():
    """Test using asyncio.create_task."""
    task1 = asyncio.create_task(slow_operation())
    task2 = asyncio.create_task(fast_operation())

    result1 = await task1
    result2 = await task2

    assert result1 is not None
    assert result2 is not None
```

### Async Context Managers

```python
import asyncio
from rustest import mark

class AsyncDatabase:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def get_user(self, id: int):
        return {"id": id}

@mark.asyncio
async def test_async_context_manager():
    """Test with async context manager."""
    async with AsyncDatabase() as db:
        user = await db.get_user(123)
        assert user is not None
```

### Async Generators

```python
import asyncio
from rustest import mark

async def async_data_stream():
    for i in range(3):
        yield i

@mark.asyncio
async def test_async_generator():
    """Test with async generator."""
    results = []
    async for item in async_data_stream():
        results.append(item)
    assert len(results) > 0
```

### Timeouts (Manual vs Built-in)

Rustest provides **built-in timeout support** via `@mark.asyncio(timeout=...)` (see [Built-in Timeout Support](#built-in-timeout-support) above). However, you can also use manual `asyncio.wait_for()` for more granular control within a test:

```python
from rustest import mark, raises
import asyncio

# RECOMMENDED: Use built-in timeout for whole-test timeout
@mark.asyncio(timeout=5.0)
async def test_with_builtin_timeout():
    """Whole test fails if it exceeds 5 seconds."""
    result = await slow_operation()
    assert result is not None

# ALTERNATIVE: Manual timeout for specific operations within a test
@mark.asyncio
async def test_with_manual_timeout():
    """Only the specific operation has a timeout."""
    # First operation - no timeout
    setup_result = await setup_operation()

    # Second operation - must complete in 1 second
    result = await asyncio.wait_for(
        slow_operation(),
        timeout=1.0
    )
    assert result is not None

@mark.asyncio
async def test_timeout_error():
    """Test that slow operation times out."""
    with raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            very_slow_operation(),
            timeout=0.1
        )
```

!!! tip "When to use which?"
    - **Built-in timeout** (`@mark.asyncio(timeout=X)`): Use for "this whole test should complete in X seconds"
    - **Manual timeout** (`asyncio.wait_for()`): Use when you need different timeouts for different parts of a test

## Combining with Other Features

### With Fixtures

Async tests work seamlessly with rustest fixtures:

```python
import asyncio
from rustest import fixture, mark

async def call_api(api_key: str):
    await asyncio.sleep(0.001)
    return {"status": "success", "key": api_key}

@fixture
def api_key() -> str:
    """Regular synchronous fixture."""
    return "test-api-key"

@mark.asyncio
async def test_with_fixture(api_key: str):
    """Async test using synchronous fixture."""
    result = await call_api(api_key)
    assert result["status"] == "success"
```

### With Parametrization

```python
import asyncio
from rustest import parametrize, mark

async def fetch_user(user_id: int):
    await asyncio.sleep(0.001)
    names = {1: "Alice", 2: "Bob", 3: "Charlie"}
    return {"id": user_id, "name": names.get(user_id, "Unknown")}

@mark.asyncio
@parametrize("user_id,expected_name", [
    (1, "Alice"),
    (2, "Bob"),
    (3, "Charlie"),
])
async def test_parametrized_async(user_id: int, expected_name: str):
    """Parametrized async test."""
    user = await fetch_user(user_id)
    assert user["name"] == expected_name
```

### With Other Marks

```python
import asyncio
from rustest import mark

async def run_integration_test():
    await asyncio.sleep(0.001)
    return {"success": True}

@mark.asyncio
@mark.slow
@mark.integration
async def test_full_workflow():
    """Async test with multiple marks."""
    result = await run_integration_test()
    assert result["success"] is True
```

### With Exception Assertions

```python
import asyncio
from rustest import mark, raises

async def process_data(data):
    await asyncio.sleep(0.001)
    if not data:
        raise ValueError("invalid input")
    return data

@mark.asyncio
async def test_async_exception():
    """Test that async function raises expected exception."""
    with raises(ValueError, match="invalid input"):
        await process_data(None)
```

## Test Classes

You can apply `@mark.asyncio` to entire test classes:

```python
import asyncio
from rustest import mark

class Database:
    def __init__(self):
        self._connected = False

    async def connect(self):
        await asyncio.sleep(0.001)
        self._connected = True
        return self

    def is_connected(self):
        return self._connected

    async def query(self, sql: str):
        return [{"id": 1}, {"id": 2}]

    async def disconnect(self):
        self._connected = False

db = None

@mark.asyncio(loop_scope="class")
class TestAsyncDatabase:
    """All async methods share the same event loop."""

    async def test_connect(self):
        """Test database connection."""
        global db
        db = await Database().connect()
        assert db.is_connected()

    async def test_query(self):
        """Test database query."""
        results = await db.query("SELECT * FROM users")
        assert len(results) > 0

    async def test_disconnect(self):
        """Test database disconnection."""
        await db.disconnect()
        assert not db.is_connected()
```

### Mixed Sync and Async Tests

You can mix sync and async tests in the same class:

```python
import asyncio
from rustest import mark

def calculate(a: int, b: int) -> int:
    return a + b

async def async_calculate(a: int, b: int) -> int:
    await asyncio.sleep(0.001)
    return a + b

class TestMixed:
    """Class with both sync and async tests."""

    def test_sync_operation(self):
        """Regular synchronous test."""
        assert calculate(2, 2) == 4

    @mark.asyncio
    async def test_async_operation(self):
        """Async test in the same class."""
        result = await async_calculate(2, 2)
        assert result == 4
```

## Exception Handling

Exceptions raised in async tests are properly propagated:

```python
import asyncio
from rustest import mark, raises

async def function_that_raises():
    await asyncio.sleep(0.001)
    raise RuntimeError("Something went wrong")

@mark.asyncio
async def test_exception_propagation():
    """Test that exceptions are properly raised."""
    # This will properly catch and assert the exception
    with raises(RuntimeError, match="Something went wrong"):
        await function_that_raises()
```

Use `raises()` context manager for expected exceptions:

```python
from rustest import mark, raises

@mark.asyncio
async def test_expected_exception():
    """Test expected async exception."""
    with raises(ValueError):
        await validate_data(invalid_data)
```

## Performance Considerations

### Loop Overhead

Creating a new event loop for each test (function scope) has some overhead. For test suites with many small async tests, consider using broader scopes:

```python
import asyncio
from rustest import mark

async def quick_operation():
    await asyncio.sleep(0.001)
    return "done"

# Many small tests - use module scope
@mark.asyncio(loop_scope="module")
async def test_small_operation_1():
    await quick_operation()

@mark.asyncio(loop_scope="module")
async def test_small_operation_2():
    await quick_operation()
```

### Cleanup

Rustest automatically cleans up the event loop after each test scope, canceling any pending tasks and closing the loop properly.

## Migration from pytest-asyncio

If you're migrating from pytest-asyncio, the transition is straightforward - and you get **built-in timeout support** as a bonus!

### Before (pytest-asyncio)

```python
import pytest
import asyncio

@pytest.mark.asyncio
async def test_async():
    result = await async_operation()
    assert result == expected

# With timeout - requires manual wrapping
@pytest.mark.asyncio
async def test_with_timeout():
    result = await asyncio.wait_for(
        async_operation(),
        timeout=5.0
    )
    assert result == expected
```

### After (rustest)

```python
from rustest import mark

@mark.asyncio
async def test_async():
    result = await async_operation()
    assert result == expected

# With timeout - built-in! No manual wrapping needed
@mark.asyncio(timeout=5.0)
async def test_with_timeout():
    result = await async_operation()
    assert result == expected
```

### Key Improvements Over pytest-asyncio

| Feature | pytest-asyncio | rustest |
|---------|----------------|---------|
| Basic async support | ✅ | ✅ |
| Loop scopes | ✅ | ✅ |
| Class decoration | ✅ | ✅ |
| **🚀 Parallel async test execution** | ❌ | ✅ |
| **⏱️ Built-in per-test timeout** | ❌ | ✅ |
| Clear timeout messages | ❌ | ✅ |

The two killer features that set rustest apart:

1. **Parallel Execution**: Async tests run concurrently, not sequentially. A test suite with 100 async tests that each wait 100ms completes in ~100ms total, not 10 seconds.

2. **Built-in Timeouts**: No plugins needed. Just add `timeout=5.0` to catch hanging tests before they block your CI pipeline.

The API is intentionally similar to minimize migration effort, while adding features that pytest-asyncio lacks.

## Common Patterns

### Testing Async Fixtures (Future Enhancement)

Currently, rustest supports synchronous fixtures used by async tests. Support for async fixtures is planned for a future release.

### Shared Async Resources

Use module or class-scoped loops for shared async resources:

```python
import asyncio
from rustest import mark

class MockConnection:
    async def query(self, sql: str):
        return [1]

class MockPool:
    async def __aenter__(self):
        return MockConnection()
    async def __aexit__(self, *args):
        pass
    def acquire(self):
        return self

connection_pool = MockPool()

# Shared connection pool across all tests in module
@mark.asyncio(loop_scope="module")
async def test_with_shared_pool():
    async with connection_pool.acquire() as conn:
        result = await conn.query("SELECT 1")
        assert result is not None
```

## Best Practices

1. **Always use timeouts**: Add `timeout=X` to every async test to prevent hanging tests in CI:
   <!--rustest.mark.skip-->
   ```python
   @mark.asyncio(timeout=30.0)  # Good: has a timeout
   async def test_api_call():
       ...
   ```

2. **Use appropriate scopes**: Function scope for isolation, broader scopes for performance

3. **Clean up resources**: Use async context managers or proper cleanup in teardown

4. **Avoid shared state**: Even with shared loops, avoid shared mutable state between tests

5. **Test concurrency**: Use `gather()` and `create_task()` to test concurrent operations

6. **Set reasonable timeouts**: Don't set timeouts too tight (flaky tests) or too loose (slow feedback):
   - Unit tests: 1-5 seconds
   - Integration tests: 10-30 seconds
   - End-to-end tests: 60+ seconds

## Limitations

Current limitations (may be addressed in future releases):

- Async fixtures are not yet supported
- Loop scope currently creates a new loop per scope (future versions may reuse loops)
- Debug mode and custom loop policies are not yet configurable

## Next Steps

- [Marks & Skipping](marks.md) - Learn more about marks
- [Fixtures](fixtures.md) - Use fixtures with async tests
- [Parametrization](parametrization.md) - Parametrize async tests
- [Test Classes](test-classes.md) - Organize async tests in classes
