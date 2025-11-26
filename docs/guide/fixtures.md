# Fixtures

Fixtures provide a way to set up test data, establish connections, or perform other setup operations that your tests need. They promote code reuse and keep your tests clean.

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

When rustest sees that a test function has a parameter, it looks for a fixture with that name and automatically injects it.

## Renaming Fixtures

Sometimes you want to use a different name for your fixture than the function name. The `name` parameter allows you to specify the fixture name:

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

This is particularly useful for:

- **Following naming conventions**: Keep function names descriptive (`client_fixture`) while using short parameter names (`client`)
- **Avoiding name conflicts**: Use different internal names while exposing a standard fixture name
- **Improving test readability**: Use natural parameter names in your tests

```python
from rustest import fixture

# pytest compatibility example
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

Fixtures support different scopes to control when they are created and destroyed:

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

Shared across the entire test session:

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

!!! tip "When to Use Each Scope"
    - **function**: Test isolation is important (default)
    - **class**: Expensive setup shared within a test class
    - **module**: Expensive setup shared within a file
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

Rustest automatically resolves the dependency graph and calls fixtures in the correct order.

## Autouse Fixtures

Autouse fixtures run automatically for all tests in their scope without being explicitly requested as a parameter. This is useful for setup/teardown operations that should run for every test.

### Basic Autouse Fixture

```python
import rustest

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

@rustest.fixture(autouse=True)
def reset_global_state():
    """Reset any global state before each test."""
    global_state.reset()
    yield
    global_state.reset()
```

!!! tip "When to Use Autouse"
    Use autouse for setup/teardown that should happen for every test in a scope. Common patterns:
    - Database resets
    - Cache clearing
    - State initialization
    - Logging and monitoring
    - Temporary file management

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

Rustest supports nested `conftest.py` files in subdirectories:

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

For better organization, you can split fixtures into separate Python modules and load them via `conftest.py` using the `rustest_fixtures` field:

<!--rustest.mark.skip-->
```
project/
├── tests/
│   ├── conftest.py           # Loads fixture modules
│   ├── fixtures/
│   │   ├── database.py       # Database fixtures
│   │   ├── api.py           # API client fixtures
│   │   └── users.py         # User-related fixtures
│   ├── test_users.py
│   └── test_api.py
```

**conftest.py:**
```python
# Load fixture modules using rustest_fixtures (preferred)
rustest_fixtures = ["fixtures.database", "fixtures.api", "fixtures.users"]

# Or load a single module
rustest_fixtures = "fixtures.database"

# For pytest compatibility, pytest_plugins also works but is less clear
pytest_plugins = ["fixtures.database"]  # Works but confusing name
```

**fixtures/database.py:**
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
```python
# All fixtures from loaded modules are automatically available
def test_user_creation(user):
    assert user.name == "Test User"

def test_admin_privileges(admin_user):
    assert admin_user.role == "admin"
```

!!! tip "rustest_fixtures vs pytest_plugins"
    - **`rustest_fixtures`** (preferred) - Clear, explicit naming for fixture modules
    - **`pytest_plugins`** (compatibility) - Works but implies plugin support (which rustest doesn't provide)

    Both load the same way - just Python module imports and fixture extraction. No actual pytest plugin system is involved.

!!! note "What This Is NOT"
    This feature loads **fixture modules**, not pytest plugins. Rustest does not support:
    - pytest's pluggy hook system
    - setuptools entry points (`pytest11`)
    - Advanced plugin features (pytest-cov, pytest-django, etc.)

    It simply imports Python modules and registers their `@fixture` decorated functions.

## Fixture Methods in Test Classes

You can define fixtures as methods within test classes:

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

Rustest provides a set of built-in fixtures that mirror pytest's most commonly used fixtures. These are automatically available without requiring any imports or conftest.py configuration.

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

This fixture is perfect for tests that need to write files or create temporary data without polluting your filesystem. Each test receives a completely isolated directory that is automatically cleaned up after the test completes.

!!! tip "pathlib.Path Advantages"
    The `tmp_path` fixture uses Python's modern `pathlib.Path` instead of string paths. Benefits include:
    - Object-oriented path operations (`/` operator for joining)
    - Built-in methods like `.mkdir()`, `.read_text()`, `.write_text()`
    - Cross-platform path handling
    - Better type safety with type hints

### tmp_path_factory - Creating Multiple Temporary Directories

For tests that need multiple temporary directories or when you want to create directories at different times, use `tmp_path_factory`:

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

The `tmp_path_factory` fixture is session-scoped, meaning it persists for the entire test session but all created directories are cleaned up at the end.

!!! note "Factory vs Direct Fixture"
    Use `tmp_path` when you need one temporary directory per test (most common).
    Use `tmp_path_factory` when you need multiple directories in a single test or more control over directory creation.

### tmpdir - Legacy Support for py.path

For compatibility with older code that uses the `py` library, Rustest provides the `tmpdir` fixture:

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
    The `tmpdir` fixture is provided for legacy compatibility. New tests should use `tmp_path` with `pathlib.Path`, which is the modern Python standard.

### tmpdir_factory - Session-Level Legacy Temporary Directories

Similar to `tmp_path_factory` but using the legacy `py.path.local` API:

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

The `monkeypatch` fixture allows you to temporarily modify attributes, environment variables, dictionary items, and sys.path during testing. All changes are automatically reverted after the test:

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
    original_cwd = os.getcwd()

    monkeypatch.chdir(tmp_path)
    assert os.getcwd() == str(tmp_path)

    # After the test, cwd is restored
    assert os.getcwd() == original_cwd
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
    All monkeypatch changes are automatically reverted after each test, even if the test fails. This ensures test isolation and prevents side effects from affecting other tests.

### capsys - Capturing stdout and stderr

The `capsys` fixture captures output to stdout and stderr during test execution:

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

The `readouterr()` method returns a tuple of `(out, err)` strings and resets the capture buffers. This is useful for testing functions that produce output.

!!! tip "Capture Resets on Read"
    Each call to `readouterr()` clears the captured output, so you can capture different sections of output during a single test.

### capfd - File Descriptor Level Capture

The `capfd` fixture provides similar functionality to `capsys` but captures at the file descriptor level:

```python
def test_fd_capture(capfd) -> None:
    """Capture output at file descriptor level."""
    print("captured by capfd")

    captured = capfd.readouterr()
    assert "captured by capfd" in captured.out
