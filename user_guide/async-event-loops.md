# Async Event Loops in Rustest

## For Newcomers to Async Programming

An event loop is the scheduler that runs your coroutines. It decides which one gets the CPU
next, tracks what is waiting on I/O, and resumes each one when its result arrives.
Synchronous code does one thing at a time and blocks while it waits. Async code hands control
back to the loop during a wait, so something else can run in the meantime.

The part that trips people up in testing is that **objects created on one loop cannot be used
on another**. A connection pool, a lock, a `Future`, a background task: each is bound to the
loop that created it. Hand one to a coroutine running on a different loop and you get
`RuntimeError: Task got Future attached to a different loop`, or an `attached to a different
loop` complaint from whichever library owns the object.

That is the whole problem this page is about. `Database` and `APIClient` below stand for
whatever async resource your own suite builds, so most examples on this page are illustrative
rather than runnable as written:

<!--rustest.mark.skip-->
```python
from rustest import fixture

# Created on the session loop:
@fixture(scope="session")
async def database():
    db = Database()  # bound to whichever loop the fixture ran on
    return db

async def test_something(database):
    # Runs on the function loop, unless you configure otherwise
    await database.query()  # different loop, so this raises
```

## How Rustest Decides Which Loop to Use

Rustest ports pytest-asyncio's loop-scope model rather than inventing one. Two independent
rules decide the answer, and **nothing inspects the fixture graph to reconcile them**. When a
test and its fixture end up on different loops, that is the configuration talking.

**A test body's loop scope** is the closest `@mark.asyncio(loop_scope=...)`, or
`asyncio_default_test_loop_scope` when there is no such mark. That option defaults to
`function`.

**An async fixture's loop scope** is its own `loop_scope` marker, or
`asyncio_default_fixture_loop_scope`, or, while that option is unset, the fixture's own
caching scope. So a `scope="module"` async fixture in an unconfigured project runs on a module
loop while every test around it runs on its own function loop.

Scopes, narrowest to widest:

```
function → class → module → package → session
```

Requesting a fixture does not move a test onto that fixture's loop, and this is the rule most
worth internalising:

<!--rustest.mark.skip-->
```python
from rustest import fixture

# Session-scoped async fixture
@fixture(scope="session")
async def database():
    return Database()

# Runs in the session loop only when asyncio_default_test_loop_scope says session
async def test_query(database):
    result = await database.query("SELECT *")
    assert len(result) > 0
```

!!! warning "A session-scoped async fixture does not pull the test onto its loop"
    In a project with no `[tool.pytest.ini_options]` at all, a test that requests a
    session-scoped async fixture still gets its own function loop, and the fixture keeps the
    session loop. Measured on a one-test file: `id(asyncio.get_running_loop())` differs
    between the two, so anything that ties a resource to its creating loop (asyncpg, aiohttp,
    most connection pools) raises *"attached to a different loop"*.

    Setting both keys to `session` makes the two identical. Rustest's own repository sets
    them, which is why its async suites are green.

    **If you use async fixtures above function scope, set this:**

    ```toml
    [tool.pytest.ini_options]
    asyncio_default_test_loop_scope = "session"
    asyncio_default_fixture_loop_scope = "session"
    ```

    An explicit `@mark.asyncio(loop_scope="session")` on the test does the same job for a
    single test without touching the configuration.

### Examples

#### Isolated Tests, Which Is the Default

```python
import asyncio

# No async fixtures = each test gets its own loop
async def test_independent_1():
    await asyncio.sleep(0.1)
    assert True

async def test_independent_2():
    await asyncio.sleep(0.1)
    assert True
```

With `asyncio_default_test_loop_scope` left at `function`, each of these runs on a loop built
and closed for it alone.

#### A Session Database

<!--rustest.mark.skip-->
```python
from rustest import fixture

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

The connection is opened once. For both tests to be able to *use* it, they have to be on the
session loop too, which means `asyncio_default_test_loop_scope = "session"`, plus
`asyncio_default_fixture_loop_scope = "session"` so the fixture is not left on a loop of its
own.

#### Nested Fixtures

<!--rustest.mark.skip-->
```python
from rustest import fixture

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

Each fixture resolves its own loop scope independently. Depending on `database` does not move
`user` onto the session loop, and `user` does not move the test. With both options set to
`session`, all three land on the same loop. With neither set, `database` runs on a session
loop, `user` on a function loop, and the `create_user` call crosses between them.

#### Mixed Scopes

<!--rustest.mark.skip-->
```python
from rustest import fixture

@fixture(scope="session")
async def db():
    return Database()

@fixture(scope="module")
async def api_client():
    return APIClient()

async def test_with_both(db, api_client):
    # Unconfigured, these are three different loops:
    # db on a session loop, api_client on a module loop, the test on its own
    user = await db.get_user(1)
    response = await api_client.get(f"/users/{user.id}")
    assert response.status == 200
```

