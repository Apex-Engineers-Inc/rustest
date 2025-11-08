# Markdown Code Block Testing

Rustest can automatically discover and test Python code blocks in your markdown files, ensuring your documentation examples stay up-to-date and functional.

## Overview

This feature is similar to pytest-codeblocks but built into rustest with better performance. It's perfect for:

- Testing documentation examples
- Ensuring README code samples work
- Validating tutorial code
- Keeping guides in sync with your codebase

## Automatic Discovery

By default, rustest automatically discovers and tests Python code blocks in `.md` files:

```bash
rustest  # Tests both .py files and .md files
```

Each Python code block is treated as a separate test case.

## Markdown File Example

Create a markdown file (e.g., `example.md`):

````markdown
# Example Documentation

## Basic Addition

```python
x = 1 + 1
assert x == 2
```

## String Operations

```python
text = "hello world"
assert text.startswith("hello")
assert "world" in text
```

## Using Imports

```python
from datetime import datetime

now = datetime.now()
assert isinstance(now, datetime)
```
````

Run rustest:

```bash
rustest example.md
```

Output:

```
  PASSED   0.001s example.md::block_0
  PASSED   0.001s example.md::block_1
  PASSED   0.002s example.md::block_2

3 tests: 3 passed in 0.003s
```

## Disabling Markdown Testing

If you don't want to test markdown files, disable it with `--no-codeblocks`:

```bash
# Only test Python files, skip markdown
rustest --no-codeblocks

# Test specific directory without markdown
rustest tests/ --no-codeblocks
```

## Language Filtering

Only Python code blocks are tested. Other languages are ignored:

````markdown
# Documentation

```python
# This will be tested
assert 1 + 1 == 2
```

```javascript
// This is ignored
console.log("Hello");
```

```bash
# This is ignored
echo "Hello"
```
````

## Code Block Structure

### Simple Assertions

```python
# Basic assertion
assert 2 + 2 == 4

# Multiple assertions
assert "hello".upper() == "HELLO"
assert len("test") == 4
```

### Using Standard Library

```python
from pathlib import Path

# Create a path
p = Path("/tmp/test.txt")
assert p.suffix == ".txt"
assert p.parent == Path("/tmp")
```

### Using Your Library

If you're documenting a library, you can import and test it:

````markdown
## Using rustest

```python
from rustest import approx

# Test floating point comparison
assert 0.1 + 0.2 == approx(0.3)
```

```python
from rustest import raises

# Test exception handling
with raises(ValueError):
    int("not a number")
```
````

## Real-World Examples

### README Example

````markdown
# MyLibrary

## Installation

```bash
pip install mylib
```

## Quick Start

```python
from mylib import Calculator

calc = Calculator()
result = calc.add(2, 3)
assert result == 5
```

## Advanced Usage

```python
from mylib import Calculator

calc = Calculator()

# Chained operations
result = calc.add(10, 5).multiply(2).value
assert result == 30
```
````

### Tutorial Example

````markdown
# Python Basics Tutorial

## Lesson 1: Variables

```python
# Create a variable
name = "Alice"
assert len(name) == 5
```

## Lesson 2: Lists

```python
# Create and manipulate lists
fruits = ["apple", "banana", "orange"]
fruits.append("grape")
assert len(fruits) == 4
assert "grape" in fruits
```

## Lesson 3: Functions

```python
# Define and test a function
def greet(name):
    return f"Hello, {name}!"

result = greet("World")
assert result == "Hello, World!"
```
````

### API Documentation Example

````markdown
# API Reference

## User Management

Create a new user:

```python
from myapi import User

user = User(name="Alice", email="alice@example.com")
assert user.name == "Alice"
assert "@" in user.email
```

Update user details:

```python
from myapi import User

user = User(name="Bob", email="bob@example.com")
user.update_email("newemail@example.com")
assert user.email == "newemail@example.com"
```
````

## Code Block Sharing State

!!! warning "Each Block is Isolated"
    Each Python code block runs in its own isolated environment. Variables from one block are NOT available in the next block.

````markdown
# Example

```python
# Block 1
x = 10
```

```python
# Block 2 - FAILS! x is not defined here
assert x == 10  # NameError: name 'x' is not defined
```
````

If you need shared state, put it in one code block:

````markdown
```python
# All in one block - this works
x = 10
y = 20
assert x + y == 30
```
````

## Handling Expected Failures

If you want to show code that deliberately fails, use text blocks or describe the failure:

````markdown
# Error Handling

This code demonstrates an error:

```text
# This would raise an error (shown as text, not tested)
result = 1 / 0  # ZeroDivisionError
```

The correct way to handle it:

```python
from rustest import raises

with raises(ZeroDivisionError):
    1 / 0
```
````

## Best Practices

### Keep Code Blocks Focused

```python
# Good - single concept per block
assert "hello".upper() == "HELLO"
```

```python
# Less ideal - too much in one block
assert "hello".upper() == "HELLO"
assert "world".lower() == "world"
assert "Python".capitalize() == "Python"
assert "test".replace("t", "T") == "TesT"
# ... many more assertions
```

### Use Realistic Examples

```python
# Good - realistic usage
from datetime import datetime, timedelta

tomorrow = datetime.now() + timedelta(days=1)
assert tomorrow > datetime.now()
```

```python
# Less helpful - trivial example
x = 1
assert x == 1
```

### Test Important Features

```python
# Good - demonstrates key functionality
from mylib import DataProcessor

processor = DataProcessor()
result = processor.analyze([1, 2, 3, 4, 5])
assert result.mean == 3.0
assert result.median == 3.0
```

### Include Setup When Needed

```python
# Good - shows complete example
from pathlib import Path
import tempfile

# Create temporary file
with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
    f.write("test data")
    filepath = f.name

# Verify it exists
assert Path(filepath).exists()

# Cleanup
Path(filepath).unlink()
```

## Integration with Documentation Workflow

### During Development

Test your documentation as you write it:

```bash
# Test README while editing
rustest README.md --no-capture

# Watch for changes (with external tool)
# while true; do rustest README.md; sleep 2; done
```

### In CI/CD

Include markdown tests in your CI pipeline:

```yaml
# .github/workflows/test.yml
- name: Test documentation examples
  run: rustest **.md
```

### Pre-commit Hook

Test documentation before committing:

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: test-docs
      name: Test documentation examples
      entry: rustest
      args: ["README.md", "docs/"]
      language: system
      pass_filenames: false
```

## Programmatic Usage

Test markdown files from Python:

```python
from rustest import run

# Test specific markdown file
report = run(paths=["README.md"])
print(f"Documentation tests: {report.passed} passed, {report.failed} failed")

# Test all markdown in docs/
report = run(paths=["docs/"])

# Disable markdown testing
report = run(paths=["docs/"], enable_codeblocks=False)
```

## Limitations

- Each code block runs in isolation (no shared state)
- Only Python code blocks are tested
- Code blocks must be valid, complete Python code
- No support for continuation across blocks
- No support for interactive console examples (use doctest format as text if needed)

## Next Steps

- [CLI Usage](cli.md) - Learn about --no-codeblocks and other options
- [Python API](python-api.md) - Control markdown testing programmatically
- [Writing Tests](writing-tests.md) - Learn about regular Python tests
