# Assertion Helpers

Rustest exports three assertion helpers: `approx()` for numeric comparisons, `raises()` for
exception testing, and `fail()` for explicit failures. Two more, `warns()` and
`deprecated_call()`, live on the pytest compatibility surface and are reached through
`import pytest`.

## The approx() Function

`approx()` compares numbers with a tolerance, so floating-point representation error does not
fail a test that is arithmetically correct.

### Basic Usage

```python
from rustest import approx

def test_floating_point() -> None:
    # Handle floating-point precision issues
    assert 0.1 + 0.2 == approx(0.3)
```

Without `approx()`, this test would fail due to floating-point arithmetic:

<!--rustest.mark.skip-->
```python
def test_without_approx():
    # This fails! 0.1 + 0.2 = 0.30000000000000004
    assert 0.1 + 0.2 == 0.3
```

### Tolerance Parameters

`approx(expected, rel=None, abs=None, nan_ok=False)`. Both tolerances default to `None`, which
means "unspecified" rather than zero, so passing `rel=None` explicitly is legal and does what
omitting it does.

#### Relative Tolerance

```python
from rustest import approx

def test_relative_tolerance():
    # Default relative tolerance is 1e-6 (0.0001%)
    assert 100.0 == approx(100.0001, rel=1e-6)

    # Stricter tolerance
    assert 100.0 == approx(100.0, rel=1e-9)

    # Looser tolerance
    assert 100.0 == approx(101.0, rel=0.02)  # 2% tolerance
```

#### Absolute Tolerance

```python
from rustest import approx

def test_absolute_tolerance():
    # Default absolute tolerance is 1e-12
    assert 1.0 == approx(1.0000000000001)

    # Custom absolute tolerance
    assert 1.0 == approx(1.1, abs=0.2)
    assert 0.0 == approx(0.001, abs=0.01)
```

#### Combining Tolerances

The tolerance actually applied is `max(rel * abs(expected), abs)`, so a value inside either one
passes. The exception is giving `abs` alone: with no `rel`, the absolute tolerance is used by
itself rather than being maxed against a relative one you never asked for.

```python
from rustest import approx

def test_combined_tolerances():
    # Passes if within EITHER tolerance
    assert 1.0 == approx(1.001, rel=1e-6, abs=0.01)
```

### Comparing Collections

`approx()` handles lists, tuples, and anything else that is sized and indexable.

```python
from rustest import approx

def test_list_comparison():
    result = [0.1 + 0.1, 0.2 + 0.1, 0.3 + 0.1]
    expected = [0.2, 0.3, 0.4]
    assert result == approx(expected)

def test_tuple_comparison():
    result = (1.0001, 2.0002, 3.0003)
    assert result == approx((1.0, 2.0, 3.0), abs=0.001)
```

Mappings work too, and their keys must match exactly as a set; only the values get a tolerance.
Sets and other unordered collections raise `TypeError`, because there is no defined pairing to
compare. Nesting a container inside a container is also refused, since the inner one would be
compared exactly and the tolerance would silently not apply.

When numpy is already imported, an array on either side is compared element by element, and a
failure prints an index/obtained/expected table rather than a single line.

### Complex Numbers

```python
from rustest import approx

def test_complex_numbers():
    result = complex(1.0 + 1e-7, 2.0 + 1e-7)
    assert result == approx(complex(1.0, 2.0))
```

`Decimal` is supported and gets `Decimal` tolerances, so the arithmetic never mixes `Decimal`
with `float`. NaN equals nothing unless you pass `nan_ok=True`, and infinity equals only
itself.

Two behaviours are worth knowing before they surprise you. A `bool` on the *expected* side is
treated as non-numeric, so `1 == approx(True)` is `False`; a `bool` on the actual side is
compared as the ordinary integer it is, so `True == approx(1)` is `True`. The asymmetry is
pytest's, and rustest reproduces it. Separately, `approx()` refuses a boolean context:
`if approx(x):` raises rather than being quietly truthy, since the only correct use is on one
side of a comparison.

### Real-World Examples

#### Scientific Computing

