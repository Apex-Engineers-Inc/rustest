# Writing tests

Rustest follows pytest's conventions for test discovery and organization.

## Test discovery

Rustest discovers tests by looking for:

- Files named `test_*.py` or `*_test.py`
- Functions named `test_*` within those files
- Classes named `Test*` containing test methods

These are the defaults. All three come from ini options rustest reads out of your
`pytest.ini` or `pyproject.toml` (`python_files`, `python_classes`, `python_functions`), so
a project that has already customized them for pytest keeps the same discovery under
rustest.

### Example directory structure

<!--rustest.mark.skip-->
```
my_project/
├── src/
│   └── mylib.py
├── tests/
│   ├── test_basic.py
│   ├── test_advanced.py
│   └── integration/
│       └── test_integration.py
└── pyproject.toml
```

## Basic test functions

Test functions are simple Python functions that start with `test_`:

```python
def test_basic_assertion() -> None:
    assert 1 + 1 == 2

def test_string_operations() -> None:
    text = "hello world"
    assert text.startswith("hello")
    assert "world" in text
    assert len(text) == 11

def test_list_operations() -> None:
    items = [1, 2, 3]
    items.append(4)
    assert len(items) == 4
    assert 4 in items
```

::: {.callout-tip title="Type hints"}
Type hints are not required on tests. They help your editor and your type checker, and
rustest ignores them either way.
:::

## Assertions

Rustest uses Python's built-in `assert` statement. Assertions are rewritten before the
module is imported, so a failure reports the values involved rather than just the source
line: `assert 41 == 42`, not `AssertionError`.

```python
def test_comparisons() -> None:
    # Equality
    assert 2 + 2 == 4
    assert "hello" != "world"

    # Numeric comparisons
    assert 10 > 5
    assert 3 <= 3

    # Membership
    assert "a" in "apple"
    assert 2 in [1, 2, 3]

    # Boolean
    assert True
    assert not False

    # Identity
    x = [1, 2, 3]
    y = x
    assert x is y
    assert x is not [1, 2, 3]
```

### Custom assertion messages

An assertion can carry its own message. It is printed above the rewritten comparison, so
you get both:

```python
def calculate_something() -> int:
    return 42

def test_with_message() -> None:
    value = calculate_something()
    assert value > 0, f"Expected positive value, got {value}"
```

## Test organization

### Grouping related tests

Related tests belong in the same file:

```python
# test_math_operations.py

def test_addition() -> None:
    assert 2 + 2 == 4

def test_subtraction() -> None:
    assert 5 - 3 == 2

def test_multiplication() -> None:
    assert 3 * 4 == 12

def test_division() -> None:
    assert 10 / 2 == 5
```

### Using test classes

Classes give you a second level of grouping, and a place to hang class-scoped fixtures:

```python
class TestMathOperations:
    """Tests for basic math operations."""

    def test_addition(self) -> None:
        assert 2 + 2 == 4

    def test_subtraction(self) -> None:
        assert 5 - 3 == 2

class TestStringOperations:
    """Tests for string operations."""

    def test_uppercase(self) -> None:
        assert "hello".upper() == "HELLO"

    def test_lowercase(self) -> None:
        assert "WORLD".lower() == "world"
```

See [Test Classes](test-classes.md) for more details.

## Setup and teardown

Use a fixture rather than a setup/teardown method. A fixture that yields runs its setup
before the test and everything after the `yield` once the test finishes, pass or fail:

```python
from rustest import fixture

class MockConnection:
    def query(self, sql: str):
        return [1]
    def close(self):
        pass

def connect_to_database():
    return MockConnection()

@fixture
def database_connection():
    # Setup
    conn = connect_to_database()
    print("Database connected")

    yield conn

    # Teardown
    conn.close()
    print("Database disconnected")

def test_query(database_connection):
    result = database_connection.query("SELECT 1")
    assert result is not None
```

See [Fixtures](intro-fixtures.md) for more information.

## Test output

The default output is the failure report plus the counts:

<!--rustest.mark.skip-->
```
================================== FAILURES ===================================
_____________________________ test_broken_feature _____________________________
Traceback (most recent call last):
  File "/path/to/test_example.py", line 12, in test_broken_feature
    assert result == 5
AssertionError: assert 4 == 5
=========================== short test summary info ===========================
FAILED test_example.py::test_broken_feature

1 failed, 3 passed, 1 skipped in 0.44s
```

