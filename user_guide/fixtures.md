# Fixtures

A fixture supplies a test with something it needs before it can run: sample data, a temporary directory, an open connection. Tests ask for fixtures by name, so one definition can serve a whole suite.

## Basic Fixtures

A fixture is a function decorated with `@fixture` that returns test data:

```python
from rustest import fixture

@fixture
def sample_user() -> dict:
    return {"id": 1, "name": "Alice", "email": "alice@example.com"}

def test_user_email(sample_user: dict) -> None:
    assert "@" in sample_user["email"]

def test_user_name(sample_user: dict) -> None:
    assert sample_user["name"] == "Alice"
```

Each parameter of a test function is matched against the registered fixture names, and the fixture's value is passed in.

## Renaming Fixtures

The `name` parameter registers a fixture under a name other than its function's:

```python
from rustest import fixture

@fixture(name="user")
def user_fixture() -> dict:
    """This fixture is accessible as 'user', not 'user_fixture'."""
    return {"id": 1, "name": "Alice"}

def test_user_id(user: dict) -> None:
    # Use 'user' as the parameter name
    assert user["id"] == 1

def test_user_name(user: dict) -> None:
    assert user["name"] == "Alice"
```

That lets the function keep a descriptive name such as `client_fixture` while tests spell the parameter `client`, and it lets a module expose a standard fixture name without having to use that name internally.

```python
from rustest import fixture

class Connection:
    def execute(self, sql: str) -> int:
        return 1
    def close(self) -> None:
        pass

def create_database_connection() -> Connection:
    return Connection()

@fixture(name="db", scope="session")
def database_connection():
    """Accessible as 'db' in tests."""
    conn = create_database_connection()
    yield conn
    conn.close()

def test_query(db):
    # Clean, short parameter name
    result = db.execute("SELECT 1")
    assert result == 1
```

## Fixture Scopes

A fixture's scope decides how often it is built and when it is torn down. rustest accepts pytest's five: `function`, `class`, `module`, `package` and `session`. Any other value raises `ValueError` at decoration.

### Function Scope (Default)

Creates a new instance for each test function:

```python
from rustest import fixture

@fixture  # Same as @fixture(scope="function")
def counter() -> dict:
    return {"count": 0}

def test_increment_1(counter: dict) -> None:
    counter["count"] += 1
    assert counter["count"] == 1

def test_increment_2(counter: dict) -> None:
    # Gets a fresh counter
    counter["count"] += 1
    assert counter["count"] == 1  # Still 1, not 2
```

### Class Scope

Shared across all test methods in a class:

```python
from rustest import fixture

@fixture(scope="class")
def database() -> dict:
    """Expensive setup shared across class tests."""
    return {"connection": "db://test", "data": []}

class TestDatabase:
    def test_connection(self, database: dict) -> None:
        assert database["connection"] == "db://test"

    def test_add_data(self, database: dict) -> None:
        database["data"].append("item1")
        assert len(database["data"]) == 1

    def test_data_persists(self, database: dict) -> None:
        # Same database instance from previous test
        assert len(database["data"]) == 1
```

### Module Scope

Shared across all tests in a Python module:

```python
from rustest import fixture

@fixture(scope="module")
def api_client() -> dict:
    """Shared across all tests in this module."""
    return {"base_url": "https://api.example.com", "timeout": 30}

def test_api_url(api_client: dict) -> None:
    assert api_client["base_url"].startswith("https://")

def test_api_timeout(api_client: dict) -> None:
    assert api_client["timeout"] == 30
```

### Session Scope

Built once per worker process and shared by every test that worker runs:

```python
from rustest import fixture

def load_config() -> dict:
    return {"environment": "test", "debug": False}

@fixture(scope="session")
def config() -> dict:
    """Global configuration loaded once."""
    return load_config()  # Expensive operation

def test_config_loaded(config: dict) -> None:
    assert "environment" in config
```

!!! warning "Session scope is per worker, not per run"
    rustest hands each worker process a subset of the run's files, so a suite spread over
    several workers gets one session-fixture instance per worker. That is pytest-xdist's
    contract; `-n 1` gives pytest's exactly. Do not rely on a session fixture running
    exactly once for side effects outside the process, such as creating a shared database.

### Package Scope

`scope="package"` is accepted and behaves like session scope inside a worker: the fixture is
built once and kept for that worker's lifetime. It is **not** torn down when the package's
last test finishes, because a worker holds an arbitrary subset of the files and cannot know
where the package's other tests ran.

!!! tip "When to Use Each Scope"
    - **function**: Test isolation is important (default)
    - **class**: Expensive setup shared within a test class
    - **module**: Expensive setup shared within a file
    - **package**: As session, without a teardown at the package boundary
    - **session**: Very expensive setup (database connections, config loading)

## Fixture Dependencies

Fixtures can depend on other fixtures:

```python
from rustest import fixture

@fixture
def database_url() -> str:
    return "postgresql://localhost/testdb"

@fixture
def database_connection(database_url: str) -> dict:
    return {"url": database_url, "connected": True}

@fixture
def user_repository(database_connection: dict) -> dict:
    return {"db": database_connection, "users": []}

def test_repository(user_repository: dict) -> None:
    assert user_repository["db"]["connected"] is True
```

rustest resolves the dependency graph and builds each fixture before the ones that request it.

## Autouse Fixtures

An autouse fixture runs for every test in its scope whether or not the test names it as a parameter. Use it for setup and teardown that should happen unconditionally.

### Basic Autouse Fixture

```python
import rustest

USERS: set[str] = set()

def db_reset() -> None:
    USERS.clear()

def db_cleanup() -> None:
    USERS.clear()

def create_user(name: str) -> None:
    USERS.add(name)

def delete_user(name: str) -> None:
    USERS.discard(name)

def user_exists(name: str) -> bool:
    return name in USERS

@rustest.fixture(autouse=True)
def reset_database():
    """Automatically run before each test."""
    # Setup
    print("Resetting database...")
    db_reset()

    yield

    # Teardown
    db_cleanup()

def test_user_creation():
    # Database is automatically reset before this test
    create_user("Alice")
    assert user_exists("Alice")

def test_user_deletion():
    # Database is automatically reset before this test too
    delete_user("Bob")
    assert not user_exists("Bob")
```

