# Organizing your tests

As your project grows, you'll need to organize your tests effectively. This guide shows you how to structure tests for maintainability and clarity.

## Directory structure

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

### Why a `tests/` directory?

Keeping tests separate from code gives you:

- **Cleaner structure.** Code and tests don't mix
- **Easy to find.** All tests in one place
- **Better packaging.** Tests don't ship with your app
- **Flexible testing.** Run all tests with one command

### Alternative structure

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

## Naming conventions

Rustest automatically finds tests using these patterns:

### Test files

- `test_*.py` is collected, for example `test_auth.py`
- `*_test.py` is collected, for example `auth_test.py`
- `tests.py` is not collected, because it matches neither pattern

### Test functions

- `test_*()` is collected, for example `test_login()`
- `check_login()` is not collected, because it doesn't start with `test_`

### Test classes

- `Test*` is collected, for example `TestUserAuth`
- `AuthTests` is not collected, because it doesn't start with `Test`

These are pytest's defaults, and rustest reads the same `python_files`, `python_classes`
and `python_functions` ini settings if your project overrides them.

::: {.callout-tip title="Be Consistent"}
Pick one style (`test_*.py` or `*_test.py`) and stick with it across your project.
:::

## Grouping tests with marks

**Marks** let you categorize and filter tests:

<!--rustest.mark.skip-->
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

### Running specific marks

```bash
# Run only unit tests
rustest -m "unit"

# Skip slow tests
rustest -m "not slow"

# Run integration or slow tests
rustest -m "integration or slow"
```

### Common marks

<!--rustest.mark.skip-->
```python
@mark.unit          # Fast, isolated unit tests
@mark.integration   # Tests that touch databases, APIs, etc.
@mark.slow          # Tests that take time
@mark.smoke         # Critical tests to run first
@mark.regression    # Tests for previously fixed bugs
```

## Test classes

Group related tests in classes:

```python
from rustest import raises
from types import SimpleNamespace

class AuthError(Exception):
    pass

def login(email, password):
    if password != "password":
        raise AuthError("wrong password")
    return SimpleNamespace(email=email, is_logged_in=True)

def logout(user):
    user.is_logged_in = False

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

### Benefits of test classes

- **Logical grouping.** Related tests stay together
- **Shared setup.** A fixture defined in the class is available to every test in it
- **Clearer output.** The class name is part of every node id
- **Easier navigation.** One place to look for one area of behavior

### Sharing setup in classes

```python
from rustest import fixture

class ShoppingCart:
    def __init__(self):
        self.lines = []

    def add_item(self, name, price):
        self.lines.append((name, price))

    def remove_item(self, name):
        self.lines = [line for line in self.lines if line[0] != name]

    @property
    def total(self):
        return sum(price for _, price in self.lines)

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

## Sharing fixtures with conftest.py

For fixtures used across multiple test files, use `conftest.py`:

```
tests/
├── conftest.py         # Shared fixtures
├── test_users.py
├── test_posts.py
└── test_comments.py
```

**`conftest.py`:**

<!--rustest.mark.skip-->
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

<!--rustest.mark.skip-->
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

## Separating test types

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

## Running tests efficiently

### Run only changed tests

Use `--lf` (last failed) to rerun failed tests:

```bash
rustest --lf
```

Use `--ff` (failed first) to run failed tests first, then all others:

```bash
rustest --ff
```

### Filter by name

Run tests matching a pattern:

```bash
# Run all login tests
rustest -k "login"

# Run tests with "user" or "auth" in the name
rustest -k "user or auth"

# Exclude anything matching "slow"
rustest -k "not slow"
```

`-k` matches a case-insensitive substring against the test's file name, class name and
function name (parameter id included), and also against the names of any marks it carries.
So `-k "not slow"` drops both a test called `test_slow_import` and a test carrying
`@mark.slow`. When you mean the mark and only the mark, use `-m "not slow"`.

### Stop on first failure

Fail fast for quick debugging:

```bash
rustest -x  # Exit after first failure
```

### Combine options

```bash
# Run failed tests first, stop on first new failure
rustest --ff -x

# Run unit tests, skip slow ones
rustest tests/unit/ -m "not slow"
```

## Real-world project structure

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

## Best practices

### Keep tests fast

Fast tests get run. Slow ones get skipped, then ignored, then deleted. Aim to keep unit
tests under 100ms each:

<!--rustest.mark.skip-->
```python
from rustest import mark

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

### Name tests descriptively

```python
# ❌ BAD
def test_1():
    ...

# ✅ GOOD
def test_login_fails_with_invalid_password():
    ...
```

### One assert per test (usually)

Focus each test on one behavior:

```python
from types import SimpleNamespace

def signup(email, password):
    if "@" not in email:
        raise ValueError("Invalid email format")
    return SimpleNamespace(email=email, name="Alice", is_active=True)

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

### Test edge cases

Don't just test the happy path:

```python
from rustest import approx, raises

def divide(a, b):
    return a / b

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

## What's next?

That is the end of the beginner track. Across the six pages you have seen why automated
testing pays for itself, how to write and run a test, the fundamentals (arrange-act-assert,
assertions, edge cases), fixtures, parametrization, and how to lay out a suite that has
outgrown one file.

The reference documentation picks up where this leaves off:

[Core Testing Guide](writing-tests.md)

Or go straight to a topic:

- [Marks & Filtering](marks.md), for advanced mark usage
- [Test Classes](test-classes.md), for class-based testing patterns
- [CLI Usage](cli.md), for every command-line option
- [Fixtures](fixtures.md), for scopes, autouse and the rest
