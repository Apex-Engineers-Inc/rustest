# Quick start

Writing and running your first tests with rustest, start to finish.

## 1. Write your first test

Create a file called `test_math.py`:

```python
def test_simple_addition() -> None:
    assert 1 + 1 == 2

def test_string_operations() -> None:
    text = "hello world"
    assert text.startswith("hello")
    assert "world" in text
```

## 2. Run your tests

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
there is no spinner, no progress bar and no per-test tick at the default verbosity, just the
summary line. Anything that went wrong prints above it.

The verbosity ladder has three rungs:

| Rung | You get |
|---|---|
| `-q` | The summary line and nothing else |
| *default* | Plus `ERRORS` / `FAILURES` blocks and a `short test summary info` list of node ids |
| `-v` | Plus one line per test, in pytest's wording |

!!! tip "Verbose output"
    Use `-v` or `--verbose` to see one line per test with a running percentage:
    ```
    test_math.py::test_simple_addition PASSED                               [ 50%]
    test_math.py::test_string_operations PASSED                             [100%]

    2 passed in 0.38s
    ```

!!! note "Which stream is which"
    **stdout** carries the payload: the per-test lines and the failure blocks. **stderr**
    carries the diagnostics: collection errors, anything the workers wrote, and the summary
    line. So `rustest > results.txt` leaves you a grep-able file of results while the summary
    still reaches your terminal.

## 3. Using fixtures

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

Rustest detects that `test_user_data` needs the `sample_data` fixture and injects it.

## 4. Parametrized tests

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

## 5. Assertion helpers

Rustest provides helpers for two assertions that are awkward to write by hand:

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

## 6. Organizing tests with marks

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

## Running tests

### Basic usage

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

# Run the python fences in a markdown file as tests -- --codeblocks turns the tier on
rustest README.md --codeblocks
```

Markdown has to be **named**, and the tier itself is **off by default**. A directory
argument collects no `.md` at all even when the tier is on, which is what pytest walking the
same tree does too. Naming a `.md` file with nothing enabling the tier -- no `--codeblocks`,
no `[tool.rustest] codeblocks = true` -- is a usage error, matching `pytest README.md`
exactly. See [Markdown Testing](markdown-testing.md) for the config spellings and what a
block actually runs.

### From Python

You can also run rustest programmatically. `run()` is keyword-only and returns pytest's exit
code as an `int`:

<!--rustest.mark.skip-->
```python
from rustest import run

exit_code = run(paths=["tests"])

# With filtering
exit_code = run(paths=["tests"], keyword="user")

# For per-test detail, write a JSON report and read it back
run(paths=["tests"], report_json="report.json")
```

[Python API](python-api.md) documents every argument and the shape of the report file.

## What's next?

- [Writing Tests](writing-tests.md), more on test functions and structure
- [Fixtures](fixtures.md), fixture scopes and dependencies
- [Parametrization](parametrization.md), the full parametrization surface
- [CLI Usage](cli.md), the complete CLI reference