```python
from rustest import approx

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
from rustest import approx

def test_price_calculation():
    # Price with tax
    base_price = 19.99
    tax_rate = 0.08
    total = base_price * (1 + tax_rate)

    assert total == approx(21.5892, abs=0.01)  # Round to cents
```

#### Statistical Tests

```python
from rustest import approx

def test_mean_calculation():
    values = [1.1, 2.2, 3.3, 4.4, 5.5]
    mean = sum(values) / len(values)

    assert mean == approx(3.3, rel=1e-9)
```

## The raises() Context Manager

### Basic Usage

```python
from rustest import raises

def test_zero_division():
    with raises(ZeroDivisionError):
        1 / 0
```

If the block completes without raising, `raises` fails the test with `DID NOT RAISE`. That
failure is a `BaseException` subclass, so a stray `except Exception:` around the block cannot
turn it into a pass.

### Matching the Exception Message

`match` is a regex, applied with `re.search`, so it matches anywhere in the message unless you
anchor it. PEP 678 `__notes__` are part of the searched text alongside `str(exc)`.

```python
from rustest import raises

def test_value_error_message():
    with raises(ValueError, match="invalid literal"):
        int("not a number")

def validate_age(age):
    if age < 0:
        raise ValueError("age must be positive")

def test_custom_exception():
    with raises(ValueError, match="must be positive"):
        validate_age(-5)
```

The `match` parameter accepts any regex pattern:

```python
from rustest import raises

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

A pattern that fails to compile is reported when you write it, not when the block runs. When
the regex misses but the message equals the pattern verbatim, the failure adds a
"Did you mean to `re.escape()` the regex?" hint.

### Multiple Exception Types

```python
from rustest import raises

def risky_operation():
    raise ValueError("could have been a TypeError")

def test_multiple_exceptions():
    with raises((ValueError, TypeError)):
        # Could raise either exception
        risky_operation()
```

An exception whose type is not among the expected ones propagates unchanged rather than being
reported as a mismatch. You see the real traceback, not a wrapper.

### Accessing Exception Information

`with raises(...) as exc_info` binds an `ExceptionInfo`, which is unfilled until the block
exits. Reading `.value` before then raises rather than returning something misleading.

```python
from rustest import raises

def test_exception_details():
    with raises(ValueError) as exc_info:
        raise ValueError("something went wrong")

    # Access the exception value
    assert str(exc_info.value) == "something went wrong"

    # Access the exception type
    assert exc_info.type == ValueError
```

Beyond `.value` and `.type` it carries `.tb` (the raw traceback), `.typename`, `.traceback`
(an iterable list of entries), `.exconly()`, `.errisinstance()` and `.match()`.

### Two Further Parameters

`check=` takes a callable applied to the exception after the type and `match` have both passed;
returning anything falsy fails the test.

`raises` also accepts pytest's legacy callable form, `raises(Exc, func, *args, **kwargs)`,
which calls `func` immediately and returns the `ExceptionInfo`. In that form the keyword
arguments go to `func`, so `raises(E, f, match="x")` passes `match="x"` to `f` rather than
using it as a pattern.

### Real-World Examples

#### Input Validation

```python
from rustest import raises

def validate_age(age):
    if not 0 <= age <= 150:
        raise ValueError("Age must be between 0 and 150")

def validate_email(address):
    if "@" not in address:
        raise ValueError("Invalid email format")

def test_age_validation():
    with raises(ValueError, match="Age must be between 0 and 150"):
        validate_age(200)

def test_email_validation():
    with raises(ValueError, match="Invalid email format"):
        validate_email("not-an-email")
```

#### API Error Handling

<!--rustest.mark.skip-->
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
from rustest import raises

def test_file_not_found():
    with raises(FileNotFoundError):
        open("/nonexistent/file.txt")

```

Opening a file the current user may not write raises `PermissionError`. The path below is
POSIX-specific, and a privileged user would not see the error at all:

<!--rustest.mark.skip-->
```python
def test_permission_denied():
    with raises(PermissionError):
        open("/root/protected.txt", "w")
```

