# Your First Test in 5 Minutes

Zero to passing tests, in about the time it takes to make coffee.

## Step 1: Install rustest

With pip:

```bash
pip install rustest
```

With uv:

```bash
uv add rustest
```

## Step 2: Create a test file

Create a new file called `test_math.py`:

```python
def test_addition():
    result = 2 + 2
    assert result == 4
```

Three things make that a test rustest will find and run:

- **File name**: `test_math.py`. Test files start with `test_` (or end with `_test.py`)
- **Function name**: `test_addition()`. Test functions start with `test_`
- **assert**: this checks that something is true. If `result == 4`, the test passes. If not, it fails

## Step 3: Run your test

In your terminal, run:

```bash
rustest
```

You should see:

```
1 passed in 0.40s
```

You just wrote and ran your first automated test.

## Understanding What Happened

When you ran `rustest`, it:

1. **Found your test file** (`test_math.py`)
2. **Found your test function** (`test_addition`)
3. **Ran the function**
4. **Checked the assertion**, and `result == 4` was true, so the test passed
5. **Reported the results**, in one line, because nothing went wrong

A green run is deliberately boring: no ticks, no progress bar, just the count and how long
it took. Everything rustest has to say about a run appears *above* that line, so when the
summary is the only output, there was nothing to say.

## Step 4: See a failing test

Break the test on purpose:

<!--rustest.mark.skip-->
```python
def test_addition():
    result = 2 + 2
    assert result == 5  # This is wrong on purpose!
```

Run `rustest` again:

```
================================== FAILURES ===================================
________________________________ test_addition ________________________________
Traceback (most recent call last):
  File "/path/to/test_math.py", line 3, in test_addition
    assert result == 5  # This is wrong on purpose!
    ^^^^^^^^^^^^^^^^^^
AssertionError: assert 4 == 5
=========================== short test summary info ===========================
FAILED test_math.py::test_addition

1 failed in 0.35s
```

Rustest shows you:

- **Where it failed**: the file and line, in a traceback you can click in most terminals
- **The exact expression**: `assert result == 5`, with `^^^` underlining what was evaluated
- **What went wrong**: `assert 4 == 5`. Rustest rewrites the assertion so you see the
  *values*, not just that something was false
- **A copyable list**: `short test summary info` repeats every failure's node id at the
  bottom, so a long red run ends with the roll-call rather than making you scroll

!!! tip "Re-running just the failures"
    Use `--lf` (last-failed) to re-run only what went red. Pasting the node id back as a
    path argument does *not* narrow the run, as [the CLI guide](cli.md) explains, but `--lf`
    does, and it needs no copying.

## Step 5: Test something real

Two-plus-two is a warm-up. Create a file called `calculator.py`:

```python
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b
```

Now create `test_calculator.py`:

<!--rustest.mark.skip-->
```python
from calculator import add, multiply

def test_add_positive_numbers():
    result = add(2, 3)
    assert result == 5

def test_add_negative_numbers():
    result = add(-1, -1)
    assert result == -2

def test_multiply():
    result = multiply(4, 5)
    assert result == 20
```

Run `rustest`:

```
3 passed in 0.33s
```

Three passing tests against real code. To see their names, add `-v`:

```
test_calculator.py::test_add_positive_numbers PASSED                    [ 33%]
test_calculator.py::test_add_negative_numbers PASSED                    [ 66%]
test_calculator.py::test_multiply PASSED                                [100%]

3 passed in 0.33s
```

## Step 6: Add more assertions

You can have multiple assertions in one test:

```python
def test_string_operations():
    text = "hello world"

    # Check multiple things
    assert text.startswith("hello")
    assert "world" in text
    assert len(text) == 11
    assert text.upper() == "HELLO WORLD"
```

All four assertions must pass for the test to succeed.

## Common Patterns

### Testing for expected errors

Sometimes you *want* code to raise an error:

```python
from rustest import raises

def test_division_by_zero():
    with raises(ZeroDivisionError):
        result = 10 / 0
```

This test **passes** if a `ZeroDivisionError` is raised. If no error occurs, the test fails.

### Testing with floating point numbers

Floating point math can be imprecise:

<!--rustest.mark.skip-->
```python
result = 0.1 + 0.2
assert result == 0.3  # This might fail!
```

Use `approx()` for tolerant comparisons:

```python
from rustest import approx

def test_floating_point():
    result = 0.1 + 0.2
    assert result == approx(0.3)  # This works!
```

### Testing multiple related cases

You'll often want to test similar things with different inputs. For now, you can write separate tests:

```python
def add(a, b):
    return a + b

def test_add_small_numbers():
    assert add(1, 2) == 3

def test_add_large_numbers():
    assert add(1000, 2000) == 3000

def test_add_negative_numbers():
    assert add(-5, -3) == -8
```

Later, you'll learn about **parametrization**, which collapses all three into one.

## Organizing Your Tests

As you write more tests, organize them in a `tests/` directory:

```
my_project/
├── calculator.py          # Your code
├── tests/
│   ├── test_calculator.py # Tests for calculator
│   └── test_utils.py      # Tests for utils
└── utils.py              # More code
```

Rustest will automatically find all `test_*.py` files in the `tests/` directory.

## Running Specific Tests

You don't have to run all tests every time:

```bash
# Run all tests
rustest

# Run tests in a specific file
rustest tests/test_calculator.py

# Run tests matching a pattern
rustest -k "add"  # Runs test_add_positive_numbers and test_add_negative_numbers

# Run with verbose output
rustest -v
```

## What You've Learned

In just 5 minutes, you:

- Installed rustest
- Wrote your first test
- Ran tests and saw passing and failing results
- Tested real code
- Learned common testing patterns
- Organized tests properly

## What's Next?

The fundamentals behind what you just did:

[Learn Testing Basics](intro-testing-basics.md)

Or jump straight to more advanced topics:

- [Making Tests Reusable (Fixtures)](intro-fixtures.md), so you stop repeating setup
- [Testing Multiple Cases (Parametrization)](intro-parametrization.md), to test many inputs at once
- [Organizing Your Tests](intro-organizing.md), for structuring larger projects
