# Async Testing

Rustest runs `async def` tests natively. There is no plugin to install and nothing to enable:
the engine ports pytest-asyncio's model directly, including loop scopes, the
`asyncio_mode` / `asyncio_default_test_loop_scope` / `asyncio_default_fixture_loop_scope` ini
options, and the `@mark.asyncio` marker itself. It adds one keyword pytest-asyncio has no
equivalent for, `timeout`.

Only asyncio is supported. anyio, trio, tornasync and twisted have no equivalent handling.

## What is Async? (For Beginners)

If you're new to async programming, here's a simple explanation:

**Regular (synchronous) code** runs one thing at a time. If you're waiting for a slow operation (like downloading a file), your program just sits there waiting.

**Asynchronous code** can start a slow operation and then do other things while waiting. It's like ordering food at a restaurant - you don't stand at the counter waiting; you sit down and do other things until your food is ready.

In Python, async functions use `async def` and you `await` operations that might take time:

```python
import asyncio
import time

# Regular function - blocks while sleeping
def slow_sync():
    time.sleep(1)  # Program freezes for 1 second
    return "done"

# Async function - doesn't block the whole program
async def slow_async():
    await asyncio.sleep(1)  # Other code can run during this wait
    return "done"
```

Within one test, `await` lets several slow operations overlap instead of running one after
another. Between tests, see [Concurrency](#concurrency) below: two async tests do not overlap
just because they are async.

## Quick Start

Write an `async def` test. Under rustest's default `asyncio_mode` of `auto`, no marker is
needed:

```python
import asyncio

async def some_async_operation():
    await asyncio.sleep(0)
    return "expected"

async def test_async_function():
    """Test an async function."""
    result = await some_async_operation()
    assert result == "expected"
```

`@mark.asyncio` is still accepted, and is what you need if the suite sets
`asyncio_mode = "strict"` or if you want to pass `loop_scope` or `timeout`:

```python
import asyncio
from rustest import mark

async def some_async_operation():
    await asyncio.sleep(0)
    return "expected"

@mark.asyncio
async def test_marked_async_function():
    """The same test, marked explicitly."""
    result = await some_async_operation()
    assert result == "expected"
```

### Auto mode and strict mode

`asyncio_mode` decides whether an unmarked `async def` test runs or fails.

| Value | Behaviour |
|-------|-----------|
| `auto` | Every `async def` test runs, marker or not. This is rustest's default. |
| `strict` | Only tests carrying an `asyncio` marker run. An unmarked one fails with pytest's own "async def functions are not natively supported" message. |

pytest-asyncio defaults to `strict`; rustest defaults to `auto`, because strict mode exists so
that several async plugins can coexist in one project and rustest is the runner rather than one
plugin among several. Set the ini value if you want the stricter behaviour, or if you want the
two runners to agree:

```toml
[tool.pytest.ini_options]
asyncio_mode = "strict"
```

A suite already written against pytest-asyncio needs no edits. `import pytest_asyncio`
resolves to rustest's compatibility module, so `@pytest_asyncio.fixture` and
`@pytest.mark.asyncio` keep working in strict mode.

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
import asyncio
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
import asyncio
from rustest import mark

async def async_add(a, b):
    await asyncio.sleep(0)
    return a + b

async def async_multiply(a, b):
    await asyncio.sleep(0)
    return a * b

@mark.asyncio
async def test_multiple_operations():
    """Test multiple async operations."""
    result1 = await async_add(1, 2)
    result2 = await async_multiply(result1, 3)
    assert result2 == 9
```

## Loop Scopes

`loop_scope` controls how long the event loop your test runs on lives. Tests that name the same
scope run on the same loop; a narrower scope means a fresh loop more often. The four values are
`function`, `class`, `module` and `session`, and the resolution rule is pytest-asyncio's: the
closest `asyncio` marker's `loop_scope`, or `asyncio_default_test_loop_scope` when no marker
sets one.

`scope=` is accepted as pytest-asyncio's deprecated spelling of `loop_scope=`. Passing both on
one marker is an error.

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

!!! note "Session scope is per worker process"
    Rustest distributes test files across a pool of worker processes, so a session-scoped
    loop is created once per worker, not once per run. This is the same boundary
    pytest-xdist gives session-scoped fixtures. Run with `-n 1` if a suite needs one loop
    for the whole run.

## Async Fixtures

An `async def` fixture is awaited before the test that requests it, and an async generator
fixture yields its value and is resumed for teardown:

```python
import asyncio
from rustest import fixture

@fixture
async def async_client():
    """Set up before the test, tear down after it."""
    await asyncio.sleep(0)
    client = {"open": True}
    yield client
    client["open"] = False

async def test_uses_async_fixture(async_client):
    assert async_client["open"] is True
```

!!! warning "In strict mode, async fixtures need `@pytest_asyncio.fixture`"
    Under `asyncio_mode = "strict"`, an `async def` fixture declared with a plain `@fixture`
    is **not awaited**. The test receives the coroutine object itself, and Python reports
    `RuntimeWarning: coroutine 'name' was never awaited`. This is pytest-asyncio's rule, and
    rustest reproduces it: the flag that marks a fixture as async-aware is set only by
    `pytest_asyncio.fixture`.

    ```python
    import asyncio
    import pytest_asyncio

    @pytest_asyncio.fixture
    async def client():
        await asyncio.sleep(0)
        return {"open": True}
    ```

    Under the default `auto` mode, a plain `@fixture` on an `async def` is awaited normally
    and needs no change.

An async fixture's loop scope resolves in three steps: an explicit `loop_scope` on the fixture,
then `asyncio_default_fixture_loop_scope`, then the fixture's own caching scope. That last
fallback is the one that surprises people: while `asyncio_default_fixture_loop_scope` is unset,
a `scope="module"` async fixture runs on a module-scoped loop rather than a function-scoped
one.

Because a test's loop scope and a fixture's loop scope are resolved independently, they can
land on different loops. A `scope="session"` async fixture and a test left on the default
`function` test loop scope do not share a loop, and an object bound to one loop (a connection,
a `Future`, a lock) will not work on the other. Set both ini keys when a suite has async
fixtures above function scope:

```toml
[tool.pytest.ini_options]
asyncio_default_test_loop_scope = "session"
asyncio_default_fixture_loop_scope = "session"
```

[Async event loops](async-event-loops.md) covers this configuration and the errors that
follow from getting it wrong.

### Overriding the event loop policy

Define an `event_loop_policy` fixture and rustest builds each loop under it, the same way
pytest-asyncio does:

<!--rustest.mark.skip-->
```python
# conftest.py
import asyncio
from rustest import fixture

@fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()
```

## Built-in Timeout Support

`@mark.asyncio(timeout=...)` fails a test that runs too long. pytest-asyncio has no timeout
keyword, so the equivalent there is a `pytest-timeout` dependency or a hand-written
`asyncio.wait_for()` around the slow call.

### Basic Timeout

Add a timeout to any async test with the `timeout` parameter:

```python
import asyncio
from rustest import mark

async def slow_api_call():
    await asyncio.sleep(0)
    return {"status": "ok"}

@mark.asyncio(timeout=5.0)
async def test_api_call():
    """This test will fail if it takes longer than 5 seconds."""
    result = await slow_api_call()
    assert result["status"] == "ok"
```

The timeout is applied as `asyncio.wait_for` inside the loop, so an overrunning test is
cancelled rather than left running. It fails with:

```
Test timed out after 5.0 seconds
```

### Why Built-in Timeouts Matter

A bug in async code can wait forever, and an untimed test that hangs takes the whole run with
it, in CI as well as locally. A timeout also turns a performance regression into a test
failure: an operation that should take 100ms and now takes 10 seconds reports itself.

### Timeout with Loop Scope

Combine timeout with loop scopes for maximum control:

```python
import asyncio
from rustest import mark

class db:
    @staticmethod
    async def query(sql):
        await asyncio.sleep(0)
        return [{"id": 1}]

@mark.asyncio(loop_scope="module", timeout=10.0)
async def test_database_query():
    """Shares event loop with other module tests, fails after 10s."""
    results = await db.query("SELECT * FROM large_table")
    assert len(results) > 0
```

### Class-Level Timeout

Apply timeout to all methods in a test class:

```python
import asyncio
from rustest import mark

async def slow_operation():
    await asyncio.sleep(0)

async def another_slow_operation():
    await asyncio.sleep(0)

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
import asyncio
from rustest import mark

async def fast_operation():
    await asyncio.sleep(0)

async def very_slow_operation():
    await asyncio.sleep(0)

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

**1. Timeout only applies to async tests**

The timeout is applied to the coroutine, so a synchronous function carrying the marker is
unaffected:

<!--rustest.mark.skip-->
```python
@mark.asyncio(timeout=5.0)
def test_sync():  # NOT async! Timeout is ignored.
    time.sleep(10)  # This will NOT timeout after 5 seconds
```

**2. Timeout must be positive**

Passing zero or negative values raises a `ValueError` at decoration time:

- `@mark.asyncio(timeout=0)` -> `ValueError: timeout must be positive`
- `@mark.asyncio(timeout=-1.0)` -> `ValueError: timeout must be positive`

`@mark.asyncio` reached through `pytest.mark.asyncio` performs no validation at decoration
time, so a bad value there is rejected during setup instead.

**3. Timeouts are per-test, not shared**

Each test carries its own timeout, including tests sharing a loop scope. One test timing out
does not shorten the next one's budget:

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
| Marker-free async tests | Default (`asyncio_mode = auto`) | Opt-in (`asyncio_mode = auto`) |
| Loop scopes | Yes | Yes |
| Async fixtures | Yes | Yes |
| Per-test timeout | `@mark.asyncio(timeout=5.0)` | Not available; needs pytest-timeout or a manual `asyncio.wait_for` |
| Timeout message | `Test timed out after X seconds` | Whatever the manual wrapper raises |
| `asyncio_debug` | Not configurable | `asyncio_debug` ini option |

With pytest-asyncio, a per-test timeout is written by hand:

```python
# pytest-asyncio: Manual timeout handling
import pytest
import asyncio

async def slow_operation():
    await asyncio.sleep(0)
    return "done"

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
import asyncio
from rustest import mark

async def slow_operation():
    await asyncio.sleep(0)
    return "done"

@mark.asyncio(timeout=5.0)
async def test_with_timeout():
    result = await slow_operation()
    assert result is not None
```

## Concurrency

Async tests do not overlap each other. Rustest drives each test coroutine to completion before
starting the next one on that loop, which is what pytest-asyncio does as well: it runs each
coroutine through `asyncio.Runner.run`, which cannot be re-entered. Ten tests that each
`await asyncio.sleep(1)` take about ten seconds under both runners, not one.

What rustest parallelises is *files*. The engine routes test files across a pool of worker
processes (four by default, `-n` to change it), so async tests in different files do run at the
same time, in different interpreters. Tests in one file do not.

To overlap work, overlap it inside a test with `gather` or `create_task`:

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

## Advanced Patterns

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

An `async def` function that consumes an async generator is an ordinary async test:

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

A test function that is *itself* an async generator, meaning `async def` with a bare `yield` in
its own body, is reported as xfail rather than run. There is no way to assert against something
that yields instead of returning, and pytest-asyncio makes the same call.

### Timeouts (Manual vs Built-in)

`@mark.asyncio(timeout=...)` bounds the whole test (see [Built-in Timeout
Support](#built-in-timeout-support) above). `asyncio.wait_for()` inside the body bounds one
operation, which is what you want when different parts of a test need different budgets:

```python
from rustest import mark, raises
import asyncio

async def setup_operation():
    await asyncio.sleep(0)
    return "ready"

async def slow_operation():
    await asyncio.sleep(0)
    return "done"

async def very_slow_operation():
    await asyncio.sleep(5)

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

## Combining with Other Features

### With Fixtures

Async tests work with synchronous fixtures as well as async ones:

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
import asyncio
from rustest import mark, raises

async def validate_data(data):
    await asyncio.sleep(0)
    if not data:
        raise ValueError("invalid data")
    return data

invalid_data = None

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

Splitting one large async test file into several also helps, because the worker pool
distributes files rather than individual tests.

### Cleanup

Each loop is closed when its scope's teardown runs, after every async fixture that ran on it.
Pending tasks are cancelled at that point.

## Migration from pytest-asyncio

Most suites need no changes at all: `import pytest`, `import pytest_asyncio`,
`@pytest.mark.asyncio` and `@pytest_asyncio.fixture` all resolve through rustest's
compatibility layer, and the loop-scope rules and ini options are the same. The one default
that differs is `asyncio_mode`, which is `auto` here and `strict` under pytest-asyncio; set it
explicitly if you want the two runners to behave identically.

### Before (pytest-asyncio)

```python
import pytest
import asyncio

async def async_operation():
    await asyncio.sleep(0)
    return "expected"

expected = "expected"

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
import asyncio
from rustest import mark

async def async_operation():
    await asyncio.sleep(0)
    return "expected"

expected = "expected"

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

### Differences worth knowing before you migrate

| Behaviour | pytest-asyncio | rustest |
|-----------|----------------|---------|
| Default `asyncio_mode` | `strict` | `auto` |
| Per-test timeout | Needs pytest-timeout or a manual wrapper | `@mark.asyncio(timeout=...)` |
| Session-scoped loop lifetime | Once per run | Once per worker process |
| `asyncio_debug` ini option | Supported | Not read |
| pytest hooks in `conftest.py` | Run | Not run; rustest has no hook system |

## Common Patterns

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

2. **Use appropriate scopes**: Function scope for isolation, broader scopes when tests must
   share a loop with an async fixture

3. **Match the two loop-scope settings**: if any async fixture is above function scope, set
   `asyncio_default_test_loop_scope` and `asyncio_default_fixture_loop_scope` together

4. **Clean up resources**: Use async context managers or proper cleanup in teardown

5. **Avoid shared state**: Even with shared loops, avoid shared mutable state between tests

6. **Set reasonable timeouts**: Don't set timeouts too tight (flaky tests) or too loose (slow feedback):
   - Unit tests: 1-5 seconds
   - Integration tests: 10-30 seconds
   - End-to-end tests: 60+ seconds

## Limitations

- Tests sharing a loop scope run one at a time. Parallelism comes from the worker pool, which
  distributes files rather than tests.
- `asyncio_debug` is not on the worker protocol, so the loop always runs with debug mode off.
  It changes the loop's own diagnostics and no test outcome.
- A test's loop scope is never widened to match the fixtures it requests. Configure it with
  `asyncio_default_test_loop_scope` or an explicit `loop_scope`.
- Package scope shares a teardown boundary with session scope, so a package-scoped loop is
  closed later than pytest-asyncio would close it.
- Only asyncio is handled. anyio, trio, tornasync and twisted have no support.

## Next Steps

- [Async event loops](async-event-loops.md) - Loop scope configuration and troubleshooting
- [Marks & Skipping](marks.md) - Learn more about marks
- [Fixtures](intro-fixtures.md) - Use fixtures with async tests
- [Parametrization](intro-parametrization.md) - Parametrize async tests
- [Test Classes](test-classes.md) - Organize async tests in classes