```

!!! note "When to Use capfd vs capsys"
    Use `capsys` for most Python output testing (print, sys.stdout.write).
    Use `capfd` when you need to capture output written directly to file descriptors (e.g., from C extensions or subprocess output). Note: rustest's `capfd` is currently implemented as an alias for `capsys`.

### caplog - Capturing Logging Output

The `caplog` fixture captures messages logged via Python's `logging` module during test execution:

```python
import logging

def test_logging_output(caplog) -> None:
    """Capture and verify logging messages."""
    logging.info("This is an info message")
    logging.warning("This is a warning")
    logging.error("This is an error")

    # Check all messages were captured
    assert len(caplog.records) == 3
    assert caplog.records[0].levelname == "INFO"
    assert caplog.records[1].levelname == "WARNING"
    assert caplog.records[2].levelname == "ERROR"

def test_log_messages(caplog) -> None:
    """Access captured log messages as strings."""
    logging.info("User logged in")
    logging.warning("Low disk space")

    assert caplog.messages == ["User logged in", "Low disk space"]
    assert "Low disk space" in caplog.text
```

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
    """Temporarily change log level."""
    logging.info("Before context")

    with caplog.at_level(logging.ERROR):
        logging.warning("Not captured in context")
        logging.error("Captured in context")

    logging.info("After context")

    # All INFO+ messages captured except the WARNING
    assert len(caplog.records) == 3
    assert "Captured in context" in caplog.messages
    assert "Not captured in context" not in caplog.messages
```

#### Accessing Log Records

The `caplog` fixture provides multiple ways to access captured logs:

```python
import logging

def test_log_record_details(caplog) -> None:
    """Access detailed log record information."""
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
    logging.info("Phase 1")
    assert len(caplog.records) == 1

    caplog.clear()
    assert len(caplog.records) == 0

    logging.info("Phase 2")
    assert caplog.messages == ["Phase 2"]  # Only Phase 2 remains
```

!!! tip "Testing Logging Behavior"
    The `caplog` fixture is essential for:
    - Verifying that your code logs expected messages
    - Testing log levels (debug, info, warning, error)
    - Ensuring sensitive data isn't accidentally logged
    - Testing error handling that relies on logging

### cache - Persistent Cache Between Test Runs

The `cache` fixture provides a persistent cache that survives across test sessions, useful for storing expensive computation results or implementing features like `--lf` (last-failed):

```python
def test_expensive_computation(cache) -> None:
    """Cache expensive computation results."""
    result = cache.get("myapp/computation_result")

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

The cache supports multiple access patterns:

```python
def test_cache_operations(cache) -> None:
    """Different ways to use the cache."""
    # Key-value access
    cache.set("user/settings", {"theme": "dark", "language": "en"})
    settings = cache.get("user/settings")
    assert settings["theme"] == "dark"

    # Dict-style access
    cache["app/counter"] = 42
    assert cache["app/counter"] == 42

    # Check key existence
    assert "app/counter" in cache
    assert "nonexistent" not in cache

    # Default values
    value = cache.get("missing/key", default="fallback")
    assert value == "fallback"
