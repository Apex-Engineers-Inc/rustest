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

```python
# test_users.py
def test_get_user(api_client, database):
    # Fixtures from conftest.py are automatically available
    user = api_client.get("/users/1")
    assert user is not None
```

### Nested conftest.py Files

Rustest supports nested `conftest.py` files in subdirectories:

```
tests/
├── conftest.py          # Root fixtures
├── test_basic.py
└── integration/
    ├── conftest.py      # Additional fixtures for integration tests
    └── test_api.py
```

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
```

### Conditional Fixture Behavior

```python
import os
from rustest import fixture

@fixture
def database_url():
    if os.getenv("USE_POSTGRES"):
        return "postgresql://localhost/testdb"
    return "sqlite:///:memory:"

@fixture
def database(database_url):
    return connect(database_url)
```

### Fixtures with Complex Setup

```python
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
```

## Best Practices

### Keep Fixtures Focused

Each fixture should have a single, clear purpose:

```python
# Good - single responsibility
@fixture
def user():
    return create_user()

@fixture
def admin():
    return create_admin()

# Less ideal - doing too much
@fixture
def test_data():
    return {
        "user": create_user(),
        "admin": create_admin(),
        "posts": create_posts(),
        "comments": create_comments(),
    }
```

### Use Appropriate Scopes

Choose the narrowest scope that meets your needs:

```python
# Good - function scope for test isolation
@fixture
def user():
    return create_user()

# Good - session scope for expensive one-time setup
@fixture(scope="session")
def config():
    return load_config_from_file()
```

### Document Your Fixtures

Add docstrings to complex fixtures:

```python
@fixture(scope="session")
def database():
    """Provides a PostgreSQL database connection for testing.

    The database is populated with test data and cleaned up after
    all tests complete. Shared across the entire test session.
    """
    db = setup_test_database()
    yield db
    db.cleanup()
```

## Next Steps

- [Parametrization](parametrization.md) - Combine fixtures with parametrized tests
- [Test Classes](test-classes.md) - Use fixtures in test classes
- [CLI Usage](cli.md) - Command-line options for test execution
