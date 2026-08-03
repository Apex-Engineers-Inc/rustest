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
2 passed in 0.36s
```

That is the whole of a green run. Rustest's output is pytest's, so a quiet run is quiet:
there is no spinner, no progress bar and no per-test tick at the default verbosity — just
the summary line. Anything that went wrong prints above it.

The verbosity ladder has three rungs:

| Rung | You get |
|---|---|
| `-q` | The summary line and nothing else |
| *default* | Plus `ERRORS` / `FAILURES` blocks and a `short test summary info` list of node ids |
| `-v` | Plus one line per test, in pytest's wording |

!!! tip "Verbose Output"
    Use `-v` or `--verbose` to see one line per test with a running percentage:
    ```
    test_math.py::test_simple_addition PASSED                               [ 50%]
    test_math.py::test_string_operations PASSED                             [100%]

    2 passed in 0.38s
    ```

!!! note "Which stream is which"
    **stdout** carries the payload — the per-test lines and the failure blocks. **stderr**
    carries the diagnostics — collection errors, anything the workers wrote, and the summary
    line. So `rustest > results.txt` leaves you a grep-able file of results while the summary
    still reaches your terminal.

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

This runs three separate test cases:

```
3 passed in 0.32s
```

Add `-v` to see the generated ids, which is where parametrization becomes easy to read:

```
test_math.py::test_double[1-2] PASSED                                   [ 33%]
test_math.py::test_double[2-4] PASSED                                   [ 66%]
test_math.py::test_double[3-6] PASSED                                   [100%]

3 passed in 0.32s
```

Those bracketed ids are pytest's, byte for byte. To run just one case, select it with `-k`:

```bash
rustest test_math.py -k "2-4"      # -> 1 passed, 2 deselected
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

- [Writing Tests](writing-tests.md) - Learn more about test functions and structure
- [Fixtures](intro-fixtures.md) - Deep dive into fixture scopes and dependencies
- [Parametrization](intro-parametrization.md) - Advanced parametrization techniques
- [CLI Usage](cli.md) - Complete CLI reference