### Autouse with Different Scopes

Autouse fixtures respect scope boundaries just like regular fixtures:

```python
import rustest

GLOBAL_CACHE: dict[str, object] = {}

def get_global_cache() -> dict[str, object]:
    return GLOBAL_CACHE

def init_module_resources() -> None:
    pass

def cleanup_module_resources() -> None:
    pass

def setup_test_db() -> None:
    pass

def teardown_test_db() -> None:
    pass

# Function scope (default) - runs before each test
@rustest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test."""
    cache_obj = get_global_cache()
    cache_obj.clear()
    yield
    cache_obj.clear()

# Module scope - runs once per module
@rustest.fixture(autouse=True, scope="module")
def setup_test_module():
    """Initialize test module resources."""
    print("Setting up module...")
    init_module_resources()
    yield
    print("Tearing down module...")
    cleanup_module_resources()

# Session scope - runs once per test session
@rustest.fixture(autouse=True, scope="session")
def initialize_test_environment():
    """Initialize entire test environment."""
    print("Initializing test environment...")
    setup_test_db()
    yield
    print("Cleaning up test environment...")
    teardown_test_db()

def test_first():
    # cache is cleared, module setup has run, session setup has run
    pass

def test_second():
    # cache is cleared again, but module and session setup don't re-run
    pass
```

### Autouse Fixtures with Dependencies

Autouse fixtures can depend on other fixtures:

```python
import rustest

class DbConnection:
    def __init__(self) -> None:
        self.rows = 0
    def execute(self, sql: str) -> int:
        if sql.startswith("INSERT"):
            self.rows += 1
        elif sql.startswith("DELETE"):
            self.rows = 0
        return self.rows

def create_db_connection() -> DbConnection:
    return DbConnection()

@rustest.fixture
def database_connection():
    return create_db_connection()

@rustest.fixture(autouse=True)
def initialize_data(database_connection):
    """Automatically populate test data before each test."""
    # This depends on database_connection, which will be provided
    database_connection.execute("INSERT INTO users VALUES (...)")
    yield
    database_connection.execute("DELETE FROM users")

def test_user_count(database_connection):
    # Database is automatically populated, and database_connection is available
    result = database_connection.execute("SELECT COUNT(*) FROM users")
    assert result > 0
```

### Autouse with Test Classes

Autouse fixtures work with test classes too:

```python
import rustest

class UserService:
    def __init__(self) -> None:
        self.running = False
    def start(self) -> None:
        self.running = True
    def stop(self) -> None:
        self.running = False
    def is_running(self) -> bool:
        return self.running
    def is_ready(self) -> bool:
        return self.running

class TestUserService:
    @rustest.fixture(autouse=True)
    def setup_service(self):
        """Automatically initialize service before each test method."""
        self.service = UserService()
        self.service.start()
        yield
        self.service.stop()

    def test_service_ready(self):
        # self.service is automatically initialized
        assert self.service.is_running()

    def test_another_operation(self):
        # self.service is initialized again for this test
        assert self.service.is_ready()
```

### Common Use Cases for Autouse

**1. Logging and Monitoring**

```python
from rustest import fixture, FixtureRequest

@fixture(autouse=True)
def test_logging(request: FixtureRequest):
    """Log test start and end."""
    print(f"Starting test: {request.node.name}")
    yield
    print(f"Finished test: {request.node.name}")
```

**2. Temporary File Cleanup**

```python
import rustest

@rustest.fixture(autouse=True)
def cleanup_temp_files(tmp_path):
    """Ensure temp files are cleaned up."""
    yield
    # tmp_path is automatically cleaned up by rustest
```

**3. State Reset Across Tests**

```python
import rustest

class GlobalState:
    def __init__(self) -> None:
        self.counter = 0
    def reset(self) -> None:
        self.counter = 0

global_state = GlobalState()

@rustest.fixture(autouse=True)
def reset_global_state():
    """Reset any global state before each test."""
    global_state.reset()
    yield
    global_state.reset()
```

!!! tip "When to Use Autouse"
    Autouse suits work that every test in a scope needs and no test should have to remember:
    resetting a database, clearing a cache, initialising global state, logging the test
    boundary, cleaning up temporary files.

## Yield Fixtures (Setup/Teardown)

Use `yield` to perform cleanup after tests:

```python
from rustest import fixture

@fixture
def temp_file():
    # Setup
    import tempfile
    file = tempfile.NamedTemporaryFile(delete=False)
    file.write(b"test data")
    file.close()

    yield file.name

    # Teardown - runs after the test
    import os
    os.remove(file.name)

def test_file_exists(temp_file: str) -> None:
    import os
    assert os.path.exists(temp_file)
    # After this test, the file is automatically deleted
```

### Yield Fixtures with Scopes

Teardown timing depends on the fixture scope:

```python
from rustest import fixture

class MockConnection:
    def query(self, sql: str):
        return [1]
    def execute(self, sql: str):
        pass
    def close(self):
        pass

def connect_to_database():
    return MockConnection()

@fixture(scope="class")
def database_connection():
    # Setup once for the class
    conn = connect_to_database()
    print("Database connected")

    yield conn

    # Teardown after all tests in class complete
    conn.close()
    print("Database disconnected")

class TestQueries:
    def test_select(self, database_connection):
        result = database_connection.query("SELECT 1")
        assert result is not None

    def test_insert(self, database_connection):
        database_connection.execute("INSERT INTO ...")
        # Connection stays open between tests
```

## Shared Fixtures with conftest.py

Create a `conftest.py` file to share fixtures across multiple test files:

<!--rustest.mark.skip-->
```python
# conftest.py
from rustest import fixture

@fixture(scope="session")
def database():
    """Shared database connection for all tests."""
    db = setup_database()
    yield db
    db.cleanup()

@fixture
def api_client():
    """API client available to all test files."""
    return create_api_client()
```

All test files in the same directory (and subdirectories) can use these fixtures:

<!--rustest.mark.skip-->
```python
# test_users.py
def test_get_user(api_client, database):
    # Fixtures from conftest.py are automatically available
    user = api_client.get("/users/1")
    assert user is not None
```

