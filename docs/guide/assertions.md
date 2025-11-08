# Assertion Helpers

Rustest provides helpful utilities for common assertions: `approx()` for numeric comparisons and `raises()` for exception testing.

## The approx() Function

The `approx()` function makes it easy to compare floating-point numbers and other numeric types with tolerance.

### Basic Usage

```python
from rustest import approx

def test_floating_point() -> None:
    # Handle floating-point precision issues
    assert 0.1 + 0.2 == approx(0.3)
```

Without `approx()`, this test would fail due to floating-point arithmetic:

```python
def test_without_approx():
    # This fails! 0.1 + 0.2 = 0.30000000000000004
    assert 0.1 + 0.2 == 0.3
```

### Tolerance Parameters

#### Relative Tolerance

Control the acceptable relative difference:

```python
def test_relative_tolerance():
    # Default relative tolerance is 1e-6 (0.0001%)
    assert 100.0 == approx(100.0001, rel=1e-6)

    # Stricter tolerance
    assert 100.0 == approx(100.0, rel=1e-9)

    # Looser tolerance
    assert 100.0 == approx(101.0, rel=0.02)  # 2% tolerance
```

#### Absolute Tolerance

Control the acceptable absolute difference:

```python
def test_absolute_tolerance():
    # Default absolute tolerance is 1e-12
    assert 1.0 == approx(1.0000000000001)

    # Custom absolute tolerance
    assert 1.0 == approx(1.1, abs=0.2)
    assert 0.0 == approx(0.001, abs=0.01)
```

#### Combining Tolerances

```python
def test_combined_tolerances():
    # Passes if within EITHER tolerance
    assert 1.0 == approx(1.001, rel=1e-6, abs=0.01)
```

### Comparing Collections

`approx()` works with lists, tuples, and other sequences:

```python
def test_list_comparison():
    result = [0.1 + 0.1, 0.2 + 0.1, 0.3 + 0.1]
    expected = [0.2, 0.3, 0.4]
    assert result == approx(expected)

def test_tuple_comparison():
    result = (1.0001, 2.0002, 3.0003)
    assert result == approx((1.0, 2.0, 3.0), abs=0.001)
```

### Complex Numbers

`approx()` supports complex number comparisons:

```python
def test_complex_numbers():
    result = complex(1.0 + 1e-7, 2.0 + 1e-7)
    assert result == approx(complex(1.0, 2.0))
```

### Real-World Examples

#### Scientific Computing

```python
def test_physics_calculation():
    # Calculate velocity: v = d / t
    distance = 100.0  # meters
    time = 9.8       # seconds
    velocity = distance / time

    # Account for floating-point precision
    assert velocity == approx(10.204081632653061, rel=1e-6)
```

#### Financial Calculations

```python
def test_price_calculation():
    # Price with tax
    base_price = 19.99
    tax_rate = 0.08
    total = base_price * (1 + tax_rate)

    assert total == approx(21.5892, abs=0.01)  # Round to cents
```

#### Statistical Tests

```python
def test_mean_calculation():
    values = [1.1, 2.2, 3.3, 4.4, 5.5]
    mean = sum(values) / len(values)

    assert mean == approx(3.3, rel=1e-9)
```

## The raises() Context Manager

The `raises()` context manager asserts that code raises a specific exception.

### Basic Usage

```python
from rustest import raises

def test_zero_division():
    with raises(ZeroDivisionError):
        1 / 0
```

### With Exception Message Matching

Match exception messages using regex patterns:

```python
def test_value_error_message():
    with raises(ValueError, match="invalid literal"):
        int("not a number")

def test_custom_exception():
    with raises(ValueError, match="must be positive"):
        validate_age(-5)
```

The `match` parameter accepts any regex pattern:

```python
def test_regex_matching():
    # Exact match
    with raises(ValueError, match="^invalid value$"):
        raise ValueError("invalid value")

    # Contains
    with raises(ValueError, match="invalid"):
        raise ValueError("this is invalid input")

    # Pattern
    with raises(ValueError, match=r"expected \d+ but got \d+"):
        raise ValueError("expected 10 but got 5")
```

