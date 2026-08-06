# Testing basics

You have a passing test. These are the ideas underneath it: how a test is shaped, what you
can assert, and what separates a test you'll thank yourself for from one you'll delete.

## The anatomy of a test

Every test follows a simple pattern called **Arrange-Act-Assert** (AAA):

```python
from types import SimpleNamespace

def signup(email, password):
    if "@" not in email:
        raise ValueError("Invalid email format")
    return SimpleNamespace(email=email, name="Alice", is_active=True)

def test_user_signup():
    # ARRANGE: Set up the test data
    email = "alice@example.com"
    password = "secure_password"

    # ACT: Do the thing you're testing
    user = signup(email, password)

    # ASSERT: Check the results
    assert user.email == email
    assert user.is_active is True
```

Three steps, in order:

### 1. Arrange (setup)

Prepare everything you need for the test:

```python
# ARRANGE
email = "alice@example.com"
password = "secure_password"
```

This might include:
- Creating test data
- Setting up database connections
- Configuring mock objects
- Preparing input values

### 2. Act (execute)

Run the code you're testing:

<!--rustest.mark.skip-->
```python
# ACT
user = signup(email, password)
```

This is usually **one line**: the specific function or method you're testing.

### 3. Assert (verify)

Check that the results are correct:

<!--rustest.mark.skip-->
```python
# ASSERT
assert user.email == email
assert user.is_active is True
```

If all assertions pass, the test succeeds. If any fail, the test fails and shows you why.

::: {.callout-tip title="Keep it simple"}
Each test should focus on **one specific behavior**. If you're testing too many things, split it into multiple tests.
:::

## Types of assertions

Assertions are how you verify correctness. Here are the most common patterns:

### Equality checks

```python
def test_calculations():
    assert 2 + 2 == 4
    assert "hello".upper() == "HELLO"
    assert [1, 2, 3] == [1, 2, 3]
```

### Boolean checks

```python
from types import SimpleNamespace

def test_boolean_conditions():
    user = SimpleNamespace(is_admin=True)
    result = "something"

    assert True
    assert not False
    assert user.is_admin is True
    assert result is not None
```

### Membership checks

```python
def test_membership():
    user_dict = {"name": "Alice", "email": "alice@example.com"}

    assert "hello" in "hello world"
    assert 5 in [1, 2, 3, 4, 5]
    assert "name" in user_dict
```

### Comparison checks

```python
def test_comparisons():
    age = 21
    price = 49.99
    items = ["apple", "banana"]

    assert age >= 18
    assert price < 100
    assert len(items) > 0
```

### Type checks

```python
class User:
    pass

def test_types():
    result = 42
    user = User()
    data = {"name": "Alice"}

    assert isinstance(result, int)
    assert isinstance(user, User)
    assert type(data) is dict
```

## Testing for errors

Sometimes you *want* your code to raise an error. Use `raises()`:

```python
from types import SimpleNamespace
from rustest import raises

def signup(email, password):
    if "@" not in email:
        raise ValueError("Invalid email format")
    return SimpleNamespace(email=email, name="Alice", is_active=True)

def test_invalid_email():
    with raises(ValueError):
        signup("not-an-email", "password")
```

This test **passes** if `ValueError` is raised. If no error occurs, or a different error occurs, the test fails.

You can also check the error message:

```python
from types import SimpleNamespace
from rustest import raises

def signup(email, password):
    if "@" not in email:
        raise ValueError("Invalid email format")
    return SimpleNamespace(email=email, name="Alice", is_active=True)

def test_error_message():
    with raises(ValueError, match="Invalid email format"):
        signup("not-an-email", "password")
```

The test only passes if:
1. A `ValueError` is raised
2. The error message contains "Invalid email format"

## Numeric comparisons with tolerance

Floating point math is imprecise. Use `approx()` for tolerant comparisons:

```python
from rustest import approx

def test_floating_point():
    result = 0.1 + 0.2
    assert result == approx(0.3)  # Works!
```

You can specify the tolerance:

```python
from rustest import approx

def test_with_tolerance():
    result = 10.1
    assert result == approx(10, abs=0.2)  # Within ±0.2
```

This works with:
- Single numbers: `approx(3.14)`
- Lists: `approx([1.1, 2.2, 3.3])`
- Dictionaries: `approx({"x": 1.1, "y": 2.2})`
- Complex numbers: `approx(1.1 + 2.2j)`

## What makes a good test?

### Independent

Each test should run independently. One test shouldn't depend on another:

```python
from types import SimpleNamespace

def signup(email, password):
    if "@" not in email:
        raise ValueError("Invalid email format")
    return SimpleNamespace(email=email, name="Alice", is_active=True)

def login(user):
    return SimpleNamespace(success=user is not None)

# ❌ BAD: Tests depend on each other
user = None

def test_create_user():
    global user
    user = signup("alice@example.com", "password")
    assert user is not None

def test_user_login():
    # This fails if test_create_user didn't run first!
    result = login(user)
    assert result.success
```