### Nested conftest.py Files

rustest reads a `conftest.py` from every directory between the rootdir and the test file:

<!--rustest.mark.skip-->
```
tests/
├── conftest.py          # Root fixtures
├── test_basic.py
└── integration/
    ├── conftest.py      # Additional fixtures for integration tests
    └── test_api.py
```

<!--rustest.mark.skip-->
```python
# tests/conftest.py
from rustest import fixture

@fixture
def base_config():
    return {"environment": "test"}

# tests/integration/conftest.py
from rustest import fixture

@fixture
def api_url(base_config):  # Can depend on parent fixtures
    return f"https://{base_config['environment']}.example.com"
```

Child fixtures can override parent fixtures with the same name.

### Loading Fixtures from External Modules

A large suite can split its fixtures into separate Python modules and have `conftest.py` pull them in with a `pytest_plugins` declaration:

<!--rustest.mark.skip-->
```
project/
└── tests/
    ├── conftest.py           # Names the fixture modules
    ├── fixtures/
    │   ├── database.py       # Database fixtures
    │   ├── api.py            # API client fixtures
    │   └── users.py          # User-related fixtures
    ├── test_users.py
    └── test_api.py
```

**conftest.py:**
<!--rustest.mark.skip-->
```python
# A list of module names
pytest_plugins = ["fixtures.database", "fixtures.api", "fixtures.users"]

# Or a single module, as a bare string
pytest_plugins = "fixtures.database"
```

**fixtures/database.py:**
<!--rustest.mark.skip-->
```python
from rustest import fixture

@fixture(scope="session")
def database():
    """Shared database connection."""
    db = setup_database()
    yield db
    db.cleanup()

@fixture
def db_session(database):
    """Transaction-scoped database session."""
    session = database.create_session()
    yield session
    session.rollback()
```

**fixtures/users.py:**
<!--rustest.mark.skip-->
```python
from rustest import fixture

@fixture
def user(db_session):
    """Create a test user."""
    user = db_session.create_user(name="Test User")
    return user

@fixture
def admin_user(db_session):
    """Create an admin user."""
    user = db_session.create_user(name="Admin", role="admin")
    return user
```

**test_users.py:**
<!--rustest.mark.skip-->
```python
# All fixtures from loaded modules are automatically available
def test_user_creation(user):
    assert user.name == "Test User"

def test_admin_privileges(admin_user):
    assert admin_user.role == "admin"
```

A module named this way is registered with no nodeid, which is what pytest does with a plugin, so its fixtures are visible to the whole run rather than only below the conftest that named it. A module rustest cannot import becomes a collection error for the file, the same treatment a broken `import` in the conftest itself gets.

!!! note "What this loads, and what it does not"
    rustest reads `pytest_plugins` for its **fixtures** and nothing else. It imports the
    named modules and registers their `@fixture` functions. Hooks those modules define are
    never called, because rustest has no hook system: there is no pluggy, no `pytest11`
    setuptools entry point, and no support for plugins built on hooks such as pytest-django.
    A plugin that only supplies fixtures works; one that implements
    `pytest_collection_modifyitems` is silently inert.

## Fixture Methods in Test Classes

Fixtures can be defined as methods on a test class, where they are visible to that class and to nothing else:

```python
from rustest import fixture

class User:
    def __init__(self, name: str, id: int):
        self.name = name
        self.id = id

class UserService:
    def __init__(self):
        self.users = {}
        self.next_id = 1
    def create(self, name: str):
        user = User(name, self.next_id)
        self.users[self.next_id] = user
        self.next_id += 1
        return user
    def delete(self, user_id: int):
        if user_id in self.users:
            del self.users[user_id]
    def exists(self, user_id: int):
        return user_id in self.users
    def cleanup(self):
        self.users.clear()

class TestUserService:
    @fixture(scope="class")
    def user_service(self):
        """Class-specific fixture."""
        service = UserService()
        yield service
        service.cleanup()

    @fixture
    def sample_user(self, user_service):
        """Fixture that depends on class fixture."""
        return user_service.create("test_user")

    def test_user_creation(self, sample_user):
        assert sample_user.name == "test_user"

    def test_user_deletion(self, user_service, sample_user):
        user_service.delete(sample_user.id)
        assert not user_service.exists(sample_user.id)
```

## Advanced Examples

### Fixture Providing Multiple Values

```python
from rustest import fixture

class MockDB:
    def close(self):
        pass

class MockCache:
    def close(self):
        pass

def connect_to_database():
    return MockDB()

def connect_to_cache():
    return MockCache()

@fixture
def database_and_cache():
    db = connect_to_database()
    cache = connect_to_cache()

    yield {"db": db, "cache": cache}

    db.close()
    cache.close()

def test_caching(database_and_cache):
    db = database_and_cache["db"]
    cache = database_and_cache["cache"]
    # Use both connections
    assert db is not None
    assert cache is not None
```

### Conditional Fixture Behavior

```python
import os
from rustest import fixture

class MockDB:
    def __init__(self, url: str):
        self.url = url

def connect(url: str):
    return MockDB(url)

@fixture
def database_url():
    if os.getenv("USE_POSTGRES"):
        return "postgresql://localhost/testdb"
    return "sqlite:///:memory:"

@fixture
def database(database_url):
    return connect(database_url)

def test_database(database):
    assert database.url is not None
```

### Fixtures with Complex Setup

```python
from rustest import fixture

class MockDB:
    def drop_all(self):
        pass
    def stop(self):
        pass

class MockServer:
    def stop(self):
        pass

def start_test_database():
    return MockDB()

def start_test_server(db):
    return MockServer()

def load_fixtures(db):
    pass

@fixture(scope="session")
def test_environment():
    """Set up a complete test environment."""
    # Start test database
    db = start_test_database()

    # Start test server
    server = start_test_server(db)

    # Load test data
    load_fixtures(db)

    yield {"db": db, "server": server}

    # Cleanup
    server.stop()
    db.drop_all()
    db.stop()

def test_environment_setup(test_environment):
    assert test_environment["db"] is not None
    assert test_environment["server"] is not None
```