Setting `asyncio_default_fixture_loop_scope` pins both fixtures to one scope; set the test
option to the same value and all three agree.

## Configuration

Rustest reads three asyncio options, under the names pytest-asyncio uses, from the files
pytest already reads its configuration from: `[tool.pytest.ini_options]` in `pyproject.toml`,
`[pytest]` in `pytest.ini` or `tox.ini`, or `[tool:pytest]` in `setup.cfg`. A project already
configured for pytest-asyncio needs no changes.

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_test_loop_scope = "session"
asyncio_default_fixture_loop_scope = "session"
```

| Key | Default | What it does |
|---|---|---|
| `asyncio_mode` | `auto` (pytest-asyncio's is `strict`) | `auto` runs every async test without a marker; `strict` requires `@mark.asyncio` on each one |
| `asyncio_default_test_loop_scope` | `function` | The loop scope for a test body carrying no `loop_scope` mark |
| `asyncio_default_fixture_loop_scope` | *unset* | The loop scope for async fixtures. **Unset is a third answer, not `"function"`.** An async fixture resolves its loop scope as `mark ?? this option ?? the fixture's own caching scope`, so while this is unset a `scope="module"` async fixture runs on a *module*-scoped loop. Setting it to `function` is therefore a real change, not a no-op |

!!! warning "`asyncio_mode` is the one default that differs from pytest-asyncio"
    Rustest defaults to `auto`; pytest-asyncio defaults to `strict`. An unmarked async test
    **runs** under rustest and is an **error** under pytest-asyncio's default. Set
    `asyncio_mode = "strict"` if you want the stricter behaviour, or want the two runners to
    agree without relying on markers.

**Which value should you use?** If any async fixture is above function scope, set both loop
scope keys to `session`. If every async fixture is function-scoped, the defaults are already
right and you need no configuration at all.

## Explicit Control

`@mark.asyncio(loop_scope=...)` overrides the configured default for one test, in either
direction. It can widen a single test onto a session loop in an otherwise unconfigured
project, and it can narrow one back to a private loop where the default is `session`. Two
tests marked `loop_scope="function"` get two distinct loops even with the default set to
`session`.

```python
from rustest import mark

async def do_something():
    return None

# Force function scope (new loop per test)
@mark.asyncio(loop_scope="function")
async def test_isolated():
    await do_something()

# Force session scope (share loop across all tests)
@mark.asyncio(loop_scope="session")
async def test_shared():
    await do_something()
```

Applied to a class, the mark reaches every coroutine method on it, so one decorator puts a
whole `TestXyz` on one loop.

**Available loop scopes:**

- `"function"` - New loop for each test (most isolated)
- `"class"` - Shared loop for all methods in a test class
- `"module"` - Shared loop for all tests in a module
- `"package"` - Shared loop for all tests in a package
- `"session"` - Shared loop for entire test session (least isolated)

### When to Reach for It

1. **One outlier.** A single test needs a wider or narrower loop than the rest of the suite.
2. **Debugging.** Force `function` scope to rule out cross-test loop contamination.
3. **Performance.** Force `session` scope so expensive setup happens once.

## Common Patterns

### Database Fixtures

<!--rustest.mark.skip-->
```python
from rustest import fixture

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

The connection opens once and each test still starts from a known state. Both loop scope
options need to be `session` for the truncate and the connection to run on the same loop.

### API Client

<!--rustest.mark.skip-->
```python
from rustest import fixture

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

With `asyncio_default_fixture_loop_scope` unset, this client lives on a module loop, so the
tests want `asyncio_default_test_loop_scope = "module"` or wider.

### Async Generator Fixtures

<!--rustest.mark.skip-->
```python
import asyncio
import os

from rustest import fixture

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

Setup runs before the `yield` and teardown after it, both on the fixture's own loop.

## Troubleshooting

### RuntimeError: Task got Future attached to a different loop

The test and the object it is using were built on different loops. Check the two rules at the
top of this page against your configuration: the test's scope comes from its mark or
`asyncio_default_test_loop_scope`, the fixture's from its own marker,
`asyncio_default_fixture_loop_scope`, or its caching scope.

Most often the fix is to set both options to `session` in `pyproject.toml`. The other common
cause is a mark pinning one side to something narrower than the fixture it uses:

<!--rustest.mark.skip-->
```python
from rustest import mark

# Problem: an explicit function scope against a session-scoped fixture
@mark.asyncio(loop_scope="function")
async def test_query(database):  # database is session-scoped
    await database.query()  # Error!

