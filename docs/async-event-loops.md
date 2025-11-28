# Async Event Loops in Rustest

## For Newcomers to Async Programming

If you're new to async programming in Python, this guide will help you understand how rustest handles asynchronous tests and fixtures with smart loop scope detection.

### What is an Event Loop?

Think of an event loop like a waiter in a restaurant:

- **Synchronous (normal) code**: Like a waiter who takes one order, goes to the kitchen, waits for it to cook, brings it back, then moves to the next customer. One thing at a time.

- **Asynchronous code**: Like a waiter who takes multiple orders, sends them all to the kitchen at once, and brings them back as they're ready. Much more efficient!

In Python, an **event loop** is the "waiter" that manages all your async operations. It keeps track of what's running, what's waiting, and coordinates everything.

### The Event Loop Challenge

Here's where it gets tricky: **you can't share work between different waiters** (event loops). If one waiter takes an order, a different waiter can't deliver it.

In testing, this means:
```python
# ❌ This causes problems:
@fixture(scope="session")
async def database():
    db = Database()  # Created by "waiter A" (session loop)
    return db

async def test_something(database):
    # Running with "waiter B" (function loop)
    await database.query()  # ❌ Error! Different waiter!
```

## How Rustest Solves This

Rustest uses **smart loop scope detection** to automatically ensure tests and fixtures use the same event loop (waiter).

### Automatic Detection

**You don't need to configure anything!** Rustest automatically detects what event loop scope your test needs:

```python
from rustest import fixture

# Session-scoped async fixture
@fixture(scope="session")
async def database():
    return Database()

# This test automatically uses the session loop!
async def test_query(database):
    result = await database.query("SELECT *")
    assert len(result) > 0
```

**How it works:**
1. Rustest sees `test_query` uses `database`
2. `database` is a session-scoped async fixture
3. Rustest automatically runs `test_query` in the session loop
4. Everything works seamlessly! ✅

### The Rules (Automatic)

Rustest follows these rules automatically:

1. **No async fixtures** → Each test gets its own loop (full isolation)
2. **Has async fixtures** → Test uses the **widest** fixture scope

**Scope from narrowest to widest:**
```
function → class → module → session
```

### Examples

#### Example 1: Isolated Tests (Default)

```python
# No async fixtures = each test gets its own loop
async def test_independent_1():
    await asyncio.sleep(0.1)
    assert True

async def test_independent_2():
    await asyncio.sleep(0.1)
    assert True
```

Each test runs in complete isolation with its own event loop.

#### Example 2: Session Database

```python
@fixture(scope="session")
async def db():
    """Shared database for all tests."""
    database = await Database.connect()
    yield database
    await database.disconnect()

async def test_users(db):
    users = await db.query("SELECT * FROM users")
    assert len(users) > 0

async def test_posts(db):
    posts = await db.query("SELECT * FROM posts")
    assert len(posts) > 0
```

Both tests automatically share the session loop, so the database connection works correctly.

#### Example 3: Nested Fixtures

```python
@fixture(scope="session")
async def database():
    return Database()

@fixture
async def user(database):
    """Function-scoped fixture using session fixture."""
    user = await database.create_user("test@example.com")
    yield user
    await database.delete_user(user.id)

async def test_user_email(user):
    assert user.email == "test@example.com"
```

**Smart detection:**
- `test_user_email` uses `user` (function scope)
- `user` depends on `database` (session scope)
- Rustest detects: **session scope needed**
- Everything runs in the session loop ✅

#### Example 4: Mixed Scopes

```python
@fixture(scope="session")
async def db():
    return Database()

@fixture(scope="module")
async def api_client():
    return APIClient()

async def test_with_both(db, api_client):
    # Rustest detects: session (widest) + module
    # Uses session loop (widest wins)
    user = await db.get_user(1)
    response = await api_client.get(f"/users/{user.id}")
    assert response.status == 200
```

## Explicit Control (Advanced)

Want explicit control? Use `@mark.asyncio(loop_scope="...")`:

```python
from rustest import mark

# Force function scope (new loop per test)
@mark.asyncio(loop_scope="function")
async def test_isolated():
    await do_something()

# Force session scope (share loop across all tests)
@mark.asyncio(loop_scope="session")
async def test_shared():
    await do_something()
```

**Available loop scopes:**
- `"function"` - New loop for each test (most isolated)
- `"class"` - Shared loop for all methods in a test class
- `"module"` - Shared loop for all tests in a module
- `"session"` - Shared loop for entire test session (least isolated)

### When to Use Explicit Control

**Use automatic detection (default)** for 99% of cases. It just works!

**Use explicit loop_scope** when:
1. **Debugging**: Force isolation to rule out loop-related issues
2. **Performance**: Force session scope to share expensive setup
3. **Testing loop behavior**: Verify code works across different loop configurations

## Common Patterns

### Pattern 1: Database Fixtures

```python
@fixture(scope="session")
async def database():
    """One database connection for all tests."""
    db = await Database.connect("postgresql://...")
    yield db
    await db.close()

@fixture
async def clean_database(database):
    """Clean database before each test."""
    await database.execute("TRUNCATE TABLE users")
    yield database

async def test_create_user(clean_database):
    user = await clean_database.create_user("test@example.com")
    assert user.id is not None
```

**Benefits:**
- Database connection shared across tests (fast)
- Each test starts with clean state
- Automatic loop scope detection handles everything