## Best Practices

### Keep Fixtures Focused

Each fixture should have a single, clear purpose:

```python
from rustest import fixture

def create_user():
    return {"type": "user", "id": 1}

def create_admin():
    return {"type": "admin", "id": 2}

def create_posts():
    return [{"id": 1, "title": "Post"}]

def create_comments():
    return [{"id": 1, "text": "Comment"}]

# Good - single responsibility
@fixture
def user():
    return create_user()

@fixture
def admin():
    return create_admin()

def test_user(user):
    assert user["type"] == "user"

def test_admin(admin):
    assert admin["type"] == "admin"

# Less ideal - doing too much
@fixture
def test_data():
    return {
        "user": create_user(),
        "admin": create_admin(),
        "posts": create_posts(),
        "comments": create_comments(),
    }

def test_all_data(test_data):
    assert test_data["user"] is not None
```

### Use Appropriate Scopes

Choose the narrowest scope that meets your needs:

```python
from rustest import fixture

def create_user():
    return {"id": 1, "name": "Test User"}

def load_config_from_file():
    return {"env": "test", "debug": True}

# Good - function scope for test isolation
@fixture
def user():
    return create_user()

# Good - session scope for expensive one-time setup
@fixture(scope="session")
def config():
    return load_config_from_file()

def test_user_isolation(user):
    assert user["name"] == "Test User"

def test_config(config):
    assert config["env"] == "test"
```

### Document Your Fixtures

Add docstrings to complex fixtures:

```python
from rustest import fixture

class MockDB:
    def cleanup(self):
        pass

def setup_test_database():
    return MockDB()

@fixture(scope="session")
def database():
    """Provides a PostgreSQL database connection for testing.

    The database is populated with test data and cleaned up after
    all tests complete. Shared across the entire test session.
    """
    db = setup_test_database()
    yield db
    db.cleanup()

def test_database_documented(database):
    assert database is not None
```

## Built-in Fixtures

rustest ships ports of pytest's own built-in fixtures. They need no import and no `conftest.py` entry: `tmp_path`, `tmp_path_factory`, `tmpdir`, `tmpdir_factory`, `monkeypatch`, `capsys`, `capfd`, `caplog`, `cache`, `mocker`, `pytestconfig` and `recwarn`, plus `request`.

Nine of pytest's are still missing: `capsysbinary`, `capfdbinary`, `capteesys`,
`doctest_namespace`, `pytester`, `testdir`, `record_property`,
`record_testsuite_property` and `record_xml_attribute`. Asking for one is an error that
names the fixture, never a silent skip.

### tmp_path - Temporary Directories with pathlib

The `tmp_path` fixture provides a unique temporary directory for each test function as a `pathlib.Path` object:

```python
from pathlib import Path

def test_write_file(tmp_path: Path) -> None:
    """Each test gets a fresh temporary directory."""
    file = tmp_path / "test.txt"
    file.write_text("Hello, World!")
    assert file.read_text() == "Hello, World!"

def test_create_subdirectory(tmp_path: Path) -> None:
    """tmp_path is isolated - previous test's files are gone."""
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    assert subdir.exists()
    assert subdir.is_dir()
```

Use it whenever a test writes files, so nothing lands in the working tree. Each test gets its
own directory, named after the test itself (sanitised and cut to 30 characters) so a failing
run leaves a tree you can read. The whole tree is removed at the end of the session rather
than after each test, which means a directory is still there while you are debugging.

!!! tip "pathlib.Path"
    `tmp_path` hands back a `pathlib.Path`, not a string, so you join with `/` and read and
    write through `.mkdir()`, `.read_text()` and `.write_text()` without importing `os.path`.

### tmp_path_factory - Creating Multiple Temporary Directories

Use `tmp_path_factory` when one test needs several temporary directories, or needs to create them at chosen moments rather than at setup:

```python
from pathlib import Path
from typing import Any

def test_multiple_temp_dirs(tmp_path_factory: Any) -> None:
    """Create multiple temporary directories in a single test."""
    dir1 = tmp_path_factory.mktemp("data")
    dir2 = tmp_path_factory.mktemp("config")

    # Both directories exist independently
    (dir1 / "file1.txt").write_text("Data")
    (dir2 / "config.json").write_text('{"key": "value"}')

    assert (dir1 / "file1.txt").exists()
    assert (dir2 / "config.json").exists()

def test_numbered_directories(tmp_path_factory: Any) -> None:
    """Directories are automatically numbered to avoid conflicts."""
    # Both are named "output" but get unique numbers
    output1 = tmp_path_factory.mktemp("output")  # Creates output0
    output2 = tmp_path_factory.mktemp("output")  # Creates output1

    assert output1 != output2

def test_custom_naming(tmp_path_factory: Any) -> None:
    """Control numbering behavior with the numbered parameter."""
    # Without numbering - exact name, only create once
    unique = tmp_path_factory.mktemp("data", numbered=False)
    assert unique.name == "data"
```

The numeric suffix comes from one counter shared by the whole factory, and `tmp_path` draws
on the same counter, so the numbers are unique but not consecutive per name. Pass
`numbered=False` for an exact name; that form raises if the directory already exists, which
makes it usable as a uniqueness assertion.

`tmp_path_factory` is session-scoped, so it lives for the worker's lifetime and removes
everything it created when that worker finishes.

!!! note "Factory vs Direct Fixture"
    `tmp_path` covers the common case of one directory per test.
    Reach for `tmp_path_factory` when a single test needs several, or needs to choose when
    each one appears.

### tmpdir - Legacy Support for py.path

`tmpdir` is a `py.path.local` pointing at **the same directory** `tmp_path` returns, for suites still written against the `py` library:

```python
def test_with_legacy_tmpdir(tmpdir) -> None:
    """Using the legacy py.path.local API."""
    # tmpdir is a py.path.local object
    file = tmpdir.join("test.txt")
    file.write("Content")

    assert file.read() == "Content"
    assert tmpdir.listdir()  # List directory contents
```

!!! warning "Prefer tmp_path"
    `tmpdir` exists for compatibility with older suites. Write new tests against `tmp_path`
    and `pathlib.Path`.

