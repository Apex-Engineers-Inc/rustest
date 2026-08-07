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

Run your tests with the `rustest` command, then again with `-v`:

{{< termshow file="first-run" autoplay="false" loop="false" >}}

That first line is the whole of a green run. Rustest's output is pytest's, so a quiet run is
quiet: there is no spinner, no progress bar and no per-test tick at the default verbosity,
just the summary line. Anything that went wrong prints above it.

The verbosity ladder has three rungs:

| Rung | You get |
|---|---|
| `-q` | The summary line and nothing else |
| *default* | Plus `ERRORS` / `FAILURES` blocks and a `short test summary info` list of node ids |
| `-v` | Plus one line per test, in pytest's wording |

::: {.callout-note title="Which stream is which"}
**stdout** carries the payload: the per-test lines and the failure blocks. **stderr**
carries the diagnostics: collection errors, anything the workers wrote, and the summary
line. So `rustest > results.txt` leaves you a grep-able file of results while the summary
still reaches your terminal.
:::

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

@parametrize("value,expected", [
    (1, 2),
    (2, 4),
    (3, 6),
])
def test_double(value: int, expected: int) -> None:
    assert value * 2 == expected
```

That is three separate test cases. Run it with `-v` to see the ids parametrization
generates, then select one of them with `-k`:

{{< termshow file="parametrize" autoplay="false" loop="false" >}}

Those bracketed ids are pytest's, byte for byte, which is what makes `-k` selections and
CI failure reports portable between the two runners.

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
same tree does too. Naming a `.md` file with nothing enabling the tier, with no
`--codeblocks` and no `[tool.rustest] codeblocks = true`, is a usage error, matching
`pytest README.md` exactly. See [Markdown Testing](markdown-testing.md) for the config spellings and what a
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