# Fix: name the scope the fixture is already on
@mark.asyncio(loop_scope="session")
async def test_query(database):
    await database.query()  # same loop, so this works
```

### ScopeMismatch on a Fixture

A fixture cannot run on a loop narrower than its own caching scope. Setting
`asyncio_default_fixture_loop_scope = "function"` while keeping a `scope="session"` async
fixture asks for exactly that, and rustest reports it rather than tearing the fixture down
against a foreign loop. Widen the option, or narrow the fixture.

### Event Loop Is Closed

Something held a reference to a loop past its scope's teardown. Check for manual loop creation
in your own code, and for fixtures that close a loop they did not build. If neither applies,
it is worth filing.

### Tests Are Too Slow

Building and closing a loop per test costs something, and so does reconnecting to a database
per test.

<!--rustest.mark.skip-->
```python
from rustest import fixture

# Slow: Connecting to database in each test
async def test_query_1():
    db = await Database.connect()
    await db.query()
    await db.close()

async def test_query_2():
    db = await Database.connect()
    await db.query()
    await db.close()

# Fast: Share database connection
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

Widening the fixture's scope only helps if the tests are on a loop wide enough to use it, so
set `asyncio_default_test_loop_scope` alongside it.

### Fixtures Not Sharing Data

Fixtures cache their **return values**, not the event loop. A fixture body runs once per
scope, and every test in that scope receives the same object.

```python
from rustest import fixture

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

The loop-scope rules are the same, which is the point: rustest ports them from
`pytest_asyncio/plugin.py` so a suite already configured for pytest-asyncio behaves
identically. Two differences are worth knowing.

`asyncio_mode` defaults to `auto` here and `strict` there, so an unmarked async test runs
under rustest and errors under pytest-asyncio.

Rustest also has no separate `@pytest_asyncio.fixture` decorator. An `async def` fixture
declared with plain `@fixture` is awaited in `auto` mode. In `strict` mode it is not, and the
test receives a coroutine object, which is what pytest-asyncio does with the same shape.

Where pytest-asyncio spells the scope on each declaration:

<!--rustest.mark.skip-->
```python
import pytest
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

rustest takes it from the project's configuration instead:

<!--rustest.mark.skip-->
```python
from rustest import fixture

# Just specify fixture scope
@fixture(scope="session")
async def database():
    return Database()

# The two loop scope options carry the rest
async def test_query(database):
    await database.query()  # session loop when both options say session
```

That is two configuration lines once, rather than a `loop_scope=` on every declaration. The
cost is that the coupling is no longer visible at the call site, which is why the warning near
the top of this page exists.

## Best Practices

### Set the Two Loop Scope Options Once

If any async fixture in the project sits above function scope, put both options in
`pyproject.toml` and stop thinking about it. Per-test marks are for the exceptions.

<!--rustest.mark.skip-->
```python
from rustest import fixture

# With both loop scope options set to session:
@fixture(scope="session")
async def db():
    return Database()

async def test_something(db):
    result = await db.query()
    assert result
```

### Choose Fixture Scope for Isolation, Then Match the Loop

Pick `scope=` based on how much state a test should inherit, not on loop mechanics, then set
the loop options to agree with the widest scope you used.

- `scope="session"` - Expensive resources shared across all tests (databases, API clients)
- `scope="module"` - Resources needed by tests in one file
- `scope="function"` (default) - Fresh state for each test

### Avoid Manual Loop Management

A loop you build inside a test is one rustest does not know about and will not tear down.

Instead of this:

```python
import asyncio

# Don't manually create loops
async def test_something():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # ...
```

write this:

```python
async def do_work():
    return None

# Let rustest manage the loop
async def test_something():
    await do_work()
```

### Keep Fixtures Async When the Resource Is Async

An async fixture is awaited on its loop and torn down on the same one.

<!--rustest.mark.skip-->
```python
from rustest import fixture

# Async fixture for async resources
@fixture
async def api_client():
    async with httpx.AsyncClient() as client:
        yield client
```

A sync wrapper around an async library gives that up:

<!--rustest.mark.skip-->
```python
from rustest import fixture

# Sync wrapper around async operations
@fixture
def api_client():
    client = httpx.Client()  # Sync version
    yield client
```

## Summary

A test body runs on the loop named by its `@mark.asyncio(loop_scope=...)`, or by
`asyncio_default_test_loop_scope`, which defaults to `function`. An async fixture runs on the
loop named by its own marker, or by `asyncio_default_fixture_loop_scope`, or by its own
caching scope. Nothing reconciles the two for you, so a project with async fixtures above
function scope has to set both options. Setting them both to `session` is what rustest's own
suite does.