### tmpdir_factory - Session-Level Legacy Temporary Directories

`tmpdir_factory` is `tmp_path_factory` over the same tree, handing back `py.path.local` objects:

```python
def test_with_legacy_factory(tmpdir_factory) -> None:
    """Create multiple py.path.local directories."""
    dir1 = tmpdir_factory.mktemp("session_data")
    dir2 = tmpdir_factory.mktemp("cache")

    file1 = dir1.join("data.txt")
    file1.write("session data")

    assert file1.check()  # Check if file exists
```

### monkeypatch - Patching Attributes and Environment Variables

`monkeypatch` changes an attribute, an environment variable, a dictionary item, `sys.path` or the working directory for the duration of one test, then puts everything back:

#### Patching Object Attributes

```python
class Config:
    debug = False
    timeout = 30

def test_patch_attribute(monkeypatch) -> None:
    """Temporarily patch an object attribute."""
    monkeypatch.setattr(Config, "debug", True)
    assert Config.debug is True

    # After the test, Config.debug reverts to False
```

#### Patching Environment Variables

```python
import os

def test_environment_variable(monkeypatch) -> None:
    """Temporarily set an environment variable."""
    monkeypatch.setenv("API_KEY", "test-key-123")
    assert os.environ["API_KEY"] == "test-key-123"

def test_remove_environment_variable(monkeypatch) -> None:
    """Remove an environment variable for the test."""
    monkeypatch.delenv("HOME", raising=False)
    assert "HOME" not in os.environ
    # HOME is restored after the test
```

#### Patching Dictionary Items

```python
def test_patch_dict(monkeypatch) -> None:
    """Temporarily modify dictionary items."""
    settings = {"theme": "light", "language": "en"}

    monkeypatch.setitem(settings, "theme", "dark")
    assert settings["theme"] == "dark"

    # After the test, reverts to "light"
```

#### Modifying sys.path

```python
import sys

def test_add_to_syspath(monkeypatch) -> None:
    """Temporarily add a directory to sys.path."""
    monkeypatch.syspath_prepend("/custom/module/path")
    assert "/custom/module/path" in sys.path
    # After the test, it's removed from sys.path
```

#### Changing the Working Directory

```python
import os
from pathlib import Path

def test_change_directory(monkeypatch, tmp_path: Path) -> None:
    """Temporarily change the working directory."""
    monkeypatch.chdir(tmp_path)

    # os.getcwd() resolves symlinks, which the macOS temp dir has
    assert Path.cwd().resolve() == tmp_path.resolve()

    # The original directory comes back at teardown, after this test returns
```

#### Patching Module Functions

```python
import json

def test_patch_module_function(monkeypatch) -> None:
    """Patch a function in an imported module."""
    def mock_loads(*args, **kwargs):
        return {"result": "mocked"}

    monkeypatch.setattr(json, "loads", mock_loads)
    result = json.loads('{"key": "value"}')
    assert result == {"result": "mocked"}
```

#### Using the Context Manager

```python
from rustest.builtin_fixtures import MonkeyPatch

def test_with_context_manager() -> None:
    """Use MonkeyPatch as a context manager."""
    with MonkeyPatch.context() as patch:
        import os
        patch.setenv("TEST_VAR", "test_value")
        assert os.environ["TEST_VAR"] == "test_value"

    # Changes are reverted after the with block
```

!!! tip "Automatic Cleanup"
    The fixture undoes every change at teardown, including after a failing test, so one
    test's patches cannot leak into the next.

### capsys - Capturing stdout and stderr

`capsys` captures what a test writes to `sys.stdout` and `sys.stderr`:

```python
import sys

def test_print_output(capsys) -> None:
    """Capture and verify printed output."""
    print("Hello, World!")
    print("Error message", file=sys.stderr)

    captured = capsys.readouterr()
    assert captured.out == "Hello, World!\n"
    assert captured.err == "Error message\n"

def test_multiple_captures(capsys) -> None:
    """Capture output multiple times in one test."""
    print("first")
    out1, _ = capsys.readouterr()

    print("second")
    out2, _ = capsys.readouterr()

    assert out1 == "first\n"
    assert out2 == "second\n"
```

`readouterr()` returns an `(out, err)` pair of strings and resets the buffers.

!!! tip "Capture Resets on Read"
    Because each `readouterr()` clears what it returns, a test can read the output of one
    phase, then the output of the next, without the two running together.

### capfd - File Descriptor Level Capture

`capfd` has the same interface as `capsys` but redirects the **operating-system file descriptors** 1 and 2, so it also catches output that never passes through `sys.stdout`:

```python
import os


def test_fd_capture(capfd) -> None:
    """Capture output at file descriptor level."""
    print("through sys.stdout")
    os.write(1, b"straight to the descriptor\n")

    captured = capfd.readouterr()
    assert captured.out.splitlines() == ["through sys.stdout", "straight to the descriptor"]
```

!!! note "When to Use capfd vs capsys"
    Use `capsys` for most Python output testing (`print`, `sys.stdout.write`).
    Use `capfd` when the output is written straight to a file descriptor: a subprocess that inherits it, a C extension, or a bare `os.write(1, ...)`.

!!! warning "One capture fixture per test"
    `capsys` and `capfd` cannot both be requested by the same test. Doing so is a setup error reading `cannot use capfd and capsys at the same time`. They would each redirect the other's redirect, and whichever started second would silently swallow the first one's output. This matches pytest.

### caplog - Capturing Logging Output

`caplog` captures messages logged through Python's `logging` module. Its handler goes on the **root logger** and, exactly as under pytest, it changes no level: the root logger keeps its default of `WARNING`, so an `INFO` record is never created until you ask for one with `caplog.set_level`.

```python
import logging

def test_logging_output(caplog) -> None:
    """Capture and verify logging messages."""
    caplog.set_level(logging.INFO)

    logging.info("This is an info message")
    logging.warning("This is a warning")
    logging.error("This is an error")

    # Check all messages were captured
    assert len(caplog.records) == 3
    assert caplog.records[0].levelname == "INFO"
    assert caplog.records[1].levelname == "WARNING"
    assert caplog.records[2].levelname == "ERROR"

def test_only_warnings_by_default(caplog) -> None:
    """Without set_level, the root logger's WARNING threshold applies."""
    logging.info("dropped before it reaches any handler")
    logging.warning("Low disk space")

    assert caplog.messages == ["Low disk space"]
    assert "Low disk space" in caplog.text
```

