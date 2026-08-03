# Your First Test in 5 Minutes

Let's write a test! This guide will get you from zero to passing tests in just a few minutes.

## Step 1: Install rustest

First, you need rustest installed. Choose your preferred method:

=== "pip"
    ```bash
    pip install rustest
    ```

=== "uv (recommended)"
    ```bash
    uv add rustest
    ```

That's it! Rustest is installed.

## Step 2: Create a test file

Create a new file called `test_math.py`:

```python
def test_addition():
    result = 2 + 2
    assert result == 4
```

Let's break this down:

- **File name**: `test_math.py` — Test files must start with `test_`
- **Function name**: `test_addition()` — Test functions must also start with `test_`
- **assert**: This checks if something is true. If `result == 4`, the test passes! If not, it fails.

## Step 3: Run your test

In your terminal, run:

```bash
rustest
```

You should see:

```
1 passed in 0.40s
```

**Congratulations!** 🎉 You just wrote and ran your first automated test!

## Understanding What Happened

When you ran `rustest`, it:

1. **Found your test file** (`test_math.py`)
2. **Found your test function** (`test_addition`)
3. **Ran the function**
4. **Checked the assertion** — `result == 4` was true, so the test passed!
5. **Reported the results** — one line, because nothing went wrong

A green run is deliberately boring: no ticks, no progress bar, just the count and how long
it took. Everything rustest has to say about a run appears *above* that line, so when the
summary is the only output, there was nothing to say.

## Step 4: See a failing test

Let's see what happens when a test fails. Update your test:

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

- 📍 **Where it failed** — the file and line, in a traceback you can click in most terminals
- 🔍 **The exact expression** — `assert result == 5`, with `^^^` underlining what was evaluated
- 💡 **What went wrong** — `assert 4 == 5`: rustest rewrites the assertion so you see the
  *values*, not just that something was false
- 📋 **A copyable list** — `short test summary info` repeats every failure's node id at the
  bottom, so a long red run ends with the roll-call rather than making you scroll

This makes debugging **super easy**.

!!! tip "Re-running just the failures"
    Use `--lf` (last-failed) to re-run only what went red. Pasting the node id back as a
    path argument does *not* narrow the run — see [the CLI guide](cli.md) — but `--lf`
    does, and it needs no copying.

## Step 5: Test something real

Let's test actual code. Create a file called `calculator.py`:

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

**Three passing tests!** Now you're testing real code. Want to see their names? Add `-v`:

```
test_calculator.py::test_add PASSED                                     [ 33%]
test_calculator.py::test_subtract PASSED                                [ 66%]
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

This test **passes** if a `ZeroDivisionError` is raised. If no error occurs, the test fails!

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
def test_add_small_numbers():
    assert add(1, 2) == 3

def test_add_large_numbers():
    assert add(1000, 2000) == 3000

def test_add_negative_numbers():
    assert add(-5, -3) == -8
```

Later, you'll learn about **parametrization** which makes this even easier!

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
rustest -k "add"  # Runs test_add_positive_numbers, test_add_negative_numbers, etc.

# Run with verbose output
rustest -v
```

## What You've Learned

In just 5 minutes, you:

- ✅ Installed rustest
- ✅ Wrote your first test
- ✅ Ran tests and saw passing/failing results
- ✅ Tested real code
- ✅ Learned common testing patterns
- ✅ Organized tests properly

## What's Next?

Now that you've written your first tests, let's dive deeper into testing fundamentals:

[:octicons-arrow-right-24: Learn Testing Basics](intro-testing-basics.md){ .md-button .md-button--primary }

Or jump straight to more advanced topics:

- [Making Tests Reusable (Fixtures)](intro-fixtures.md) — Don't repeat yourself
- [Testing Multiple Cases (Parametrization)](intro-parametrization.md) — Test many inputs easily
- [Organizing Your Tests](intro-organizing.md) — Structure for larger projects