#### Type Checking

```python
from rustest import raises

def test_type_error():
    with raises(TypeError, match="can only concatenate"):
        "string" + 42
```

## The fail() Function

`fail()` raises immediately with your message. It earns its keep where the failure condition is
too involved to read well as a single `assert`.

### Basic Usage

<!--rustest.mark.skip-->
```python
from rustest import fail

def test_conditional_validation():
    data = load_data()

    if not is_valid(data):
        fail("Data validation failed")

    # Test continues only if data is valid
    process_data(data)
```

### With Detailed Messages

<!--rustest.mark.skip-->
```python
def test_operation_result():
    result = complex_operation()

    if result.status == "error":
        fail(f"Operation failed: {result.error_message}")

    if result.value < 0:
        fail(f"Expected positive value, got {result.value}")

    assert result.value > 0
```

`fail(reason, pytrace=False)` reports the message alone instead of a traceback. That flag is
honoured at collection; a failing test *body* still renders its traceback either way.

### Real-World Examples

#### State Validation

<!--rustest.mark.skip-->
```python
def test_database_state():
    db = connect_to_database()

    if not db.is_connected():
        fail("Database connection failed")

    if db.table_count() == 0:
        fail("No tables found in database")

    assert db.table_exists("users")
```

#### Multi-Step Verification

<!--rustest.mark.skip-->
```python
def test_user_workflow():
    user = create_user("test@example.com")

    if user is None:
        fail("Failed to create user")

    if not user.email_verified:
        fail(f"Expected verified email, but user {user.id} is not verified")

    # Continue with test...
    assert user.can_login()
```

#### Test Preconditions

<!--rustest.mark.skip-->
```python
def test_feature_availability():
    if not feature_flags.is_enabled("new_feature"):
        fail("Feature flag 'new_feature' is not enabled")

    # Test the new feature
    result = use_new_feature()
    assert result is not None
```

### When to Use fail() Versus assert

Use `assert` for straightforward conditions:

<!--rustest.mark.skip-->
```
assert value == expected
assert result is not None
```

Use `fail()` for complex conditional logic:

<!--rustest.mark.skip-->
```
if complex_condition_1 or complex_condition_2:
    fail("Detailed explanation of what went wrong")
```

Use `fail()` for early returns with clear messages:

<!--rustest.mark.skip-->
```
result = expensive_operation()
if result.is_error():
    fail(f"Operation failed early: {result.error}")
# Continue with more tests...
```

!!! tip "Clear failure messages"
    Always include descriptive messages with `fail()` to make debugging easier:

<!--rustest.mark.skip-->
```
# Good - describes what went wrong
fail(f"Expected user {user_id} to exist, but not found in database")

# Less helpful - generic message
fail("Test failed")
```

## Combining Assertion Helpers

```python
from rustest import approx, raises, fail

def load_data():
    return {"value": 10.0}

def process_invalid_data():
    raise ValueError("invalid payload")

def test_division_result():
    result = 10 / 3
    assert result == approx(3.333333, rel=1e-6)

def test_division_by_zero():
    with raises(ZeroDivisionError, match="division by zero"):
        1 / 0

def test_complex_validation():
    data = load_data()

    if not data:
        fail("No data returned from load_data()")

    # Validate numeric values with tolerance
    assert data["value"] == approx(10.0, abs=0.1)

    # Ensure error handling works
    with raises(ValueError, match="invalid"):
        process_invalid_data()
```

## The warns() Context Manager

`warns()` asserts that a block emits a warning. It comes from the pytest compatibility surface
rather than the `rustest` namespace, so reach it through `import pytest`.

### Basic Usage

```python
import warnings
import pytest

def test_deprecation_warning():
    with pytest.warns(DeprecationWarning):
        warnings.warn("This is deprecated", DeprecationWarning)

def test_user_warning():
    with pytest.warns(UserWarning):
        warnings.warn("Check your input", UserWarning)
```

The expected category defaults to `Warning`, so a bare `pytest.warns()` asserts that *something*
was warned. Passing an exception class that is not a `Warning` subclass is a `TypeError` at the
point you write it.

