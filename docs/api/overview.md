# API Reference Overview

This section provides detailed API documentation for all public interfaces in rustest.

## Main Modules

### [Decorators](decorators.md)
Test decorators including `@fixture`, `@parametrize`, `@skip`, `@mark`, and exception handling with `raises()`.

### [Test Execution](core.md)
The `run()` function for programmatic test execution.

### [Reporting](reporting.md)
Test result objects: `RunReport` and `TestResult`.

### [Assertion Utilities](approx.md)
The `approx()` function for floating-point comparisons.

## Quick Reference

### Importing

```python
# Import everything
from rustest import (
    fixture,
    parametrize,
    skip,
    mark,
    raises,
    approx,
    run,
    RunReport,
    TestResult,
)

# Or import specific items
from rustest import fixture, parametrize
from rustest import run, RunReport
```

### Decorators

```python
from rustest import fixture, parametrize, skip, mark

@fixture(scope="function")  # or "class", "module", "session"
def my_fixture():
    return data

@parametrize("arg1,arg2", [(val1, val2), ...], ids=["id1", ...])
def test_function(arg1, arg2):
    pass

@skip("reason")
def test_skip():
    pass

@mark.slow
@mark.integration
def test_marked():
    pass
```

### Assertions

```python
from rustest import approx, raises

# Floating-point comparison
assert 0.1 + 0.2 == approx(0.3)
assert value == approx(expected, rel=1e-6, abs=1e-12)

# Exception testing
with raises(ValueError):
    risky_operation()

with raises(ValueError, match="pattern"):
    risky_operation()
```

### Test Execution

```python
from rustest import run

report = run(
    paths=["tests"],
    pattern="user",
    capture_output=True,
    enable_codeblocks=True
)

print(f"{report.passed}/{report.total} passed")
```

## Type Annotations

All public APIs include full type annotations for use with type checkers like mypy, pyright, or basedpyright:

```python
from rustest import run, RunReport
from typing import Optional

def run_tests(path: str, pattern: Optional[str] = None) -> RunReport:
    return run(paths=[path], pattern=pattern)
```

## Next Steps

- [Decorators](decorators.md) - Detailed decorator documentation
- [Test Execution](core.md) - `run()` function reference
- [Reporting](reporting.md) - Result objects
- [Assertion Utilities](approx.md) - `approx()` function