```python
from types import SimpleNamespace

def signup(email, password):
    if "@" not in email:
        raise ValueError("Invalid email format")
    return SimpleNamespace(email=email, name="Alice", is_active=True)

def login(user):
    return SimpleNamespace(success=user is not None)

# ✅ GOOD: Each test is independent
def test_create_user():
    user = signup("alice@example.com", "password")
    assert user is not None

def test_user_login():
    # Set up everything we need
    user = signup("alice@example.com", "password")
    result = login(user)
    assert result.success
```

### Fast

Tests should run quickly so you can run them often:

<!--rustest.mark.skip-->
```python
# ❌ BAD: Slow test
def test_slow_operation():
    time.sleep(5)  # Don't do this!
    assert calculate() == 42

# ✅ GOOD: Fast test
def test_fast_operation():
    result = calculate()  # Should be instant
    assert result == 42
```

If you must have slow tests (like API calls), mark them so you can skip them:

<!--rustest.mark.skip-->
```python
from rustest import mark

@mark.slow
def test_external_api():
    response = call_external_api()
    assert response.status == 200
```

Then run fast tests only:

```bash
rustest -m "not slow"
```

Marks get a fuller treatment in [Organizing Your Tests](intro-organizing.md).

### Readable

Someone else, or you in six months, should understand what the test does:

```python
def f(a, b):
    return a + b

def add(a, b):
    return a + b

# ❌ BAD: Unclear test
def test_x():
    a = f(1, 2)
    assert a == 3

# ✅ GOOD: Clear test
def test_add_function_sums_two_numbers():
    result = add(1, 2)
    assert result == 3
```

Good test names answer: **"What does this test verify?"**

### Focused

Test one thing at a time:

```python
from types import SimpleNamespace

def signup(email, password):
    if "@" not in email:
        raise ValueError("Invalid email format")
    return SimpleNamespace(email=email, name="Alice", is_active=True)

def login(user):
    return SimpleNamespace(success=user is not None)

def get_profile(user):
    return user

# ❌ BAD: Testing too much
def test_user_operations():
    user = signup("alice@example.com", "password")
    assert user is not None

    login_result = login(user)
    assert login_result.success

    profile = get_profile(user)
    assert profile.name == "Alice"

# ✅ GOOD: Separate focused tests
def test_signup_creates_user():
    user = signup("alice@example.com", "password")
    assert user is not None

def test_login_succeeds_with_valid_credentials():
    user = signup("alice@example.com", "password")
    result = login(user)
    assert result.success

def test_profile_shows_user_name():
    user = signup("alice@example.com", "password")
    profile = get_profile(user)
    assert profile.name == "Alice"
```

When one test fails, you immediately know *what* broke.

## Test organization strategies

### Group related tests

<!--rustest.mark.skip-->
```python
# test_user_auth.py
def test_signup_with_valid_email():
    # ...

def test_signup_with_invalid_email():
    # ...

def test_login_with_correct_password():
    # ...

def test_login_with_wrong_password():
    # ...
```

### Use descriptive file names

```
tests/
├── test_authentication.py  # All auth-related tests
├── test_database.py        # Database tests
├── test_api.py            # API endpoint tests
└── test_utils.py          # Utility function tests
```

### Test edge cases

Don't just test the happy path:

```python
def add(a, b):
    return a + b

def test_add_positive_numbers():
    assert add(2, 3) == 5

def test_add_negative_numbers():
    assert add(-2, -3) == -5

def test_add_zero():
    assert add(0, 5) == 5
    assert add(5, 0) == 5

def test_add_large_numbers():
    assert add(1_000_000, 2_000_000) == 3_000_000
```

Think about:
- Empty inputs (`[]`, `""`, `None`)
- Zero and negative numbers
- Very large values
- Invalid inputs
- Boundary conditions

## Running and filtering tests

Run all tests:

```bash
rustest
```

Run specific tests:

```bash
# Run one file
rustest tests/test_auth.py

# Run tests matching a pattern
rustest -k "login"  # Runs all tests with "login" in the name

# Run tests in a directory
rustest tests/unit/
```

See detailed output:

```bash
rustest -v  # Verbose mode shows each test name
```

## What's next?

### Make tests reusable

[Learn About Fixtures](intro-fixtures.md)

Fixtures let you reuse setup code across multiple tests. Instead of copying the same setup everywhere, define it once and use it everywhere.

### Test multiple cases efficiently

[Learn About Parametrization](intro-parametrization.md)

Test the same logic with different inputs without writing repetitive tests.

### Organize larger test suites

[Organizing Your Tests](intro-organizing.md)

Structure tests for real projects with marks, test classes, and shared `conftest.py` fixtures.
