# Testing Multiple Cases with Parametrization

Often you want to test the same logic with different inputs. Parametrization lets you do this efficiently without writing repetitive tests.

## The Problem: Repetitive Tests

Imagine testing an `add()` function:

```python
def test_add_small_numbers():
    assert add(1, 2) == 3

def test_add_large_numbers():
    assert add(100, 200) == 300

def test_add_negative_numbers():
    assert add(-5, -10) == -15

def test_add_mixed_numbers():
    assert add(-5, 10) == 5

def test_add_with_zero():
    assert add(0, 5) == 5
```

This works, but it's repetitive. Every test does the same thing with different numbers.

## The Solution: Parametrization

**Parametrization** lets you run the same test with different inputs:

```python
from rustest import parametrize

@parametrize("a,b,expected", [
    (1, 2, 3),
    (100, 200, 300),
    (-5, -10, -15),
    (-5, 10, 5),
    (0, 5, 5),
])
def test_add(a, b, expected):
    result = add(a, b)
    assert result == expected
```

**This one test runs 5 times** with different inputs! Much cleaner.

## How It Works

```python
@parametrize("a,b,expected", [
    (1, 2, 3),
    (10, 20, 30),
])
def test_add(a, b, expected):
    result = add(a, b)
    assert result == expected
```

Breaking this down:

1. **`"a,b,expected"`** — Names of the parameters (matches function arguments)
2. **`[(1, 2, 3), (10, 20, 30)]`** — List of value tuples
3. **`test_add(a, b, expected)`** — Function receives these parameters

For each tuple, rustest:
- Assigns values to `a`, `b`, and `expected`
- Runs the test function
- Reports pass/fail separately

When you run this:

```
✓✓

✓ 2/2 2 passing (1ms)
```

Each `✓` represents one parameter set!

## Real-World Examples

### Testing Email Validation

```python
@parametrize("email", [
    "alice@example.com",
    "bob.smith@company.org",
    "user+tag@domain.co.uk",
])
def test_valid_emails(email):
    assert is_valid_email(email) is True

@parametrize("email", [
    "not-an-email",
    "@example.com",
    "user@",
    "user @example.com",  # Space before @
])
def test_invalid_emails(email):
    assert is_valid_email(email) is False
```

### Testing Password Strength

```python
from rustest import parametrize

@parametrize("password,expected_strength", [
    ("12345", "weak"),
    ("password", "weak"),
    ("Passw0rd", "medium"),
    ("MyP@ssw0rd123!", "strong"),
])
def test_password_strength(password, expected_strength):
    strength = check_password_strength(password)
    assert strength == expected_strength
```

### Testing Edge Cases

```python
@parametrize("input,expected", [
    ([], 0),           # Empty list
    ([1], 1),          # Single element
    ([1, 2, 3], 6),    # Multiple elements
    ([-1, -2], -3),    # Negative numbers
    ([1000000], 1000000),  # Large number
])
def test_sum_list(input, expected):
    result = sum_list(input)
    assert result == expected
```

## Parametrize Multiple Parameters

Test combinations of inputs:

```python
@parametrize("width,height,expected_area", [
    (10, 20, 200),
    (5, 5, 25),
    (1, 100, 100),
])
def test_rectangle_area(width, height, expected_area):
    rect = Rectangle(width, height)
    assert rect.area() == expected_area
```

## Testing for Errors

Parametrize expected errors too:

```python
from rustest import parametrize, raises

@parametrize("dividend,divisor", [
    (10, 0),
    (100, 0),
    (-5, 0),
])
def test_division_by_zero(dividend, divisor):
    with raises(ZeroDivisionError):
        result = dividend / divisor
```

## Making Tests Easier to Read

For complex tests, name your test cases:

```python
@parametrize("username,password,should_succeed", [
    ("alice", "correct_password", True),
    ("alice", "wrong_password", False),
    ("unknown_user", "any_password", False),
], ids=["valid_credentials", "wrong_password", "unknown_user"])
def test_login(username, password, should_succeed):
    result = login(username, password)
    assert result.success is should_succeed
```

With `ids`, your test output is much clearer:

```
✓ test_login[valid_credentials]
✗ test_login[wrong_password]
✓ test_login[unknown_user]
```

## Combining Parametrization with Fixtures

You can use both together:

```python
from rustest import fixture, parametrize

@fixture
def database():
    db = Database()
    db.connect()
    yield db
    db.disconnect()

@parametrize("name,email", [
    ("Alice", "alice@example.com"),
    ("Bob", "bob@example.com"),
])
def test_create_user(database, name, email):
    user = database.create_user(name, email)
    assert user.name == name
    assert user.email == email
```

The fixture runs for **each** parameter set!

## When to Use Parametrization

Use parametrization when you:

- ✅ Test the same logic with different inputs
- ✅ Want to test many edge cases
- ✅ Need to verify similar behaviors with different data

Don't use it when:

- ❌ Tests have different logic (use separate tests)
- ❌ You're only testing one or two cases (regular tests are simpler)

## Common Patterns

### Testing String Transformations

```python
@parametrize("input,expected", [
    ("hello", "HELLO"),
    ("world", "WORLD"),
    ("MiXeD", "MIXED"),
    ("", ""),  # Edge case: empty string
])
def test_to_uppercase(input, expected):
    assert to_uppercase(input) == expected
```

### Testing Number Ranges

```python
@parametrize("age", [18, 21, 30, 65, 100])
def test_valid_ages(age):
    assert is_valid_age(age) is True

@parametrize("age", [-1, 0, 17, 150])
def test_invalid_ages(age):
    assert is_valid_age(age) is False
```

### Testing Different Data Structures

```python
@parametrize("data", [
    [1, 2, 3],          # List
    (1, 2, 3),          # Tuple
    {1, 2, 3},          # Set
])
def test_sum_iterables(data):
    assert sum(data) == 6
```

## Debugging Parametrized Tests

When a parametrized test fails, rustest shows you which case failed:

```
✓✓✗✓

FAILURES
test_add[case_2] (test_math.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError
  Parameters: a=5, b=3, expected=7
  Expected: 7
  Received: 8

✗ 4/4 3 passing, 1 failed (2ms)
```

This makes it easy to identify and fix the specific failing case.

## What's Next?

Parametrization makes testing comprehensive without being tedious. Next, learn how to organize and structure your growing test suite:

[:octicons-arrow-right-24: Organizing Your Tests](organizing.md){ .md-button .md-button--primary }

Want to dive deeper into parametrization?

[:octicons-arrow-right-24: Advanced Parametrization Guide](../guide/parametrization.md){ .md-button }
