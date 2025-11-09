# Parametrization

Parametrization allows you to run the same test with different input values, reducing code duplication and making your tests more comprehensive.

## Basic Parametrization

Use the `@parametrize` decorator to run a test multiple times with different arguments:

```python
from rustest import parametrize

@parametrize("input,expected", [
    (1, 2),
    (2, 4),
    (3, 6),
])
def test_double(input: int, expected: int) -> None:
    assert input * 2 == expected
```

This creates three separate test cases:

```
  PASSED   0.001s test_double[case_0]
  PASSED   0.001s test_double[case_1]
  PASSED   0.001s test_double[case_2]
```

## Parameter Formats

### Comma-Separated String

You can specify parameter names as a comma-separated string:

```python
from rustest import parametrize

@parametrize("x,y,expected", [
    (1, 1, 2),
    (2, 3, 5),
    (10, 5, 15),
])
def test_addition(x: int, y: int, expected: int) -> None:
    assert x + y == expected
```

### List of Strings

Or as a list of strings:

```python
from rustest import parametrize

@parametrize(["x", "y", "expected"], [
    (1, 1, 2),
    (2, 3, 5),
    (10, 5, 15),
])
def test_addition(x: int, y: int, expected: int) -> None:
    assert x + y == expected
```

## Single Parameter

For a single parameter, pass values directly:

```python
from rustest import parametrize

@parametrize("value", [1, 2, 3, 4, 5])
def test_is_positive(value: int) -> None:
    assert value > 0
```

Or as tuples if you prefer consistency:

```python
from rustest import parametrize

@parametrize("value", [(1,), (2,), (3,)])
def test_is_positive(value: int) -> None:
    assert value > 0
```

## Custom Test IDs

Provide custom IDs to make test output more readable:

```python
from rustest import parametrize

@parametrize("value,expected", [
    (2, 4),
    (3, 9),
    (4, 16),
], ids=["two", "three", "four"])
def test_square(value: int, expected: int) -> None:
    assert value ** 2 == expected
```

Output:

```
  PASSED   0.001s test_square[two]
  PASSED   0.001s test_square[three]
  PASSED   0.001s test_square[four]
```

### Descriptive IDs

Use descriptive IDs for complex test cases:

```python
from rustest import parametrize

@parametrize("operation,a,b,expected", [
    ("add", 2, 3, 5),
    ("subtract", 5, 3, 2),
    ("multiply", 4, 3, 12),
    ("divide", 10, 2, 5),
], ids=["addition", "subtraction", "multiplication", "division"])
def test_calculator(operation: str, a: int, b: int, expected: int) -> None:
    if operation == "add":
        assert a + b == expected
    elif operation == "subtract":
        assert a - b == expected
    elif operation == "multiply":
        assert a * b == expected
    elif operation == "divide":
        assert a / b == expected
```

## Parametrizing with Fixtures

Combine parametrized tests with fixtures:

```python
from rustest import fixture, parametrize

@fixture
def multiplier() -> int:
    return 10

@parametrize("value,expected", [
    (1, 10),
    (2, 20),
    (3, 30),
])
def test_multiply(multiplier: int, value: int, expected: int) -> None:
    assert multiplier * value == expected
```

## Complex Parameter Values

### Using Dictionaries

Pass dictionaries as parameter values:

```python
from rustest import parametrize

@parametrize("user", [
    {"name": "Alice", "age": 30},
    {"name": "Bob", "age": 25},
    {"name": "Charlie", "age": 35},
], ids=["alice", "bob", "charlie"])
def test_user_valid(user: dict) -> None:
    assert "name" in user
    assert user["age"] > 0
```

### Using Objects

```python
from dataclasses import dataclass
from rustest import parametrize

@dataclass
class User:
    name: str
    email: str

@parametrize("user", [
    User("Alice", "alice@example.com"),
    User("Bob", "bob@example.com"),
], ids=["alice", "bob"])
def test_user_email(user: User) -> None:
    assert "@" in user.email
```

### Using Lists

```python
from rustest import parametrize

@parametrize("numbers", [
    [1, 2, 3],
    [10, 20, 30],
    [100, 200, 300],
])
def test_sum_positive(numbers: list) -> None:
    assert sum(numbers) > 0
```

## Multiple Parametrize Decorators

You can stack `@parametrize` decorators to test all combinations:

```python
from rustest import parametrize

@parametrize("x", [1, 2])
@parametrize("y", [3, 4])
def test_combinations(x: int, y: int) -> None:
    assert x < y
```

This creates 4 test cases:
- `test_combinations[case_0-case_0]` (x=1, y=3)
- `test_combinations[case_0-case_1]` (x=1, y=4)
- `test_combinations[case_1-case_0]` (x=2, y=3)
- `test_combinations[case_1-case_1]` (x=2, y=4)

