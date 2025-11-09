# Test Execution

The `run()` function executes tests programmatically and returns detailed results.

## run

::: rustest.core.run

## Parameters

### paths

**Type:** `Sequence[str]` (required)

List of paths to test files or directories. Rustest will discover tests in:
- Python files matching `test_*.py` or `*_test.py`
- Markdown files (`.md`) with Python code blocks (if `enable_codeblocks=True`)

```python
from rustest import run

# Single directory
report = run(paths=["tests"])

# Multiple paths
report = run(paths=["tests", "examples/tests"])

# Single file
report = run(paths=["README.md"])
```

### pattern

**Type:** `str | None` (optional, default: `None`)

Filter tests by name using pattern matching. Same as the CLI `-k` option.

```python
from rustest import run

# Run only user-related tests
report = run(paths=["tests"], pattern="user")

# Exclude slow tests
report = run(paths=["tests"], pattern="not slow")

# Complex patterns
report = run(paths=["tests"], pattern="user and not slow")
```

### workers

**Type:** `int | None` (optional, default: `None`)

Reserved for future parallel execution support. Currently not implemented.

### capture_output

**Type:** `bool` (optional, default: `True`)

Whether to capture stdout/stderr during test execution.

```python
from rustest import run

# Capture output (default)
report = run(paths=["tests"], capture_output=True)

# See print statements during execution
report = run(paths=["tests"], capture_output=False)
```

### enable_codeblocks

**Type:** `bool` (optional, default: `True`)

Whether to test Python code blocks in markdown files.

```python
from rustest import run

# Test markdown code blocks (default)
report = run(paths=["docs"], enable_codeblocks=True)

# Skip markdown code blocks
report = run(paths=["docs"], enable_codeblocks=False)
```

## Return Value

Returns a [`RunReport`](reporting.md#runreport) object containing test results and statistics.

## Examples

### Basic Usage

<!--pytest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])

print(f"Total: {report.total}")
print(f"Passed: {report.passed}")
print(f"Failed: {report.failed}")
print(f"Skipped: {report.skipped}")
print(f"Duration: {report.duration:.3f}s")
```

### With Pattern Filtering

<!--pytest.mark.skip-->
```python
from rustest import run

# Run integration tests only
report = run(paths=["tests"], pattern="integration")

if report.failed == 0:
    print(f"All {report.total} integration tests passed!")
```

### Without Output Capture

<!--pytest.mark.skip-->
```python
from rustest import run

# Useful for debugging
report = run(paths=["tests"], capture_output=False)
```

### Analyzing Results

<!--pytest.mark.skip-->
```python
from rustest import run

report = run(paths=["tests"])

# Check for failures
if report.failed > 0:
    print("Failed tests:")
    for result in report.iter_status("failed"):
        print(f"  - {result.name}: {result.message}")

# Find slow tests
slow_tests = [r for r in report.results if r.duration > 1.0]
if slow_tests:
    print("Slow tests:")
    for test in slow_tests:
        print(f"  - {test.name}: {test.duration:.3f}s")
```

### In a Script

<!--pytest.mark.skip-->
```python
#!/usr/bin/env python3
from rustest import run
import sys

def main():
    report = run(paths=["tests"], capture_output=True)

    # Print summary
    print(f"\n{report.total} tests: {report.passed} passed, "
          f"{report.failed} failed, {report.skipped} skipped\n")

    # Exit with error code if tests failed
    sys.exit(1 if report.failed > 0 else 0)

if __name__ == "__main__":
    main()
```

## See Also

- [RunReport](reporting.md#runreport) - Return value documentation
- [Python API Guide](../guide/python-api.md) - Detailed usage examples
- [CLI Usage](../guide/cli.md) - Command-line equivalent
