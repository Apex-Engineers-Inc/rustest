# Async Testing

Rustest provides built-in support for testing asynchronous code using the `@mark.asyncio` decorator. This feature is inspired by pytest-asyncio and provides a familiar API for developers who need to test async functions.

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

### Timeouts

```python
from rustest import mark, raises
import asyncio

@mark.asyncio
async def test_with_timeout():
    """Test async operation with timeout."""
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

## Concurrent Async Test Execution

Rustest automatically runs compatible async tests concurrently using `asyncio.gather()`, providing significant speedup for I/O-bound test suites.

### How It Works

When executing tests within a module, rustest analyzes each async test's fixture dependencies to determine if it can safely run concurrently with others:

1. **Gatherable tests**: Async tests that don't depend on session-scoped or package-scoped **async fixtures** are gathered and run concurrently. All gathered tests share a single event loop.
2. **Sequential tests**: Async tests that depend on session-scoped or package-scoped async fixtures run sequentially (they need a persistent event loop shared across the session/package)
3. **Sync tests**: Regular synchronous tests run sequentially as normal

```python
from rustest import mark
import asyncio

# These tests run concurrently - they share a single event loop during gather
@mark.asyncio
async def test_api_call_1():
    """Runs concurrently with other gatherable tests."""
    await asyncio.sleep(0.1)  # Simulates network I/O
    assert True

@mark.asyncio
async def test_api_call_2():
    """Runs at the same time as test_api_call_1."""
    await asyncio.sleep(0.1)
    assert True

@mark.asyncio
async def test_api_call_3():
    """Also concurrent with the others."""
    await asyncio.sleep(0.1)
    assert True
```

!!! note "Event Loop Sharing"
    All gathered tests run in a single shared event loop using `asyncio.gather()`. This is what enables concurrent execution - the event loop can switch between tests while they await I/O.

### Performance Benefits

For I/O-bound async tests, the speedup can be dramatic:

| Scenario | Sequential Time | Concurrent Time | Speedup |
|----------|-----------------|-----------------|---------|
| 10 tests × 100ms each | 1,000ms | ~100ms | **~10×** |
| 50 tests × 50ms each | 2,500ms | ~50ms | **~50×** |
| 100 tests × 20ms each | 2,000ms | ~20ms | **~100×** |

The actual speedup depends on:
- **I/O wait time**: More waiting = more benefit from concurrency
- **CPU work**: CPU-bound portions still run sequentially (Python GIL)
- **Async fixture scopes**: Tests depending on session/package-scoped async fixtures run sequentially

### When Tests Run Sequentially

Tests are excluded from concurrent gathering when they:

1. **Depend on session/package-scoped async fixtures**: These fixtures require a persistent event loop that spans multiple modules, so tests using them cannot be gathered
2. **Are synchronous**: Regular sync tests run sequentially (only async tests benefit from gathering)

```python
from rustest import fixture, mark

# Session-scoped ASYNC fixture - tests using this run sequentially
@fixture(scope="session")
async def database_connection():
    """Session-scoped async fixture - tests using this run sequentially."""
    conn = await create_connection()
    yield conn
    await conn.close()

@mark.asyncio(loop_scope="session")
async def test_db_query_1(database_connection):
    """Runs sequentially - shares session loop with the async fixture."""
    result = await database_connection.query("SELECT 1")
    assert result == 1

@mark.asyncio(loop_scope="session")
async def test_db_query_2(database_connection):
    """Also runs sequentially - same session loop."""
    result = await database_connection.query("SELECT 2")
    assert result == 2
```

!!! tip "Sync fixtures are fine"
    Tests with **synchronous** session/module-scoped fixtures CAN still be gathered. Only **async fixtures** with session/package scope trigger sequential execution.

### Best Practices for Concurrent Tests

1. **Avoid session/package-scoped async fixtures when possible**: These prevent concurrent gathering
2. **Isolate test state**: Since gathered tests share an event loop, avoid global mutable state
3. **Use sync fixtures for shared resources**: Sync fixtures don't affect gathering decisions
4. **Keep async fixtures function or module-scoped**: Module-scoped async fixtures are fine - tests within the same module can still be gathered

```python
from rustest import fixture, mark
import asyncio

