# Writing Tests

Rustest follows pytest conventions for test discovery and organization.

## Test Discovery

Rustest automatically discovers tests by looking for:

- Files named `test_*.py` or `*_test.py`
- Functions named `test_*` within those files
- Classes named `Test*` containing test methods

### Example Directory Structure

<!--pytest.mark.skip-->
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

## Basic Test Functions

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

!!! tip "Type Hints"
    While not required, adding type hints to your tests helps with code clarity and IDE support.

## Assertions

Rustest uses Python's built-in `assert` statement:

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

### Custom Assertion Messages

You can provide custom messages for assertions:

```python
def calculate_something() -> int:
    return 42

def test_with_message() -> None:
    value = calculate_something()
    assert value > 0, f"Expected positive value, got {value}"
```

## Test Organization

### Grouping Related Tests

For better organization, group related tests in the same file:

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

### Using Test Classes

Group related tests using classes:

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

## Setup and Teardown

For setup and teardown logic, use fixtures instead of traditional setup/teardown methods:

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

See [Fixtures](fixtures.md) for more information.

## Test Output

When you run rustest, you'll see clean, informative output:

<!--pytest.mark.skip-->
```
✓✓✓⊘✗

test_broken_feature (test_example.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError

  Expected: 5
  Received: 4

✗ 5 tests: 3 passed, 1 failed, 1 skipped in 0.01s
```

**Output symbols:**
- `✓` = Passed test
- `✗` = Failed test
- `⊘` = Skipped test

### Verbose Output

For more detailed output showing test names and timing, use the `-v` or `--verbose` flag:

<!--pytest.mark.skip-->
```bash
rustest -v
```

<!--pytest.mark.skip-->
```
/home/user/project/test_example.py
  ✓ test_basic_assertion 0ms
  ✓ test_string_operations 1ms
  ✓ test_list_operations 0ms
  ⊘ test_future_feature 0ms
  ✗ test_broken_feature 2ms
    AssertionError: Expected 5, got 4

5 tests: 3 passed, 1 failed, 1 skipped in 0.003s
```

Verbose mode shows:
- File paths being tested
- Individual test names with indentation
- Timing for each test in milliseconds
- Inline error output for failed tests

### Viewing Print Statements

By default, rustest captures stdout/stderr. To see print statements during test execution:

<!--pytest.mark.skip-->
```bash
rustest --no-capture
```

```python
def test_with_output() -> None:
    print("Debug information")
    assert True
```

## Best Practices

### Keep Tests Simple and Focused

Each test should verify one specific behavior:

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

### Use Descriptive Test Names

Test names should clearly describe what they test:

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

### Arrange-Act-Assert Pattern

Organize test code into three sections:

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

## Next Steps

- [Fixtures](fixtures.md) - Learn about reusable test data and setup
- [Parametrization](parametrization.md) - Run the same test with different inputs
- [Marks & Skipping](marks.md) - Organize and skip tests
- [Test Classes](test-classes.md) - Organize tests using classes