## Parametrizing Test Classes

Apply parametrization to all methods in a test class:

```python
from rustest import parametrize

@parametrize("value", [1, 2, 3])
class TestNumber:
    def test_positive(self, value: int) -> None:
        assert value > 0

    def test_less_than_ten(self, value: int) -> None:
        assert value < 10
```

This runs both tests for each value (6 total tests).

## Real-World Examples

### Testing Edge Cases

```python
from rustest import parametrize

@parametrize("text,expected", [
    ("", 0),                    # Empty string
    ("a", 1),                   # Single character
    ("hello", 5),               # Normal case
    ("hello world", 11),        # With space
    ("🎉", 1),                  # Unicode emoji
], ids=["empty", "single", "normal", "with_space", "emoji"])
def test_string_length(text: str, expected: int) -> None:
    assert len(text) == expected
```

### Testing Multiple Data Types

```python
from rustest import parametrize

@parametrize("value,expected_type", [
    (42, int),
    (3.14, float),
    ("hello", str),
    ([1, 2, 3], list),
    ({"key": "value"}, dict),
], ids=["int", "float", "str", "list", "dict"])
def test_type_checking(value, expected_type):
    assert isinstance(value, expected_type)
```

### Testing Error Conditions

```python
from rustest import parametrize, raises

@parametrize("invalid_input,error_type", [
    ("abc", ValueError),
    ("", ValueError),
    (None, TypeError),
], ids=["non_numeric", "empty", "none"])
def test_invalid_conversion(invalid_input, error_type):
    with raises(error_type):
        int(invalid_input)
```

### Testing API Responses

```python
from rustest import parametrize

class MockResponse:
    def __init__(self, status_code):
        self.status_code = status_code

class MockAPIClient:
    def get(self, endpoint):
        if endpoint.startswith("/api/") and endpoint != "/api/invalid":
            return MockResponse(200)
        return MockResponse(404)

@parametrize("endpoint,expected_status", [
    ("/api/users", 200),
    ("/api/posts", 200),
    ("/api/invalid", 404),
], ids=["users", "posts", "not_found"])
def test_api_endpoints(endpoint: str, expected_status: int):
    api_client = MockAPIClient()
    response = api_client.get(endpoint)
    assert response.status_code == expected_status
```

## Best Practices

### Use Meaningful IDs

```python
from rustest import parametrize

def is_adult(age: int) -> bool:
    return age >= 18

# Good - clear what's being tested
@parametrize("age,valid", [
    (17, False),
    (18, True),
    (65, True),
], ids=["underage", "adult", "senior"])
def test_age_validation(age: int, valid: bool):
    assert is_adult(age) == valid

# Less clear
@parametrize("age,valid", [
    (17, False),
    (18, True),
    (65, True),
])
def test_age_validation(age: int, valid: bool):
    assert is_adult(age) == valid
```

### Keep Test Cases Focused

```python
from rustest import parametrize

# Good - focused test cases
@parametrize("value", [1, 2, 3, 100, 1000])
def test_positive_numbers(value: int):
    assert value > 0

@parametrize("value", [-1, -10, -100])
def test_negative_numbers(value: int):
    assert value < 0

# Less ideal - mixing concerns
@parametrize("value,expected", [
    (1, "positive"),
    (-1, "negative"),
    (100, "positive"),
    (-100, "negative"),
])
def test_number_sign(value: int, expected: str):
    # Test logic becomes complex
    if expected == "positive":
        assert value > 0
    else:
        assert value < 0
```

### Document Complex Parameters

```python
from rustest import parametrize

class ConfigResult:
    def __init__(self, cache_status: str):
        self.cache_status = cache_status

def run_with_config(config: dict) -> ConfigResult:
    if config.get("mock"):
        return ConfigResult("mocked")
    elif config.get("cache"):
        return ConfigResult("cached")
    else:
        return ConfigResult("uncached")

@parametrize("config,expected_result", [
    # Production config with caching enabled
    ({"env": "prod", "cache": True}, "cached"),
    # Development config without caching
    ({"env": "dev", "cache": False}, "uncached"),
    # Test config with mock cache
    ({"env": "test", "cache": True, "mock": True}, "mocked"),
], ids=["production", "development", "testing"])
def test_environment_behavior(config: dict, expected_result: str):
    result = run_with_config(config)
    assert result.cache_status == expected_result
```

## Next Steps

- [Fixtures](fixtures.md) - Combine fixtures with parametrization
- [Marks & Skipping](marks.md) - Mark parametrized tests
- [Test Classes](test-classes.md) - Parametrize test classes
