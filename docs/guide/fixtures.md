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
import rustest

@rustest.fixture(autouse=True)
def test_logging(request):
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

<!--pytest.mark.skip-->
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

<!--pytest.mark.skip-->
```python
# test_users.py
def test_get_user(api_client, database):
    # Fixtures from conftest.py are automatically available
    user = api_client.get("/users/1")
    assert user is not None
```

### Nested conftest.py Files

Rustest supports nested `conftest.py` files in subdirectories:

<!--pytest.mark.skip-->
```
tests/
├── conftest.py          # Root fixtures
├── test_basic.py
└── integration/
    ├── conftest.py      # Additional fixtures for integration tests
    └── test_api.py
```

<!--pytest.mark.skip-->
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
import requests

def test_patch_module_function(monkeypatch) -> None:
    """Patch a function in an imported module."""
    def mock_get(*args, **kwargs):
        class Response:
            status_code = 200
            text = '{"result": "success"}'
        return Response()

    monkeypatch.setattr(requests, "get", mock_get)
    response = requests.get("https://api.example.com")
    assert response.status_code == 200
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
