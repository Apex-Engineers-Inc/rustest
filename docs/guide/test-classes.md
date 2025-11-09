# Test Classes

Test classes allow you to group related tests together and share fixtures across test methods. Rustest supports pytest-style test classes.

## Basic Test Classes

Create a test class by naming it with a `Test` prefix:

```python
class TestMathOperations:
    """Group related math tests together."""

    def test_addition(self):
        assert 1 + 1 == 2

    def test_subtraction(self):
        assert 5 - 3 == 2

    def test_multiplication(self):
        assert 3 * 4 == 12
```

!!! note "No __init__ Required"
    Test classes don't need an `__init__` method. Rustest creates fresh instances automatically.

## Using Fixtures in Test Classes

Test methods can use fixtures just like standalone test functions:

```python
from rustest import fixture

@fixture
def calculator():
    return {"add": lambda x, y: x + y, "multiply": lambda x, y: x * y}

class TestCalculator:
    def test_addition(self, calculator):
        assert calculator["add"](2, 3) == 5

    def test_multiplication(self, calculator):
        assert calculator["multiply"](4, 5) == 20
```

## Class-Scoped Fixtures

Use class-scoped fixtures to share expensive setup across all tests in a class:

```python
from rustest import fixture

@fixture(scope="class")
def database():
    """Shared database connection for all tests in a class."""
    db = {"connection": "db://test", "data": []}
    return db

class TestDatabase:
    def test_connection(self, database):
        assert database["connection"] == "db://test"

    def test_add_data(self, database):
        database["data"].append("item1")
        assert len(database["data"]) == 1

    def test_data_persists(self, database):
        # Same database instance from previous test
        assert len(database["data"]) == 1
```

!!! warning "Shared State"
    Class-scoped fixtures maintain state across tests in the class. Be careful with mutable data!

## Fixture Methods Within Classes

Define fixtures as methods inside the test class:

```python
from rustest import fixture

class TestUserService:
    @fixture(scope="class")
    def service(self):
        """Class-level fixture shared across all tests."""
        svc = UserService()
        yield svc
        svc.cleanup()

    @fixture
    def user(self, service):
        """Per-test fixture that depends on class fixture."""
        return service.create_user("test_user")

    def test_user_creation(self, user):
        assert user.name == "test_user"

    def test_user_count(self, service, user):
        assert service.count() >= 1
```

## Class and Instance Variables

### Class Variables

Class variables are shared across all test methods:

```python
class TestSharedData:
    shared_config = {"debug": True, "timeout": 30}

    def test_config_debug(self):
        assert self.shared_config["debug"] is True

    def test_config_timeout(self):
        assert self.shared_config["timeout"] == 30
```

### Instance Variables

Each test method gets a fresh instance, so instance variables are isolated:

```python
class TestInstanceVariables:
    def test_instance_var_1(self):
        self.value = 10
        assert self.value == 10

    def test_instance_var_2(self):
        # Fresh instance - self.value doesn't exist yet
        self.value = 20
        assert self.value == 20
```

## Parametrized Test Methods

Use `@parametrize` on class methods:

```python
from rustest import parametrize

class TestStringOperations:
    @parametrize("text,expected", [
        ("hello", "HELLO"),
        ("world", "WORLD"),
        ("Python", "PYTHON"),
    ])
    def test_uppercase(self, text, expected):
        assert text.upper() == expected

    @parametrize("value", [1, 2, 3, 4, 5])
    def test_positive(self, value):
        assert value > 0
```

## Marks on Test Classes

Apply marks to all tests in a class:

```python
from rustest import mark

@mark.integration
class TestDatabaseIntegration:
    """All tests in this class are integration tests."""

    def test_insert(self):
        pass

    def test_update(self):
        pass

    @mark.slow
    def test_bulk_import(self):
        # Has both @mark.integration and @mark.slow
        pass
```

## Organizing Tests with Classes

### By Feature

```python
class TestUserAuthentication:
    def test_login_success(self):
        pass

    def test_login_failure(self):
        pass

    def test_logout(self):
        pass

class TestUserProfile:
    def test_update_email(self):
        pass

    def test_update_password(self):
        pass

    def test_delete_account(self):
        pass
```

### By Test Type

```python
from rustest import mark

@mark.unit
class TestUnitMath:
    def test_addition(self):
        assert 1 + 1 == 2

    def test_subtraction(self):
        assert 5 - 3 == 2

@mark.integration
class TestIntegrationAPI:
    def test_get_user(self):
        pass

    def test_create_user(self):
        pass
```