!!! warning "Loggers that do not propagate"
    A logger with `propagate = False` never reaches a handler on the root logger, so its records are **not** captured, under pytest either. A suite that needs them adds `caplog.handler` to that logger itself.

#### Filtering by Log Level

Control which log levels are captured:

```python
import logging

def test_log_levels(caplog) -> None:
    """Only capture WARNING and above."""
    caplog.set_level(logging.WARNING)

    logging.debug("Debug message")  # Not captured
    logging.info("Info message")    # Not captured
    logging.warning("Warning")      # Captured
    logging.error("Error")          # Captured

    assert len(caplog.records) == 2
    assert caplog.messages == ["Warning", "Error"]

def test_with_at_level_context(caplog) -> None:
    """Temporarily change the log level, and put it back on the way out."""
    caplog.set_level(logging.INFO)
    logging.info("Before context")

    with caplog.at_level(logging.ERROR):
        logging.warning("Not captured in context")
        logging.error("Captured in context")

    logging.info("After context")

    # The at_level block raised the bar to ERROR, so the WARNING inside it is dropped
    assert len(caplog.records) == 3
    assert "Captured in context" in caplog.messages
    assert "Not captured in context" not in caplog.messages
```

#### Accessing Log Records

Four attributes read the same capture at different levels of detail: `records` holds the raw `LogRecord` objects, `record_tuples` reduces each to `(name, level, message)`, `messages` keeps only the message strings, and `text` is the whole capture formatted as one string. `caplog.get_records("setup")` reads one phase on its own, with `"call"` and `"teardown"` as the other two.

```python
import logging

def test_log_record_details(caplog) -> None:
    """Access detailed log record information."""
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("myapp")
    logger.info("Application started")
    logger.error("Connection failed", exc_info=True)

    # Access raw LogRecord objects
    assert len(caplog.records) == 2
    assert caplog.records[0].name == "myapp"
    assert caplog.records[0].levelno == logging.INFO

    # Get (name, level, message) tuples
    assert caplog.record_tuples[0] == ("myapp", logging.INFO, "Application started")

    # Get just the messages
    assert "Application started" in caplog.messages

    # Get all messages as single text
    assert "Connection failed" in caplog.text
```

#### Clearing Captured Logs

Clear logs mid-test to isolate different phases:

```python
import logging

def test_log_clearing(caplog) -> None:
    """Clear logs between test phases."""
    caplog.set_level(logging.INFO)
    logging.info("Phase 1")
    assert len(caplog.records) == 1

    caplog.clear()
    assert len(caplog.records) == 0

    logging.info("Phase 2")
    assert caplog.messages == ["Phase 2"]  # Only Phase 2 remains
```

!!! tip "Testing Logging Behavior"
    `caplog` is how a test asserts on logging itself: that the expected message was emitted,
    that it came out at the level you intended, that a secret never reached the log, or that
    an error path logged what it was supposed to before recovering.

### cache - Persistent Cache Between Test Runs

`cache` is a small JSON store under `.rustest_cache/` that survives between runs. It has three
methods: `get(key, default)`, `set(key, value)` and `mkdir(name)`. The `default` argument to
`get` is required, not optional.

This is the same store `--lf` writes to, in pytest's own layout, so a test can read the last
run's failures with `cache.get("cache/lastfailed", {})`.

```python
def test_expensive_computation(cache) -> None:
    """Cache expensive computation results."""
    result = cache.get("myapp/computation_result", None)

    if result is None:
        # Expensive operation only runs once
        result = sum(range(1_000_000))
        cache.set("myapp/computation_result", result)

    assert result > 0

def test_version_tracking(cache) -> None:
    """Track application version across runs."""
    version = cache.get("myapp/version", "1.0.0")
    assert version >= "1.0.0"

    # Update for next run
    cache.set("myapp/version", "1.1.0")
```

#### Cache Operations

Reads and writes both go through named methods. There is no dict-style access: `cache[key]`,
`cache[key] = value` and `key in cache` all raise `TypeError`. A missing key is not an error,
it returns the default you passed, and so does an unreadable or corrupt entry, because a cache
that can fail a run is worse than no cache at all.

```python
def test_cache_operations(cache) -> None:
    """Reading and writing values."""
    cache.set("user/settings", {"theme": "dark", "language": "en"})
    settings = cache.get("user/settings", None)
    assert settings["theme"] == "dark"

    # A miss returns the default
    value = cache.get("missing/key", default="fallback")
    assert value == "fallback"
```

#### Cache Storage

Values are JSON, written one file per key under `.rustest_cache/v2/v/<key>`:

```python
def test_cache_data_types(cache) -> None:
    """Cache supports JSON-serializable types."""
    # Primitives
    cache.set("string", "hello")
    cache.set("number", 42)
    cache.set("boolean", True)
    cache.set("null", None)

    # Collections
    cache.set("list", [1, 2, 3])
    cache.set("dict", {"a": 1, "b": 2})
    cache.set("nested", {"users": [{"id": 1, "name": "Alice"}]})

    # All values persist across test runs
    assert cache.get("string", None) == "hello"
    assert cache.get("nested", None)["users"][0]["name"] == "Alice"
```

#### Creating Cache Directories

`mkdir(name)` returns a directory under `.rustest_cache/v2/d/<name>`, creating it if it is not
already there. The name may not contain a path separator: the cache is a flat namespace, and a
nested name raises `ValueError`.

```python
from pathlib import Path

def test_cache_directories(cache) -> None:
    """Create directories within cache."""
    # Create a cache subdirectory
    data_dir = cache.mkdir("test_data")
    assert isinstance(data_dir, Path)
    assert data_dir.exists()

    # Use it for test files
    (data_dir / "config.json").write_text('{"key": "value"}')
    assert (data_dir / "config.json").read_text() == '{"key": "value"}'
```

