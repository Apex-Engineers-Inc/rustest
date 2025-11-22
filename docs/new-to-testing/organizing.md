# Organizing Your Tests

As your project grows, you'll need to organize your tests effectively. This guide shows you how to structure tests for maintainability and clarity.

## Directory Structure

A typical Python project with tests looks like this:

```
my_project/
├── src/
│   └── myapp/
│       ├── __init__.py
│       ├── auth.py
│       ├── database.py
│       └── utils.py
├── tests/
│   ├── test_auth.py
│   ├── test_database.py
│   └── test_utils.py
├── pyproject.toml
└── README.md
```

### Why a `tests/` Directory?

Keeping tests separate from code:

- ✅ **Cleaner structure** — Code and tests don't mix
- ✅ **Easy to find** — All tests in one place
- ✅ **Better packaging** — Tests don't ship with your app
- ✅ **Flexible testing** — Run all tests with one command

### Alternative Structure

For larger projects, mirror your code structure:

```
my_project/
├── src/
│   └── myapp/
│       ├── api/
│       │   ├── users.py
│       │   └── posts.py
│       └── database/
│           ├── models.py
│           └── queries.py
└── tests/
    ├── api/
    │   ├── test_users.py
    │   └── test_posts.py
    └── database/
        ├── test_models.py
        └── test_queries.py
```

This makes it easy to find tests for specific code files.

## Naming Conventions

Rustest automatically finds tests using these patterns:

### Test Files

- ✅ `test_*.py` — Example: `test_auth.py`
- ✅ `*_test.py` — Example: `auth_test.py`
- ❌ `tests.py` — Won't be discovered (doesn't match pattern)

### Test Functions

- ✅ `test_*()` — Example: `test_login()`
- ❌ `check_login()` — Won't be discovered (doesn't start with `test_`)

### Test Classes

- ✅ `Test*` — Example: `TestUserAuth`
- ❌ `AuthTests` — Won't be discovered (doesn't start with `Test`)

!!! tip "Be Consistent"
    Pick one style (`test_*.py` or `*_test.py`) and stick with it across your project.

## Grouping Tests with Marks

**Marks** let you categorize and filter tests:

```python
from rustest import mark

@mark.unit
def test_add():
    assert add(1, 2) == 3

@mark.integration
def test_database_connection():
    db = connect_database()
    assert db.is_connected

@mark.slow
def test_large_dataset():
    result = process_million_rows()
    assert result.success
```

### Running Specific Marks

```bash
# Run only unit tests
rustest -m "unit"

# Skip slow tests
rustest -m "not slow"

# Run integration or slow tests
rustest -m "integration or slow"
```

### Common Marks

```python
@mark.unit          # Fast, isolated unit tests
@mark.integration   # Tests that touch databases, APIs, etc.
@mark.slow          # Tests that take time
@mark.smoke         # Critical tests to run first
@mark.regression    # Tests for previously fixed bugs
```

## Test Classes

Group related tests in classes:

```python
class TestUserAuth:
    def test_login_success(self):
        user = login("alice@example.com", "password")
        assert user is not None

    def test_login_failure(self):
        with raises(AuthError):
            login("alice@example.com", "wrong_password")

    def test_logout(self):
        user = login("alice@example.com", "password")
        logout(user)
        assert user.is_logged_in is False
```

### Benefits of Test Classes

- ✅ **Logical grouping** — Related tests stay together
- ✅ **Shared setup** — Use class-level fixtures
- ✅ **Clearer output** — Tests are grouped in output
- ✅ **Better organization** — Easy to navigate

### Sharing Setup in Classes

```python
from rustest import fixture

class TestShoppingCart:
    @fixture
    def cart(self):
        # This fixture is available to all tests in this class
        return ShoppingCart()

    def test_add_item(self, cart):
        cart.add_item("Apple", 1.50)
        assert cart.total == 1.50

    def test_remove_item(self, cart):
        cart.add_item("Apple", 1.50)
        cart.remove_item("Apple")
        assert cart.total == 0.00
```

## Sharing Fixtures with conftest.py

For fixtures used across multiple test files, use `conftest.py`:

```
tests/
├── conftest.py         # Shared fixtures
├── test_users.py
├── test_posts.py
└── test_comments.py
```

**`conftest.py`:**

```python
from rustest import fixture

@fixture
def database():
    db = Database()
    db.connect()
    yield db
    db.disconnect()

@fixture
def api_client():
    client = APIClient("https://api.example.com")
    return client
```

**`test_users.py`:**

```python
# No imports needed! Fixtures from conftest.py are automatically available
def test_create_user(database):
    user = database.create_user("alice@example.com")
    assert user is not None

def test_get_user_api(api_client):
    user = api_client.get("/users/1")
    assert user["name"] == "Alice"
```

### Nested conftest.py

You can have multiple `conftest.py` files at different levels:

