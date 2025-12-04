# Quick Start

This guide will walk you through writing and running your first tests with rustest.

## 1. Write Your First Test

Create a file called `test_math.py`:

```python
def test_simple_addition() -> None:
    assert 1 + 1 == 2

def test_string_operations() -> None:
    text = "hello world"
    assert text.startswith("hello")
    assert "world" in text
```

## 2. Run Your Tests

Run your tests with the `rustest` command:

<!--rustest.mark.skip-->
```bash
rustest
```

You should see output like this:

```
✓ Collected 2 tests from 1 files (15ms)

✓ test_math.py (1ms) 100% • 2/2

✓ 2 passed in 1ms
```

Rustest shows real-time feedback:

1. **Collection phase**: A spinner shows progress while discovering tests
2. **Collection summary**: Total tests and files found
3. **Execution phase**: Progress bars for each file
4. **Final summary**: Pass/fail counts and duration

Each `✓` represents a passing test. Failed tests show as `✗` with detailed error information.

!!! tip "Verbose Output"
    Use `-v` or `--verbose` to see individual test names and timing:
    ```
    /path/to/test_math.py
      ✓ test_simple_addition 0ms
      ✓ test_string_operations 1ms

    ✓ 2/2 2 passing (1ms)
    ```

## 3. Using Fixtures

Fixtures provide reusable test data and setup. Add this to your test file:

```python
from rustest import fixture

@fixture
def sample_data() -> dict:
    return {"name": "Alice", "age": 30}

def test_user_data(sample_data: dict) -> None:
    assert sample_data["name"] == "Alice"
    assert sample_data["age"] == 30
```

Rustest automatically detects that `test_user_data` needs the `sample_data` fixture and injects it.

## 4. Parametrized Tests

Run the same test with different inputs using `@parametrize`:

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

This will run three separate test cases, showing three checkmarks in the output:

```
✓✓✓

✓ 3/3 3 passing (1ms)
```

## 5. Assertion Helpers

Rustest provides helpful utilities for common assertions:

```python
from rustest import approx, raises

def test_floating_point() -> None:
    # Handle floating point precision
    assert 0.1 + 0.2 == approx(0.3)

def test_exceptions() -> None:
    # Assert that code raises an exception
    with raises(ValueError, match="invalid"):
        raise ValueError("invalid input")
```

## 6. Organizing Tests with Marks

Use marks to organize and categorize your tests:

```python
from rustest import mark

@mark.unit
def test_calculation() -> None:
    assert 2 + 2 == 4

@mark.integration
@mark.slow
def test_database_integration() -> None:
    # This test has multiple marks
    pass
```

## Running Tests

### Basic Usage

<!--rustest.mark.skip-->
```bash
# Run all tests in current directory
rustest

# Run tests in specific paths
rustest tests/ integration/

# Filter tests by name pattern
rustest -k "user"  # Runs test_user_login, test_user_data, etc.

# Show print statements during execution
rustest --no-capture

# Disable markdown code block tests
rustest --no-codeblocks
```

### From Python

You can also run rustest programmatically:

<!--rustest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])
print(f"Passed: {report.passed}, Failed: {report.failed}")

# With filtering
report = run(paths=["tests"], pattern="user")

# Access individual results
for result in report.results:
    if result.status == "failed":
        print(f"{result.name}: {result.message}")
```

## What's Next?

You now know the basics of rustest! Continue learning:

- [Writing Tests](../guide/writing-tests.md) - Learn more about test functions and structure
- [Fixtures](../guide/fixtures.md) - Deep dive into fixture scopes and dependencies
- [Parametrization](../guide/parametrization.md) - Advanced parametrization techniques
- [CLI Usage](../guide/cli.md) - Complete CLI reference