#### Cache Keys Convention

A key with forward slashes in it becomes a nested path on disk, so a slash-separated prefix keeps one project's entries out of another's:

```python
def test_cache_key_organization(cache) -> None:
    """Organize cache with namespaced keys."""
    # Application-specific namespace
    cache.set("myapp/version", "2.0.0")
    cache.set("myapp/config/theme", "dark")

    # Test-specific namespace
    cache.set("test/results/last_run", {"passed": 42, "failed": 3})
    cache.set("test/results/previous_run", {"passed": 40, "failed": 5})

    assert cache.get("myapp/version", None) == "2.0.0"
    assert cache.get("test/results/last_run", None)["passed"] == 42
```

!!! tip "Cache Use Cases"
    Anything a run would rather not recompute: the result of an expensive setup step, a
    record of what the previous run did, or a downloaded artifact a test needs. Reading
    rustest's own `cache/lastfailed` entry falls in the same category.

!!! warning "Cache Cleanup"
    The cache persists between test runs by design. To clear the cache:
    ```bash
    rm -rf .rustest_cache/
    ```

### mocker - Mocking and Test Doubles

`mocker` is a port of pytest-mock's fixture. It wraps `unittest.mock` and stops every patch it started when the test ends, newest first, so nested patches of one attribute unwind in the right order.

```python
import os

def test_basic_mocking(mocker):
    """Patch a function with a mock."""
    mock_remove = mocker.patch('os.remove')
    os.remove('/tmp/test.txt')
    mock_remove.assert_called_once_with('/tmp/test.txt')

def test_mock_with_return_value(mocker):
    """Mock with a specific return value."""
    mock_exists = mocker.patch('os.path.exists', return_value=True)
    result = os.path.exists('/nonexistent')
    assert result is True
    mock_exists.assert_called_once_with('/nonexistent')

def test_spy_on_method(mocker):
    """Spy on a method while preserving its behavior."""
    class Calculator:
        def add(self, a, b):
            return a + b

    calc = Calculator()
    spy = mocker.spy(calc, 'add')
    result = calc.add(2, 3)

    # Original behavior is preserved
    assert result == 5
    # But we can verify the call
    spy.assert_called_once_with(2, 3)

def test_stub_for_callbacks(mocker):
    """Create a stub that accepts any arguments."""
    callback = mocker.stub(name='callback')

    # Use the stub in your code
    callback('arg1', 'arg2')
    callback.assert_called_once_with('arg1', 'arg2')

def test_direct_mock_creation(mocker):
    """Create mocks directly for complete control."""
    mock_obj = mocker.MagicMock()
    mock_obj.method.return_value = 'result'
    assert mock_obj.method() == 'result'
    mock_obj.method.assert_called_once()
```