The tests that passed and the one that skipped print nothing of their own. That is pytest's
shape and it is deliberate: on a suite of a few thousand tests, the lines worth reading are
the ones about what broke.

### Verbose output

For one line per test, use `-v` or `--verbose`:

<!--rustest.mark.skip-->
```bash
rustest -v
```

<!--rustest.mark.skip-->
```
test_example.py::test_basic_assertion PASSED                            [ 20%]
test_example.py::test_string_operations PASSED                          [ 40%]
test_example.py::test_list_operations PASSED                            [ 60%]
test_example.py::test_future_feature SKIPPED (not implemented yet)      [ 80%]
test_example.py::test_broken_feature FAILED                             [100%]
================================== FAILURES ===================================
_____________________________ test_broken_feature _____________________________
Traceback (most recent call last):
  File "/path/to/test_example.py", line 12, in test_broken_feature
    assert result == 5
AssertionError: assert 4 == 5
=========================== short test summary info ===========================
FAILED test_example.py::test_broken_feature

1 failed, 3 passed, 1 skipped in 0.40s
```

Verbose mode adds:

- The full node id of every test, byte-identical to the one pytest would print, which is
  the form `--lf` keys on and the form to paste into a CI failure list
- Its outcome in pytest's wording: `PASSED`, `FAILED`, `SKIPPED (reason)`, `XFAIL`,
  `XPASS`, `ERROR`
- A running percentage through the selected set

Skip reasons appear only at this rung, which is often the reason to reach for it.

`-k` matches against the parts of a node id rather than the whole string: the path
segments, the class names, the function name with its `[param]` suffix, and the names of
any marks. `-k test_list_operations` works; a whole node id with `::` in it matches
nothing.

### Viewing print statements

Rustest captures stdout and stderr by default. To see print statements as a test runs:

<!--rustest.mark.skip-->
```bash
rustest --no-capture
```

```python
def test_with_output() -> None:
    print("Debug information")
    assert True
```

## Best practices

### Keep tests simple and focused

Each test should verify one behavior. When a test that checks four things fails, the report
tells you the first one that broke and nothing about the rest:

```python
class User:
    def __init__(self, name: str):
        self.name = name
        self.email = ""
        self._exists = True
    def update_email(self, email: str):
        self.email = email
    def delete(self):
        self._exists = False
    def exists(self):
        return self._exists

def create_user(name: str):
    return User(name)

# Good - tests one thing
def test_user_creation() -> None:
    user = create_user("Alice")
    assert user.name == "Alice"

# Less ideal - tests multiple things
def test_user_operations() -> None:
    user = create_user("Alice")
    assert user.name == "Alice"
    user.update_email("alice@example.com")
    assert user.email == "alice@example.com"
    user.delete()
    assert not user.exists()
```

### Use descriptive test names

The test name is what a failure prints, so it should say what broke without your having to
open the file:

```python
class ShoppingCart:
    def __init__(self):
        self.total = 0
        self.items = []
    def add(self, product):
        self.items.append(product)
        self.total += product.price

# Good
def test_empty_cart_has_zero_total() -> None:
    cart = ShoppingCart()
    assert cart.total == 0

# Less clear
def test_cart() -> None:
    cart = ShoppingCart()
    assert cart.total == 0
```

### Arrange-Act-Assert pattern

Set up the data, perform the action, then check the result:

```python
class Product:
    def __init__(self, name: str, price: float):
        self.name = name
        self.price = price

class ShoppingCart:
    def __init__(self):
        self.total = 0.0
        self.items = []
    def add(self, product: Product):
        self.items.append(product)
        self.total += product.price

def test_user_can_add_items_to_cart() -> None:
    # Arrange - set up test data
    cart = ShoppingCart()
    item = Product("Book", price=10)

    # Act - perform the action being tested
    cart.add(item)

    # Assert - verify the results
    assert len(cart.items) == 1
    assert cart.total == 10
```

## Next steps

- [Fixtures](intro-fixtures.md) - Learn about reusable test data and setup
- [Parametrization](intro-parametrization.md) - Run the same test with different inputs
- [Marks & Skipping](marks.md) - Organize and skip tests
- [Test Classes](test-classes.md) - Organize tests using classes
