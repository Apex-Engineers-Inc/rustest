# Markdown Code Block Testing

rustest runs the Python code blocks in your markdown files as tests, so a documentation
example that stops working fails the build instead of sitting there misleading people. This
is rustest's own extension: pytest collects nothing from a `.md` file.

The feature covers the same ground as pytest-codeblocks, including its skip markers, and
needs no plugin.

## Naming the Files

**Markdown files must be named as arguments.** A directory argument collects none. Run from
this repository, the two lines below do very different things:

```bash
rustest README.md user_guide/fixtures.md   # runs the python fences in both files
rustest user_guide/                        # "no tests ran": a walk never collects .md
```

This matches what pytest does with the same tree. A directory walk that picked up `.md`
would mean `rustest tests/` running tests `pytest tests/` never sees, which is how a repo
with fences in its docs ends up with a green pytest run and a red rustest one.

Shell globs are the usual way to name a whole directory of pages. rustest's own CI line is:

```bash
rustest README.md user_guide/*.md
```

Each Python code block is collected as its own test.

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
3 passed in 0.45s
```

Each fence is its own test. `-v` shows how they are named, by block index and by the line the
opening fence sits on, so a failure points at the right place in the page:

```
example.md::codeblock_0_line_3 PASSED                                   [ 33%]
example.md::codeblock_1_line_8 PASSED                                   [ 66%]
example.md::codeblock_2_line_13 PASSED                                  [100%]

3 passed in 0.45s
```

Every block is compiled with the markdown file as its filename, so a traceback names the
page and the line rather than an anonymous `<string>`.

Every block also carries a `codeblock` mark, so `-m` can select or exclude doc examples:

```bash
rustest README.md user_guide/*.md -m codeblock       # only doc examples
rustest README.md tests/ -m "not codeblock"          # everything except them
```

## Skipping Code Blocks

An HTML comment above a fence stops that block from being executed. Use it for pseudo-code,
and for examples that call into something your test environment does not have:

```markdown
<!--rustest.mark.skip-->
```python
# This example won't be executed
result = some_external_api()
```
```

Keep the marker on the line directly above the opening fence. One line may sit between them
and the marker still applies, but anything more and it is forgotten, so a marker separated
from its fence by a paragraph does nothing. A skipped block is never compiled either, which
is what lets you mark pseudo-code that would not parse.

The skipped block reports as `SKIPPED (Skipped via HTML comment marker)`.

!!! warning "A skip marker is permanent, and it is silent"
    Skipping does not just exempt a block from this run. It exempts it from every future run,
    so nothing will ever tell you when the code in it stops being correct. A skipped example
    that calls an API you later rename, or passes an argument you later reject, keeps sitting
    on the page looking authoritative while CI stays green.

    Reach for the marker when a block genuinely cannot execute: it needs a service, a package
    you will not depend on, or it is deliberately incomplete. When a block is skipped only
    because it needs a little setup, write the setup instead. A stub of five lines is cheaper
    than an example that quietly goes stale.

    Skipped blocks are worth re-reading by hand whenever the API around them changes, because
    no tool is going to do it for you.

!!! note "pytest compatibility"
    For compatibility with pytest-codeblocks, `<!--pytest.mark.skip-->` and `<!--pytest-codeblocks:skip-->` also work.

## Disabling Markdown Testing

`--no-codeblocks` removes the markdown tier entirely. Because a directory walk never
collects `.md` anyway, the flag has no effect on `rustest tests/`; what it does is turn a
named markdown file back into pytest's answer for one, which is the usage error `found no
collectors for ...` and exit code 4:

```bash
rustest README.md --no-codeblocks   # ERROR: found no collectors for README.md
```

Reach for it when something in your toolchain passes `*.md` and you want the pytest
behaviour back.

## Language Filtering

Only Python code blocks are tested. The language is the text after the opening fence,
lowercased, so ```` ```Python ```` counts and ```` ```pycon ```` does not. Other languages
are ignored:

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

## How a Block Executes

Each block is indented into a generated `def run_codeblock():`, compiled on its own, and
called. Two consequences follow from that, and the second one catches almost everybody.

### Each block gets a fresh namespace

Names defined in one block do not exist in the next, imports included. There is no way to
continue a block.

### Only module-level code runs

The block body *is* the test. A `def` inside it is defined and never called, so its
assertions are never checked:

<!--rustest.mark.skip-->
```python
def test_never_runs():
    assert False        # This block PASSES. Nothing calls test_never_runs.
```

The same applies to fixtures. `@fixture` inside a block registers nothing the block will
use, because the runner has already decided what this test is: the block itself, requesting
no fixtures.

Write documentation examples as **statements that assert directly**:

```python
def double(x: int) -> int:
    return x * 2

# The assertion is at block level, so it is actually checked.
assert double(21) == 42
```

If the example must show the shape of a test function, because that is what you are
documenting, keep the `def` for the reader and add a block-level call or assertion so
something is genuinely verified:

```python
from rustest import fixture

@fixture
def sample():
    return "test"

def test_example(sample):
    assert sample == "test"

# Doc blocks run at module level, so exercise the body directly.
test_example("test")
```

This page's own limitation applies to every page in a documentation suite: a block whose
assertions all sit inside a `def test_*` is checked for syntax and imports, and for nothing
else.

## Code Block Sharing State

The fresh namespace above is what this looks like in practice. The second block here fails,
because `x` belongs to the first block's namespace and nothing carries it over:

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

<!--rustest.mark.skip-->
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

Name the pages, and let the shell expand a glob over the directory holding them. This is
rustest's own workflow step, testing its landing page and every page of this guide:

```yaml
# .github/workflows/ci.yml
- name: Test documentation examples
  run: python -m rustest README.md user_guide/*.md
```

Point the glob at whatever directory holds your pages. A bare directory argument in that
position silently tests nothing, and the step still goes green.

### Pre-commit Hook

pre-commit passes the changed filenames to the hook by default, which is exactly the naming
the collector needs. Restrict it to markdown with `files`:

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: test-docs
      name: Test documentation examples
      entry: rustest
      language: system
      files: \.md$
```

## Programmatic Usage

`rustest.run()` is keyword-only and returns pytest's exit code as an `int`. Pass
`report_json=` when you want counts:

<!--rustest.mark.skip-->
```python
import json
from pathlib import Path

from rustest import run

# One page. Returns 0 on success, 1 on failure.
exit_code = run(paths=["README.md"])

# Several pages, with a machine-readable report written alongside.
run(paths=["README.md", "user_guide/fixtures.md"], report_json="docs-report.json")
summary = json.loads(Path("docs-report.json").read_text())["summary"]
print(f"{summary['passed']} passed, {summary['failed']} failed")

# Turn the markdown tier off. A named .md then becomes a usage error.
run(paths=["tests/"], codeblocks=False)
```

## Limitations

- Blocks do not share state, and there is no continuation syntax across them
- Only Python fences are collected
- A block must be a complete, compilable Python fragment unless it is marked skipped
- Interactive console transcripts are not recognised; render them as `text` if you need them
- A code block requests no fixtures. Autouse fixtures from a `conftest.py` above the file
  still apply, but a block cannot ask for one by name

## Next Steps

- [CLI Usage](cli.md) - Learn about --no-codeblocks and other options
- [Python API](python-api.md) - Control markdown testing programmatically
- [Writing Tests](writing-tests.md) - Learn about regular Python tests