!!! tip "pytest-mock Compatibility"
    A suite written against [pytest-mock](https://pytest-mock.readthedocs.io/) should run
    unchanged. Four pieces of it are missing: `mocker.patch.context_manager`, the
    `mock_use_standalone_module` ini, the wider-scoped `class_mocker`, `module_mocker`,
    `package_mocker` and `session_mocker` fixtures, and pytest-mock's process-wide rewriting
    of `unittest.mock`'s `assert_called_with` family, which changes the failure message but
    not the outcome.

**Main patching methods:**

- `mocker.patch(target)` - Patch an object or module
- `mocker.patch.object(target, attr)` - Patch an attribute
- `mocker.patch.multiple(target, **kwargs)` - Patch multiple attributes
- `mocker.patch.dict(target, values)` - Patch a dictionary

**Utility methods:**

- `mocker.spy(obj, name)` - Spy on a method while calling through
- `mocker.stub(name=None)` - Create a stub that accepts any arguments
- `mocker.async_stub(name=None)` - Create an async stub
- `mocker.create_autospec(spec)` - Autospecced mock, tracked by `resetall()`

**Management methods:**

- `mocker.resetall()` - Reset the mocks the fixture created (patches and `create_autospec`), not bare `mocker.Mock()` objects
- `mocker.stopall()` - Stop all patches
- `mocker.stop(mock)` - Stop a specific patch

**Direct access to mock classes:**

- `mocker.Mock`, `mocker.MagicMock`, `mocker.AsyncMock`
- `mocker.PropertyMock`, `mocker.NonCallableMock`, `mocker.NonCallableMagicMock`
- `mocker.ANY`, `mocker.call`, `mocker.sentinel`, `mocker.DEFAULT`
- `mocker.mock_open`, `mocker.seal`

```python
import os

def test_advanced_mocking(mocker):
    """Advanced mocking patterns."""
    # Mock open() to simulate file reading
    m = mocker.mock_open(read_data='file content')
    mocker.patch('builtins.open', m)

    with open('/tmp/test.txt') as f:
        content = f.read()

    assert content == 'file content'

def test_any_matcher(mocker):
    """Use ANY to match any argument."""
    mock_fn = mocker.Mock()
    mock_fn('test', 123)
    # Don't care about the second argument
    mock_fn.assert_called_once_with('test', mocker.ANY)

def test_call_tracking(mocker):
    """Track multiple calls."""
    mock_fn = mocker.Mock()
    mock_fn(1, 2)
    mock_fn(3, 4)

    assert mock_fn.call_args_list == [
        mocker.call(1, 2),
        mocker.call(3, 4)
    ]

def test_reset_mocks(mocker):
    """Reset the mocks this fixture created."""
    mock_remove = mocker.patch('os.remove')
    os.remove('/tmp/test.txt')
    mock_remove.assert_called_once()

    # resetall() clears the call records of every mock the fixture handed out
    mocker.resetall()
    mock_remove.assert_not_called()

def test_reset_an_untracked_mock(mocker):
    """A bare mocker.Mock() is not tracked, so reset it yourself."""
    mock_fn = mocker.Mock(return_value=42)
    assert mock_fn() == 42

    mocker.resetall()          # does not touch mock_fn
    mock_fn.assert_called_once()

    mock_fn.reset_mock()
    mock_fn.assert_not_called()
```

!!! note "Automatic Cleanup"
    Every patch and mock is undone when the test finishes. Calling `stop()` or `stopall()`
    yourself is only for undoing a patch early, in the middle of a test.

### recwarn - Recording Warnings

`recwarn` collects the warnings a test raises, so they can be asserted on after the fact rather than caught at the point they are issued:

```python
import warnings

def test_records_a_warning(recwarn) -> None:
    warnings.warn("deprecated", DeprecationWarning)

    assert len(recwarn) == 1
    assert recwarn.list[0].category is DeprecationWarning
```

The recorder is open for the whole test, and it installs the `"default"` warning filter, which
is once per location. A warning raised twice from the same line is recorded once.

### pytestconfig - The Session Config Object

`pytestconfig` is the session-scoped form of `request.config`, for code that wants the config without going through a request:

```python
def test_rootdir_is_known(pytestconfig) -> None:
    assert pytestconfig.rootpath.is_dir()
    assert pytestconfig.getini("python_files")
```

The same limits apply to both: see the warning under [Accessing Configuration](#accessing-configuration) below.

### request - Accessing Test Metadata and Parameters

`request` gives a fixture or a test access to the item it is running for: the node, the
config, and the current value of a parametrized fixture. It is available everywhere without
being registered.

#### Type Annotation

Annotate the parameter with `FixtureRequest`, which is importable from `rustest`:

```python
from rustest import fixture, FixtureRequest

@fixture
def my_fixture(request: FixtureRequest):
    """Fixture with type-annotated request parameter."""
    print(f"Running test: {request.node.name}")
    return "data"
```

#### Parametrized Fixtures

A fixture declared with `params=` runs once per value, and `request.param` is the value for the current run:

```python
from rustest import fixture, FixtureRequest

@fixture(params=[1, 2, 3])
def number(request: FixtureRequest) -> int:
    """Fixture that provides multiple values."""
    return request.param

def test_numbers(number: int):
    """This test runs three times with different values."""
    assert number in [1, 2, 3]
```

#### Custom Parameter IDs

`ids=` replaces the generated id component in each node id, which is what `-k` matches against and what the report prints:

```python
from rustest import fixture, FixtureRequest

@fixture(params=["sqlite", "postgres", "mysql"], ids=["SQLite", "PostgreSQL", "MySQL"])
def database_type(request: FixtureRequest) -> str:
    """Parametrized fixture with custom test IDs."""
    return request.param

def test_database(database_type: str):
    """Test ID will show which database type is being tested."""
    assert database_type in ["sqlite", "postgres", "mysql"]
```

#### Accessing Test Node Information

`request.node.name` is the test's name including its parameter id, and `request.node.nodeid` is the full identifier, path included:

```python
from rustest import fixture, FixtureRequest

@fixture(autouse=True)
def log_test_info(request: FixtureRequest):
    """Log test information automatically."""
    print(f"Running: {request.node.name}")
    print(f"Node ID: {request.node.nodeid}")
    yield
    print(f"Finished: {request.node.name}")
```

#### Checking for Markers

`get_closest_marker(name)` returns the nearest mark of that name, or `None`, so a fixture can behave differently for the tests that carry it:

```python
from rustest import fixture, mark, FixtureRequest

class Database:
    def __init__(self, real: bool) -> None:
        self.real = real
    def is_connected(self) -> bool:
        return self.real
    def is_mock(self) -> bool:
        return not self.real

def setup_real_database() -> Database:
    return Database(real=True)

def setup_mock_database() -> Database:
    return Database(real=False)

@fixture
def database(request: FixtureRequest):
    """Setup different databases based on markers."""
    if request.node.get_closest_marker("integration"):
        # Use real database for integration tests
        return setup_real_database()
    # Use mock for unit tests
    return setup_mock_database()

@mark.integration
def test_with_real_db(database):
    """This test gets a real database."""
    assert database.is_connected()

def test_with_mock_db(database):
    """This test gets a mock database."""
    assert database.is_mock()
```

#### Accessing Configuration

`request.config` is a small subset of pytest's `Config`. It answers `rootpath`, `inipath`,
`invocation_params.dir`, `cache`, `getini(name)` and `getoption(name, default)`. The same
object is available as the `pytestconfig` fixture.

```python
from rustest import fixture, FixtureRequest

class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

def create_client(base_url: str) -> Client:
    return Client(base_url)

@fixture
def api_client(request: FixtureRequest):
    """Create API client with configuration."""
    # No command-line option is carried, so this returns the default
    base_url = request.config.getoption("--api-url", default="http://localhost")
    verbose = request.config.getoption("verbose", default=0)

    if verbose > 1:
        print(f"Connecting to: {base_url}")

    return create_client(base_url)
```

!!! warning "getoption always returns your default"
    rustest does not put the run's command-line flags on the worker's wire, so
    `getoption(name, default)` returns `default` for every name, and `getoption(name)` with
    no default raises `ValueError: no option named ...`. That is deliberate: a fabricated
    `verbose=0` would let a suite report on a mode it never ran in. Pass a default and treat
    the result as the default.

    `getini` is narrower still. It answers for the six values the worker carries, the three
    `python_*` naming patterns and the three `asyncio_*` options, and raises for anything
    else. An ini name pytest knows but rustest does not carry, `markers` or `testpaths` for
    instance, gets its own message saying so, rather than being reported as a typo.

### Combining Built-in Fixtures

Built-in fixtures compose with each other and with your own:

```python
import os
from pathlib import Path

def test_multiple_builtin_fixtures(tmp_path: Path, monkeypatch) -> None:
    """Use multiple built-in fixtures together."""
    # Create a test file
    config_file = tmp_path / "config.txt"
    config_file.write_text("API_KEY=secret123")

    # Patch environment variable
    monkeypatch.setenv("CONFIG_PATH", str(config_file))

    # Change working directory
    monkeypatch.chdir(tmp_path)

    # All patches are isolated and cleaned up
    assert os.environ["CONFIG_PATH"] == str(config_file)
    assert os.getcwd() == str(tmp_path)
```

## Next Steps

- [Parametrization](intro-parametrization.md) - Combine fixtures with parametrized tests
- [Test Classes](test-classes.md) - Use fixtures in test classes
- [CLI Usage](cli.md) - Command-line options for test execution