## Nested Test Classes

While rustest supports nested classes, it's generally better to use flat structures:

```python
# Supported but not recommended
class TestOuter:
    class TestInner:
        def test_something(self):
            pass

# Better - use flat structure with descriptive names
class TestOuterInner:
    def test_something(self):
        pass
```

## Real-World Examples

### API Testing

```python
from rustest import fixture, mark

@fixture(scope="class")
def api_client():
    client = APIClient("https://api.example.com")
    yield client
    client.close()

@mark.integration
class TestUserAPI:
    def test_get_user(self, api_client):
        response = api_client.get("/users/1")
        assert response.status == 200

    def test_create_user(self, api_client):
        data = {"name": "Alice", "email": "alice@example.com"}
        response = api_client.post("/users", json=data)
        assert response.status == 201

    def test_update_user(self, api_client):
        data = {"email": "newemail@example.com"}
        response = api_client.put("/users/1", json=data)
        assert response.status == 200
```

### Database Testing

```python
from rustest import fixture

@fixture(scope="class")
def db_connection():
    conn = connect_to_database()
    setup_test_schema(conn)
    yield conn
    teardown_test_schema(conn)
    conn.close()

class TestUserRepository:
    @fixture
    def repository(self, db_connection):
        return UserRepository(db_connection)

    def test_create_user(self, repository):
        user = repository.create("Alice")
        assert user.name == "Alice"

    def test_find_user(self, repository):
        user = repository.find_by_name("Alice")
        assert user is not None

    def test_delete_user(self, repository):
        repository.delete("Alice")
        user = repository.find_by_name("Alice")
        assert user is None
```

### Service Testing

```python
from rustest import fixture, parametrize

class TestEmailService:
    @fixture(scope="class")
    def email_service(self):
        service = EmailService()
        service.connect()
        yield service
        service.disconnect()

    @parametrize("email,valid", [
        ("user@example.com", True),
        ("invalid-email", False),
        ("@example.com", False),
        ("user@", False),
    ])
    def test_email_validation(self, email_service, email, valid):
        result = email_service.validate(email)
        assert result == valid

    def test_send_email(self, email_service):
        result = email_service.send(
            to="user@example.com",
            subject="Test",
            body="Hello"
        )
        assert result.success is True
```

## Best Practices

### Keep Classes Focused

Each class should test a single component or feature:

```python
# Good - focused on one component
class TestShoppingCart:
    def test_add_item(self):
        pass

    def test_remove_item(self):
        pass

    def test_calculate_total(self):
        pass

# Less ideal - testing multiple components
class TestEverything:
    def test_cart_add(self):
        pass

    def test_user_login(self):
        pass

    def test_payment_process(self):
        pass
```

### Use Descriptive Class Names

```python
# Good - clear what's being tested
class TestUserRegistration:
    pass

class TestPasswordReset:
    pass

# Less clear
class TestUser:
    pass

class TestStuff:
    pass
```

### Don't Overuse Class Scope

Use class-scoped fixtures only when necessary:

```python
from rustest import fixture

def create_expensive_connection():
    return {"status": "connected"}

# Good - expensive setup worth sharing
@fixture(scope="class")
def database_connection():
    return create_expensive_connection()

# Unnecessary - simple data doesn't benefit from class scope
@fixture(scope="class")  # Should be function scope
def sample_number():
    return 42

def test_with_db(database_connection):
    assert database_connection["status"] == "connected"

def test_with_number(sample_number):
    assert sample_number == 42
```

### Combine with conftest.py

Use conftest.py for fixtures shared across multiple classes:

```python
# conftest.py
from rustest import fixture

@fixture
def api_client():
    return APIClient()

# test_users.py
class TestUsers:
    def test_get_user(self, api_client):
        pass

# test_posts.py
class TestPosts:
    def test_get_post(self, api_client):
        pass
```

## When to Use Test Classes

**Use test classes when:**

- You have multiple related tests
- You want to share fixtures across several tests
- You want to group tests logically

**Use standalone functions when:**

- You have a single test
- Tests are independent and don't share setup
- You prefer simplicity

Both approaches are valid and can be mixed in the same project!

## Next Steps

- [Fixtures](fixtures.md) - Learn more about fixture scopes
- [Marks & Skipping](marks.md) - Apply marks to test classes
- [Writing Tests](writing-tests.md) - General testing patterns
