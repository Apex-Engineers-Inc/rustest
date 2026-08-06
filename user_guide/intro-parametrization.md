# Testing multiple cases with parametrization

Often you want to test the same logic with different inputs. Parametrization lets you do this efficiently without writing repetitive tests.

## The problem: repetitive tests

Imagine testing an `add()` function:

```python
def add(a, b):
    return a + b

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

Five function definitions for one behavior. The only thing that changes is the numbers.

## The solution: parametrization

**Parametrization** lets you run the same test with different inputs:

```python
from rustest import parametrize

def add(a, b):
    return a + b

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

**This one test runs 5 times** with different inputs.

## How it works

```python
from rustest import parametrize

def add(a, b):
    return a + b

@parametrize("a,b,expected", [
    (1, 2, 3),
    (10, 20, 30),
])
def test_add(a, b, expected):
    result = add(a, b)
    assert result == expected
```

The three pieces:

1. **`"a,b,expected"`** are the names of the parameters, matching the function arguments
2. **`[(1, 2, 3), (10, 20, 30)]`** is the list of value tuples
3. **`test_add(a, b, expected)`** receives one tuple per run

For each tuple, rustest:
- Assigns values to `a`, `b`, and `expected`
- Runs the test function
- Reports pass/fail separately

When you run this:

```
2 passed in 0.32s
```

Two tests, not one: each parameter set is counted, run and reported separately. Add `-v`
to see the ids rustest generated from the values:

```
test_add.py::test_add[1-2-3] PASSED                                     [ 50%]
test_add.py::test_add[10-20-30] PASSED                                  [100%]

2 passed in 0.32s
```

If one set fails, only that set goes red. The others still run and still report.

## Real-world examples

### Testing email validation

```python
import re
from rustest import parametrize

def is_valid_email(address):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address))

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

### Testing password strength

```python
from rustest import parametrize

def check_password_strength(password):
    if len(password) >= 12:
        return "strong"
    has_digit = any(c.isdigit() for c in password)
    has_upper = any(c.isupper() for c in password)
    if len(password) >= 8 and has_digit and has_upper:
        return "medium"
    return "weak"

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

### Testing edge cases

```python
from rustest import parametrize

def sum_list(values):
    return sum(values)

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

## Parametrize multiple parameters

Test combinations of inputs:

```python
from rustest import parametrize

class Rectangle:
    def __init__(self, width, height):
        self.width = width
        self.height = height

    def area(self):
        return self.width * self.height

@parametrize("width,height,expected_area", [
    (10, 20, 200),
    (5, 5, 25),
    (1, 100, 100),
])
def test_rectangle_area(width, height, expected_area):
    rect = Rectangle(width, height)
    assert rect.area() == expected_area
```

## Testing for errors

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

## Making tests easier to read

Values make serviceable ids, but names make better ones. Pass `ids=`:

```python
from types import SimpleNamespace
from rustest import parametrize

def login(username, password):
    correct = username == "alice" and password == "correct_password"
    return SimpleNamespace(success=correct)

@parametrize("username,password,should_succeed", [
    ("alice", "correct_password", True),
    ("alice", "wrong_password", False),
    ("unknown_user", "any_password", False),
], ids=["valid_credentials", "wrong_password", "unknown_user"])
def test_login(username, password, should_succeed):
    result = login(username, password)
    assert result.success is should_succeed
```

Each case now reports under the name you gave it:

```
test_login.py::test_login[valid_credentials] PASSED                     [ 33%]
test_login.py::test_login[wrong_password] PASSED                        [ 66%]
test_login.py::test_login[unknown_user] PASSED                          [100%]
```

## Combining parametrization with fixtures

You can use both together:

```python
from types import SimpleNamespace
from rustest import fixture, parametrize

class Database:
    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def create_user(self, name, email):
        return SimpleNamespace(name=name, email=email)

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

The fixture runs for **each** parameter set.

## When to use parametrization

Use parametrization when you:

- Test the same logic with different inputs
- Want to test many edge cases
- Need to verify similar behaviors with different data

Skip it when:

- Tests have different logic (use separate tests)
- You're only testing one or two cases (regular tests are simpler)

## Common patterns

### Testing string transformations

```python
from rustest import parametrize

def to_uppercase(text):
    return text.upper()

@parametrize("input,expected", [
    ("hello", "HELLO"),
    ("world", "WORLD"),
    ("MiXeD", "MIXED"),
    ("", ""),  # Edge case: empty string
])
def test_to_uppercase(input, expected):
    assert to_uppercase(input) == expected
```

### Testing number ranges

```python
from rustest import parametrize

def is_valid_age(age):
    return 18 <= age <= 120

@parametrize("age", [18, 21, 30, 65, 100])
def test_valid_ages(age):
    assert is_valid_age(age) is True

@parametrize("age", [-1, 0, 17, 150])
def test_invalid_ages(age):
    assert is_valid_age(age) is False
```

### Testing different data structures

```python
from rustest import parametrize

@parametrize("data", [
    [1, 2, 3],          # List
    (1, 2, 3),          # Tuple
    {1, 2, 3},          # Set
])
def test_sum_iterables(data):
    assert sum(data) == 6
```

## Debugging parametrized tests

When a parametrized test fails, rustest shows you which case failed:

```
================================== FAILURES ===================================
_______________________________ test_add[5-3-7] _______________________________
Traceback (most recent call last):
  File "/path/to/test_math.py", line 5, in test_add
    assert a + b == expected
AssertionError: assert (5 + 3) == 7
=========================== short test summary info ===========================
FAILED test_math.py::test_add[5-3-7]

1 failed, 3 passed in 0.43s
```

The failing case names itself. `test_add[5-3-7]` carries the parameter values in the
heading and again in the `short test summary info` line, so you never have to count which
case was the third one.

To re-run just that case while you fix it, select it with `-k`:

```bash
rustest test_math.py -k "5-3-7"
```

## What's next?

Structure a suite that has grown past a single file:

[Organizing Your Tests](intro-organizing.md)

For custom ids, indirect parametrization and stacked decorators:

[Parametrization Guide](parametrization.md)