```
tests/
├── conftest.py              # Shared across all tests
├── unit/
│   ├── conftest.py          # Shared across unit tests only
│   ├── test_math.py
│   └── test_utils.py
└── integration/
    ├── conftest.py          # Shared across integration tests only
    ├── test_api.py
    └── test_database.py
```

Fixtures in inner `conftest.py` override outer ones if they have the same name.

## Separating Test Types

Organize tests by type for flexibility:

```
tests/
├── unit/              # Fast, isolated tests
│   ├── test_utils.py
│   └── test_models.py
├── integration/       # Tests with external dependencies
│   ├── test_api.py
│   └── test_database.py
└── e2e/              # End-to-end tests
    └── test_workflows.py
```

Run specific types:

```bash
# Only unit tests (fast)
rustest tests/unit/

# Only integration tests
rustest tests/integration/

# Everything
rustest tests/
```

## Running Tests Efficiently

### Run Only Changed Tests

Use `--lf` (last failed) to rerun failed tests:

```bash
rustest --lf
```

Use `--ff` (failed first) to run failed tests first, then all others:

```bash
rustest --ff
```

### Filter by Name

Run tests matching a pattern:

```bash
# Run all login tests
rustest -k "login"

# Run tests with "user" or "auth" in the name
rustest -k "user or auth"

# Exclude slow tests by name
rustest -k "not slow"
```

### Stop on First Failure

Fail fast for quick debugging:

```bash
rustest -x  # Exit after first failure
```

### Combine Options

```bash
# Run failed tests first, stop on first new failure
rustest --ff -x

# Run unit tests, skip slow ones
rustest tests/unit/ -m "not slow"
```

## Real-World Project Structure

Here's a complete example:

```
my_api/
├── src/
│   └── api/
│       ├── __init__.py
│       ├── auth.py
│       ├── users.py
│       ├── posts.py
│       └── database.py
├── tests/
│   ├── conftest.py              # Shared fixtures (database, api_client)
│   ├── unit/
│   │   ├── test_auth.py         # Fast auth logic tests
│   │   ├── test_users.py        # Fast user logic tests
│   │   └── test_posts.py        # Fast post logic tests
│   └── integration/
│       ├── conftest.py          # Integration-specific fixtures
│       ├── test_api_endpoints.py
│       └── test_database.py
├── pyproject.toml
└── README.md
```

**Workflow:**

```bash
# During development: fast unit tests
rustest tests/unit/

# Before committing: all tests
rustest

# In CI: all tests with verbose output
rustest -v
```

## Best Practices

### ✅ Keep Tests Fast

Fast tests = happy developers. Keep unit tests under 100ms each:

```python
# ✅ GOOD: Fast test
def test_calculate():
    result = add(2, 3)
    assert result == 5

# ❌ BAD: Slow test
@mark.slow
def test_api_integration():
    time.sleep(5)  # Avoid sleeps in tests!
    result = call_external_api()
    assert result.status == 200
```

### ✅ Name Tests Descriptively

```python
# ❌ BAD
def test_1():
    ...

# ✅ GOOD
def test_login_fails_with_invalid_password():
    ...
```

### ✅ One Assert Per Test (Usually)

Focus each test on one behavior:

```python
# ✅ GOOD
def test_user_signup_creates_user():
    user = signup("alice@example.com", "password")
    assert user is not None

def test_user_signup_sets_email():
    user = signup("alice@example.com", "password")
    assert user.email == "alice@example.com"

# ⚠️ ACCEPTABLE
def test_user_signup():
    user = signup("alice@example.com", "password")
    assert user is not None
    assert user.email == "alice@example.com"
    assert user.is_active is True
```

Use multiple asserts if they're all checking the same behavior.

### ✅ Test Edge Cases

Don't just test the happy path:

```python
def test_divide_normal_case():
    assert divide(10, 2) == 5

def test_divide_by_zero():
    with raises(ZeroDivisionError):
        divide(10, 0)

def test_divide_negative_numbers():
    assert divide(-10, 2) == -5

def test_divide_floats():
    assert divide(7, 2) == approx(3.5)
```

## What's Next?

You now know how to organize tests for real projects! Ready to dive deeper?

### For Complete Reference

[:octicons-arrow-right-24: Core Testing Guide](../guide/writing-tests.md){ .md-button .md-button--primary }

Explore the complete reference documentation for all testing features.

### Learn More About

- [Marks & Filtering](../guide/marks.md) — Advanced mark usage
- [Test Classes](../guide/test-classes.md) — Class-based testing patterns
- [CLI Usage](../guide/cli.md) — All command-line options
- [Fixtures](../guide/fixtures.md) — Advanced fixture patterns

### Continue Your Learning

You've completed the beginner's guide to testing! You now know:

- ✅ Why automated testing matters
- ✅ How to write and run tests
- ✅ Testing fundamentals (AAA, assertions, edge cases)
- ✅ Making tests reusable with fixtures
- ✅ Testing multiple cases with parametrization
- ✅ Organizing tests for real projects

**Congratulations!** You're ready to write comprehensive, maintainable tests. 🎉
