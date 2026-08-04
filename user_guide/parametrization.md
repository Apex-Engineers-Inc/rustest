# Parametrization

Parametrization runs one test body against many inputs. Each input becomes a separate test
with its own node id, so a failure names the case that failed instead of the loop that
contained it.

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

This collects three tests. Under `-v`:

```
test_double.py::test_double[1-2] PASSED                                 [ 33%]
test_double.py::test_double[2-4] PASSED                                 [ 66%]
test_double.py::test_double[3-6] PASSED                                 [100%]
```

## How IDs Are Generated

The part in brackets is pytest's, generated the same way and byte-identical to what pytest
produces for the same values. One component per argument name, joined with `-`:

- Strings, numbers, booleans, `None`, complex numbers, regex patterns, and enums are spelled
  by their own value. Non-printable characters in a string are escaped.
- Anything with a `__name__`, such as a class, a function, or a module, uses that name.
- Everything else, including lists, dicts, and instances of your own classes, has no id of
  its own and falls back to `<argname><index>`. That is why
  `@parametrize("numbers", [[1, 2, 3], [10, 20, 30]])` collects `numbers0` and `numbers1`.

Pass `ids=` when the generated form is unreadable; see [Custom Test IDs](#custom-test-ids).

## Parameter Formats

### Comma-Separated String

Names in one string, split on commas. This is the form most pytest suites use:

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

One name per element. For two or more names this is equivalent to the string form; for
exactly one name the two forms differ, as the next section describes:

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

With one name, values are passed through as they are:

```python
from rustest import parametrize

@parametrize("value", [1, 2, 3, 4, 5])
def test_is_positive(value: int) -> None:
    assert value > 0
```

Wrapping each value in a one-tuple does **not** unpack it here.
`@parametrize("value", [(1,), (2,)])` binds `value` to the tuple `(1,)`, with the ids
`value0` and `value1`. That is pytest's rule: a `str` `argnames` naming a single parameter
makes each argvalue the whole value, however it is written.

A **sequence** `argnames` does unpack, so the same tuples are read as one-column rows:

```python
from rustest import parametrize

@parametrize(["value"], [(1,), (2,), (3,)])
def test_is_positive_unpacked(value: int) -> None:
    assert value > 0
```

## Custom Test IDs

`ids=` takes one string per case and replaces the generated id entirely. The list must be
the same length as the value list:

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

Under `-v`:

```
test_square.py::test_square[two] PASSED                                 [ 33%]
test_square.py::test_square[three] PASSED                               [ 66%]
test_square.py::test_square[four] PASSED                                [100%]
```

`ids=` also accepts a callable. It is called once per *individual value*, not once per row,
and the results are joined with `-`; a value the callable answers `None` for falls back to
that component's generated id.

```python
from rustest import parametrize

@parametrize("value,expected", [(2, 4), (3, 9)], ids=lambda v: f"n{v}")
def test_square_callable_ids(value: int, expected: int) -> None:
    assert value ** 2 == expected
```

That collects `test_square_callable_ids[n2-n4]` and `[n3-n9]`.

### Descriptive IDs

Names carry further than positions once a case list gets long:

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

Parameters and fixtures are resolved independently, so a test can request both. Names that
are not parametrized are looked up in the fixture registry as usual:

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

## Indirect Parametrization

`indirect=` routes a parametrized value **through a fixture of the same name** instead of
handing it straight to the test. The fixture reads it as `request.param`; the test receives
whatever the fixture returns. Node ids are generated from the parameter either way, so
making a name indirect never changes a test id.

This is pytest's meaning, ported in full
(`_pytest/python.py::Metafunc._resolve_args_directness`).

!!! warning "This changed in 1.0"
    rustest used to read an indirect value as *the name of a fixture to resolve*. That was a
    rustest-only reading that borrowed pytest's keyword, so a suite written for pytest got
    the wrong value. Rewrite `@parametrize("data", ["fixture_a"], indirect=True)` as a
    fixture that reads `request.param`. The recipes below show how, including the
    `request.getfixturevalue(request.param)` form that reproduces the old behaviour when you
    really do want to select a fixture by name.

### Using `indirect` with a List

Name the parameters to route; the rest stay direct:

```python
from rustest import fixture, parametrize

@fixture
def scaled(request):
    return request.param * 10

@parametrize("scaled, expected", [
    (2, 20),
    (3, 30),
], indirect=["scaled"])
def test_with_indirect(scaled: int, expected: int) -> None:
    # `scaled` came through the fixture; `expected` is a direct value.
    assert scaled == expected
```

### Using `indirect=True`

Route every parametrized name:

```python
from rustest import fixture, parametrize

@fixture
def dataset(request):
    return [value * request.param for value in (1, 2, 3)]

@parametrize("dataset", [1, 10], indirect=True)
def test_all_positive(dataset: list) -> None:
    assert all(x > 0 for x in dataset)
```

### Selecting a Fixture by Name

The old behaviour, written the way pytest writes it: one fixture that resolves the name it
is handed.

```python
from rustest import fixture, parametrize

@fixture
def dataset_a():
    return [1, 2, 3]

@fixture
def dataset_b():
    return [4, 5, 6]

@fixture
def chosen(request):
    return request.getfixturevalue(request.param)

@parametrize("chosen", ["dataset_a", "dataset_b"], indirect=True)
def test_all_positive(chosen: list) -> None:
    assert all(x > 0 for x in chosen)
```

!!! note "`indirect="name"` is not a shorthand"
    A `str` is a `Sequence`, so pytest iterates it character by character and
    `indirect="config"` fails with `indirect fixture 'c' doesn't exist`. rustest reproduces
    that. Pass `["config"]` or `True`.

### Why Use Indirect Parametrization?

- **Complex setup per parameter**: the fixture can do work, and teardown, for each value
- **Wider scopes**: a module-scoped fixture parametrized indirectly is built once per value
- **Reuse**: the same fixture serves tests that do not parametrize it at all

## Complex Parameter Values

Any Python object can be a parameter value. Containers and instances have no id of their
own, so the generated ids for the three sections below are `user0`, `user1`, `numbers0` and
so on; that is why the first two pass `ids=`.

### Using Dictionaries

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

Stacked decorators produce the cross product of their value lists:

```python
from rustest import parametrize

@parametrize("x", [1, 2])
@parametrize("y", [3, 4])
def test_combinations(x: int, y: int) -> None:
    assert x < y
```

This collects 4 tests. Decorators apply bottom-up, so the `y` values vary slowest and lead
the id:

- `test_combinations[3-1]` (x=1, y=3)
- `test_combinations[3-2]` (x=2, y=3)
- `test_combinations[4-1]` (x=1, y=4)
- `test_combinations[4-2]` (x=2, y=4)

## Parametrizing Test Classes

A `@parametrize` on the class applies to every test method it contains, and each method
takes the parameter as an argument:

```python
from rustest import parametrize

@parametrize("value", [1, 2, 3])
class TestNumber:
    def test_positive(self, value: int) -> None:
        assert value > 0

    def test_less_than_ten(self, value: int) -> None:
        assert value < 10
```

That is six tests: `TestNumber::test_positive[1]` through `TestNumber::test_less_than_ten[3]`.
A method that carries its own `@parametrize` gets the cross product of the two.

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

Without `ids=`, the cases below collect as `17-False`, `18-True`, and `65-True`. Those say
what the values are but not what each one is testing:

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

- [Fixtures](intro-fixtures.md) - Combine fixtures with parametrization
- [Marks & Skipping](marks.md) - Mark parametrized tests
- [Test Classes](test-classes.md) - Parametrize test classes