# Good: Tests without async fixtures run concurrently
@mark.asyncio
async def test_independent_1():
    await asyncio.sleep(0.1)

@mark.asyncio
async def test_independent_2():
    await asyncio.sleep(0.1)

# Good: Sync fixtures don't affect gathering
@fixture(scope="session")
def api_config():
    return {"base_url": "https://api.example.com"}

@mark.asyncio
async def test_with_sync_fixture(api_config):
    """Still gatherable - the fixture is sync."""
    await asyncio.sleep(0.1)

# Caution: Module-scoped async fixtures still allow gathering within the module
@fixture(scope="module")
async def shared_client():
    return await create_client()

@mark.asyncio
async def test_with_client_1(shared_client):
    """Gatherable with other tests in this module."""
    await shared_client.request()
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

If you're migrating from pytest-asyncio, the transition is straightforward:

### Before (pytest-asyncio)

```python
import pytest

@pytest.mark.asyncio
async def test_async():
    result = await async_operation()
    assert result == expected
```

### After (rustest)

```python
from rustest import mark

@mark.asyncio
async def test_async():
    result = await async_operation()
    assert result == expected
```

The API is intentionally similar to minimize migration effort.

### Key Differences from pytest-asyncio

| Feature | pytest-asyncio | rustest |
|---------|---------------|---------|
| **Concurrent execution** | Sequential by default | Automatic `asyncio.gather()` for compatible tests |
| **Plugin required** | Yes (`pip install pytest-asyncio`) | No - built-in support |
| **Auto mode** | `asyncio_mode = "auto"` | Not supported - explicit `@mark.asyncio` required |
| **Event loop fixture** | `event_loop` fixture available | Not available - loops managed internally |
| **Async fixtures** | `@pytest_asyncio.fixture` | Regular `@fixture` with async functions |
| **Loop scope** | `loop_scope` parameter | Same - `loop_scope` parameter |

### Performance Comparison

Rustest provides a significant advantage for I/O-bound async test suites:

**pytest-asyncio behavior**:
- Runs async tests **sequentially** by default
- Each test awaits completion before the next starts
- 10 tests with 100ms I/O each = **1,000ms total**

**rustest behavior**:
- Runs compatible async tests **concurrently** via `asyncio.gather()`
- I/O wait time overlaps across tests
- 10 tests with 100ms I/O each = **~100ms total** (~10× faster)

This means:

- **API tests**: If you have 50 HTTP endpoint tests averaging 50ms each, rustest runs them in ~50ms vs pytest-asyncio's ~2,500ms
- **Database tests**: Async DB query tests benefit when they don't require session-scoped async connection fixtures
- **WebSocket tests**: Connection and message tests can run concurrently

### Migration Considerations

**What works the same**:
- `@mark.asyncio` decorator syntax
- `loop_scope` parameter with same values
- Async fixtures (defined differently)
- Exception handling in async tests

**What changes**:
- No need to install a plugin
- Tests may complete faster due to concurrent execution
- No `event_loop` fixture - rustest manages loops internally
- No auto mode - always use explicit `@mark.asyncio`

**Potential issues**:
- Tests that accidentally relied on sequential execution may expose race conditions
- Global state mutations between tests may cause issues when running concurrently
- Session-scoped async fixtures require careful design

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

1. **Use appropriate scopes**: Function scope for isolation, broader scopes for performance
2. **Clean up resources**: Use async context managers or proper cleanup in teardown
3. **Avoid shared state**: Even with shared loops, avoid shared mutable state between tests
4. **Test concurrency**: Use `gather()` and `create_task()` to test concurrent operations
5. **Handle timeouts**: Use `asyncio.wait_for()` to prevent tests from hanging

## Limitations

Current limitations (may be addressed in future releases):

- No `event_loop` fixture - loops are managed internally by rustest
- Auto mode (`asyncio_mode = "auto"`) is not supported - use explicit `@mark.asyncio`
- Debug mode and custom loop policies are not yet configurable
- Tests with session/package-scoped async fixtures cannot run concurrently (by design - they share loops)

## Next Steps

- [Marks & Skipping](marks.md) - Learn more about marks
- [Fixtures](fixtures.md) - Use fixtures with async tests
- [Parametrization](parametrization.md) - Parametrize async tests
- [Test Classes](test-classes.md) - Organize async tests in classes