### Pattern Matching

```python
import warnings
import pytest

def test_warning_message():
    with pytest.warns(UserWarning, match="must be positive"):
        warnings.warn("Value must be positive", UserWarning)

def test_regex_match():
    with pytest.warns(DeprecationWarning, match=r"use \w+ instead"):
        warnings.warn("use new_function instead", DeprecationWarning)
```

A failure distinguishes the two ways this goes wrong: no warning of that category at all, or
the right category with a message the pattern did not match.

### Capturing Multiple Warnings

```python
import warnings
import pytest

def test_capture_warnings():
    with pytest.warns(UserWarning) as record:
        warnings.warn("first warning", UserWarning)
        warnings.warn("second warning", UserWarning)

    assert len(record) == 2
    assert "first" in str(record[0].message)
    assert "second" in str(record[1].message)

def test_capture_all_warnings():
    with pytest.warns() as record:  # No type specified captures all
        warnings.warn("user warning", UserWarning)
        warnings.warn("deprecation", DeprecationWarning)

    assert len(record) == 2
```

The `as` target is a recorder, not a bare list. Indexing, `len()` and iteration work, and so do
`record.list`, `record.pop(SomeWarning)` and `record.clear()`. Warnings the block raised that
the assertion did not claim are re-emitted on exit, so they still reach the enclosing filter
stack.

### Multiple Warning Types

```python
import warnings
import pytest

def test_multiple_types():
    with pytest.warns((UserWarning, DeprecationWarning)):
        warnings.warn("some warning", UserWarning)
```

## The deprecated_call() Context Manager

```python
import warnings
import pytest

def test_deprecated_function():
    with pytest.deprecated_call():
        warnings.warn("old function", DeprecationWarning)

def test_deprecated_with_match():
    with pytest.deprecated_call(match="use new_api"):
        warnings.warn("use new_api instead", DeprecationWarning)
```

!!! note "deprecated_call versus warns"
    `deprecated_call()` is `warns((DeprecationWarning, PendingDeprecationWarning, FutureWarning))`.
    `FutureWarning` is in the set because it is the category libraries pick for deprecations
    aimed at end users rather than developers, which is what numpy and pandas both do.

## Best Practices

### Use Appropriate Tolerances

```python
from rustest import approx

# Good - appropriate tolerance for the domain
def test_scientific_measurement():
    # Scientific measurements might need tight tolerance
    measurement, expected = 2.0000000001, 2.0
    assert measurement == approx(expected, rel=1e-9)

def test_financial_calculation():
    # Money typically rounds to 2 decimal places
    total, expected = 19.99 * 1.08, 21.5892
    assert total == approx(expected, abs=0.01)

# Too loose - hiding real bugs
def test_bad_tolerance():
    assert 100 == approx(200, rel=0.5)  # 50% tolerance is too much!
```

### Be Specific with Exception Messages

Without `match`, any exception of the right class passes, including one raised by a bug on the
line before the one you meant to test.

```python
from rustest import raises

def validate_email(address):
    if not address:
        raise ValueError("Email cannot be empty")

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
from rustest import approx

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

<!--rustest.mark.skip-->
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

Where `approx()` and `raises()` do not fit, a plain `assert` is the answer.

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

Rustest rewrites your assertions the way pytest does, so a failure reports the operands and not
just `AssertionError`. A failed `==` between two strings, lists, dicts or sets prints a diff of
where they differ; a failed comparison involving a call prints what each subexpression
evaluated to; a failed `in` shows the containing text. A failing `assert x == approx(y)` prints
approx's own index/obtained/expected table.

Two differences from pytest are worth knowing. Assertion verbosity is fixed at pytest's level 0,
so `-v` does not lengthen diffs or un-truncate reprs the way it does under pytest. And failure
sections are not syntax highlighted.

## Next Steps

- [Writing Tests](writing-tests.md) - Learn more about test structure
- [Parametrization](intro-parametrization.md) - Test multiple values
- [Fixtures](intro-fixtures.md) - Reusable test data