```

#### Cache Storage

The cache stores data in `.rustest_cache/` directory as JSON:

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
    assert cache.get("string") == "hello"
    assert cache.get("nested")["users"][0]["name"] == "Alice"
```

#### Creating Cache Directories

The cache can create subdirectories for storing files:

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

Use forward slashes to organize cache keys hierarchically:

```python
def test_cache_key_organization(cache) -> None:
    """Organize cache with namespaced keys."""
    # Application-specific namespace
    cache.set("myapp/version", "2.0.0")
    cache.set("myapp/config/theme", "dark")

    # Test-specific namespace
    cache.set("test/results/last_run", {"passed": 42, "failed": 3})
    cache.set("test/results/previous_run", {"passed": 40, "failed": 5})

    assert cache.get("myapp/version") == "2.0.0"
    assert cache.get("test/results/last_run")["passed"] == 42
```

!!! tip "Cache Use Cases"
    The cache fixture is perfect for:
    - Storing expensive computation results
    - Implementing `--lf` (last-failed) functionality
    - Tracking test execution history
    - Caching build artifacts or test data
    - Persisting state between test sessions

!!! warning "Cache Cleanup"
    The cache persists between test runs by design. To clear the cache:
    ```bash
    rm -rf .rustest_cache/
    ```

### mocker - Mocking and Test Doubles

The `mocker` fixture provides a pytest-mock compatible API for creating mocks, stubs, and spies in your tests. It wraps Python's `unittest.mock` module with automatic cleanup.

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
    The `mocker` fixture is designed to be API-compatible with [pytest-mock](https://pytest-mock.readthedocs.io/), making it easy to migrate tests from pytest to rustest.

**Main patching methods:**

- `mocker.patch(target)` - Patch an object or module
- `mocker.patch.object(target, attr)` - Patch an attribute
- `mocker.patch.multiple(target, **kwargs)` - Patch multiple attributes
- `mocker.patch.dict(target, values)` - Patch a dictionary

**Utility methods:**

- `mocker.spy(obj, name)` - Spy on a method while calling through
- `mocker.stub(name=None)` - Create a stub that accepts any arguments
- `mocker.async_stub(name=None)` - Create an async stub

**Management methods:**

- `mocker.resetall()` - Reset all mocks
- `mocker.stopall()` - Stop all patches
- `mocker.stop(mock)` - Stop a specific patch

**Direct access to mock classes:**

- `mocker.Mock`, `mocker.MagicMock`, `mocker.AsyncMock`
- `mocker.PropertyMock`, `mocker.NonCallableMock`
- `mocker.ANY`, `mocker.call`, `mocker.sentinel`
- `mocker.mock_open`, `mocker.seal`

```python
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
    """Reset mocks between test phases."""
    mock_fn = mocker.Mock(return_value=42)
    result = mock_fn()
    assert result == 42

    # Reset all mocks
    mocker.resetall()
    mock_fn.assert_not_called()
```

!!! note "Automatic Cleanup"
    All patches and mocks are automatically cleaned up after the test completes. You don't need to manually call `stop()` or `restore()`.

### request - Accessing Test Metadata and Parameters

The `request` fixture provides access to test metadata, configuration, and parameter values for parametrized fixtures. It's automatically available in all fixtures and tests.

#### Type Annotation

Use the `FixtureRequest` type for type hints:

```python
from rustest import fixture, FixtureRequest

@fixture
def my_fixture(request: FixtureRequest):
    """Fixture with type-annotated request parameter."""
    print(f"Running test: {request.node.name}")
    return "data"
```

#### Parametrized Fixtures

The most common use of `request` is to access parameter values in parametrized fixtures:

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

You can provide custom IDs for better test output:

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

The `request.node` attribute provides test metadata:

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

Use `request.node` to check test markers:

```python
from rustest import fixture, mark, FixtureRequest

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

The `request.config` attribute provides access to test configuration:

```python
from rustest import fixture, FixtureRequest

@fixture
def api_client(request: FixtureRequest):
    """Create API client with configuration."""
    # Access command-line options
    base_url = request.config.getoption("--api-url", default="http://localhost")
    verbose = request.config.getoption("verbose", default=0)

    if verbose > 1:
        print(f"Connecting to: {base_url}")

    return create_client(base_url)
```

### Combining Built-in Fixtures

You can combine multiple built-in fixtures in your tests:

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

- [Parametrization](parametrization.md) - Combine fixtures with parametrized tests
- [Test Classes](test-classes.md) - Use fixtures in test classes
- [CLI Usage](cli.md) - Command-line options for test execution