### Multiple Exception Types

Accept any of multiple exception types:

```python
def test_multiple_exceptions():
    with raises((ValueError, TypeError)):
        # Could raise either exception
        risky_operation()
```

### Accessing Exception Information

Access the caught exception for further inspection:

```python
def test_exception_details():
    with raises(ValueError) as exc_info:
        raise ValueError("something went wrong")

    # Access the exception value
    assert str(exc_info.value) == "something went wrong"

    # Access the exception type
    assert exc_info.type == ValueError
```

### Real-World Examples

#### Input Validation

```python
def test_age_validation():
    with raises(ValueError, match="Age must be between 0 and 150"):
        validate_age(200)

def test_email_validation():
    with raises(ValueError, match="Invalid email format"):
        validate_email("not-an-email")
```

#### API Error Handling

```python
def test_api_not_found():
    with raises(NotFoundError, match="User not found"):
        api.get_user(user_id=99999)

def test_api_unauthorized():
    with raises(UnauthorizedError, match="Invalid token"):
        api.protected_resource(token="invalid")
```

#### File Operations

```python
def test_file_not_found():
    with raises(FileNotFoundError):
        open("/nonexistent/file.txt")

def test_permission_denied():
    with raises(PermissionError):
        open("/root/protected.txt", "w")
```

#### Type Checking

```python
def test_type_error():
    with raises(TypeError, match="unsupported operand"):
        "string" + 42
```

## Combining Assertion Helpers

Use `approx()` and `raises()` together:

```python
from rustest import approx, raises

def test_division_result():
    result = 10 / 3
    assert result == approx(3.333333, rel=1e-6)

def test_division_by_zero():
    with raises(ZeroDivisionError, match="division by zero"):
        1 / 0
```

## Best Practices

### Use Appropriate Tolerances

```python
# Good - appropriate tolerance for the domain
def test_scientific_measurement():
    # Scientific measurements might need tight tolerance
    assert measurement == approx(expected, rel=1e-9)

def test_financial_calculation():
    # Money typically rounds to 2 decimal places
    assert total == approx(expected, abs=0.01)

# Too loose - hiding real bugs
def test_bad_tolerance():
    assert 100 == approx(200, rel=0.5)  # 50% tolerance is too much!
```

### Be Specific with Exception Messages

```python
# Good - verifies the exact error
def test_validation():
    with raises(ValueError, match="Email cannot be empty"):
        validate_email("")

# Less helpful - any ValueError passes
def test_validation_loose():
    with raises(ValueError):
        validate_email("")
```

### Don't Overuse approx()

```python
# Good - approx() only where needed
def test_integer_math():
    assert 2 + 2 == 4  # No approx() needed for exact integers

def test_float_math():
    assert 0.1 + 0.2 == approx(0.3)  # approx() needed for floats

# Unnecessary - integers are exact
def test_unnecessary_approx():
    assert 5 == approx(5)  # Just use assert 5 == 5
```

### Test Exception Details

```python
# Good - validates exception contents
def test_exception_contents():
    with raises(ValidationError) as exc:
        validate_user({"name": ""})

    # Verify error details
    assert "name" in exc.value.fields
    assert exc.value.code == "required"

# Basic - only checks exception type
def test_exception_basic():
    with raises(ValidationError):
        validate_user({"name": ""})
```

## Standard Python Assertions

For cases where `approx()` and `raises()` don't fit, use Python's built-in assertions:

```python
def test_membership():
    assert "hello" in "hello world"
    assert 5 in [1, 2, 3, 4, 5]

def test_identity():
    x = []
    y = x
    assert x is y

def test_type_checking():
    assert isinstance(42, int)
    assert isinstance("hello", str)

def test_boolean():
    assert True
    assert not False
    assert bool([1, 2, 3])
    assert not bool([])
```

## Next Steps

- [Writing Tests](writing-tests.md) - Learn more about test structure
- [Parametrization](parametrization.md) - Test multiple values
- [Fixtures](fixtures.md) - Reusable test data