### Pattern 2: API Client

```python
@fixture(scope="module")
async def api_client():
    """API client reused within a test module."""
    async with httpx.AsyncClient() as client:
        yield client

async def test_get_users(api_client):
    response = await api_client.get("/users")
    assert response.status_code == 200

async def test_create_user(api_client):
    response = await api_client.post("/users", json={"name": "Alice"})
    assert response.status_code == 201
```

### Pattern 3: Async Generator Fixtures

```python
@fixture
async def temp_file():
    """Create and cleanup a temporary file."""
    file_path = "/tmp/test_file.txt"

    # Setup
    async with aiofiles.open(file_path, 'w') as f:
        await f.write("test data")

    yield file_path

    # Teardown
    await asyncio.to_thread(os.remove, file_path)

async def test_read_file(temp_file):
    async with aiofiles.open(temp_file, 'r') as f:
        content = await f.read()
    assert content == "test data"
```

## Troubleshooting

### Error: "RuntimeError: Task got Future attached to a different loop"

**Cause:** This usually means you've explicitly forced a narrower loop scope than your fixtures need.

**Solution:** Remove explicit `loop_scope` and let rustest auto-detect:

```python
# ❌ Problem: Explicit function scope with session fixture
@mark.asyncio(loop_scope="function")
async def test_query(database):  # database is session-scoped
    await database.query()  # Error!

# ✅ Solution: Remove explicit loop_scope
async def test_query(database):
    await database.query()  # Works! Auto-detects session scope
```

### Error: "Event loop is closed"

**Cause:** Trying to reuse a loop that was closed.

**Solution:** This shouldn't happen with rustest's smart detection. If it does:
1. Check for manual loop creation in your code
2. Verify fixtures aren't manually closing loops
3. Report as a bug if using rustest correctly

### Tests Are Too Slow

**Issue:** Each test creating its own event loop adds overhead.

**Solution:** Use scoped fixtures to share resources:

```python
# ❌ Slow: Connecting to database in each test
async def test_query_1():
    db = await Database.connect()
    await db.query()
    await db.close()

async def test_query_2():
    db = await Database.connect()
    await db.query()
    await db.close()

# ✅ Fast: Share database connection
@fixture(scope="session")
async def db():
    database = await Database.connect()
    yield database
    await database.close()

async def test_query_1(db):
    await db.query()

async def test_query_2(db):
    await db.query()
```

### Fixtures Not Sharing Data

**Issue:** Expected fixtures to share state, but they don't.

**Explanation:** Fixtures cache their **return values**, not the event loop. Each fixture runs once per scope and returns the same value.

```python
@fixture(scope="session")
async def counter():
    return {"value": 0}  # This dict is shared

async def test_1(counter):
    counter["value"] += 1
    assert counter["value"] == 1

async def test_2(counter):
    # Same dict object!
    assert counter["value"] == 1  # Sees test_1's change
```

## Comparison with pytest-asyncio

If you're coming from pytest-asyncio, here are the differences:

### pytest-asyncio (Manual Configuration)

```python
import pytest_asyncio

# Must explicitly specify loop_scope for fixtures
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database():
    return Database()

# Must explicitly specify loop_scope for tests
@pytest.mark.asyncio(loop_scope="session")
async def test_query(database):
    await database.query()
```

### rustest (Automatic Detection)

```python
from rustest import fixture

# Just specify fixture scope
@fixture(scope="session")
async def database():
    return Database()

# No configuration needed!
async def test_query(database):
    await database.query()  # Automatically uses session loop
```

**Benefits of rustest's approach:**
- ✅ Less boilerplate
- ✅ Harder to make mistakes
- ✅ More intuitive for beginners
- ✅ Can still use explicit `loop_scope` when needed

## Best Practices

### 1. Let Rustest Handle It

**Don't overthink it!** Rustest's automatic detection handles 99% of cases correctly.

```python
# ✅ Just write your tests naturally
@fixture(scope="session")
async def db():
    return Database()

async def test_something(db):
    result = await db.query()
    assert result
```

### 2. Use Appropriate Fixture Scopes

Choose fixture scope based on **test isolation** needs, not loop concerns:

- `scope="session"` - Expensive resources shared across all tests (databases, API clients)
- `scope="module"` - Resources needed by tests in one file
- `scope="function"` (default) - Fresh state for each test

### 3. Avoid Manual Loop Management

**Don't do this:**
```python
# ❌ Don't manually create loops
async def test_something():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # ...
```

**Do this:**
```python
# ✅ Let rustest manage the loop
async def test_something():
    await do_work()
```

### 4. Keep Fixtures Async When Possible

**Prefer:**
```python
# ✅ Async fixture for async resources
@fixture
async def api_client():
    async with httpx.AsyncClient() as client:
        yield client
```

**Over:**
```python
# ❌ Sync wrapper around async operations
@fixture
def api_client():
    client = httpx.Client()  # Sync version
    yield client
```

## Summary

1. **Event loops** coordinate async operations (like a waiter managing orders)
2. **Rustest automatically detects** the right loop scope for your tests
3. **Tests with async fixtures** automatically share the appropriate loop
4. **No configuration needed** in 99% of cases
5. **Explicit `loop_scope`** available when you need fine control

**The golden rule:** Write your tests naturally, and rustest handles the complex event loop management for you!
