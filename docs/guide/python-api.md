# Python API

You can run rustest programmatically from Python using the `run()` function. This is useful for custom test runners, integrations, and automation.

## Basic Usage

```python
from rustest import run

# Run tests in a directory
report = run(paths=["tests"])

# Check results
print(f"Passed: {report.passed}")
print(f"Failed: {report.failed}")
print(f"Total: {report.total}")
```

## The run() Function

::: rustest.core.run
    options:
      show_source: true

### Parameters

- **paths** (required): List of paths to test files or directories
- **pattern**: Filter tests by name (same as CLI `-k`)
- **workers**: Number of parallel workers (reserved for future use)
- **capture_output**: Capture stdout/stderr during tests (default: `True`)
- **enable_codeblocks**: Test markdown code blocks (default: `True`)

### Returns

Returns a `RunReport` object containing test results and statistics.

## RunReport Object

The `run()` function returns a `RunReport` with the following attributes:

```python
from rustest import run

report = run(paths=["tests"])

# Summary statistics
print(report.total)     # Total number of tests
print(report.passed)    # Number of passed tests
print(report.failed)    # Number of failed tests
print(report.skipped)   # Number of skipped tests
print(report.duration)  # Total execution time in seconds

# Access individual test results
for result in report.results:
    print(f"{result.name}: {result.status}")
```

## TestResult Object

Each test result has the following attributes:

```python
from rustest import run

report = run(paths=["tests"])

for result in report.results:
    print(f"Name: {result.name}")        # Test name
    print(f"Path: {result.path}")        # File path
    print(f"Status: {result.status}")    # "passed", "failed", or "skipped"
    print(f"Duration: {result.duration}") # Execution time in seconds
    print(f"Message: {result.message}")  # Error message (if failed)
    print(f"Stdout: {result.stdout}")    # Captured stdout
    print(f"Stderr: {result.stderr}")    # Captured stderr
```

## Examples

### Basic Test Execution

```python
from rustest import run
import os

# Run tests if directory exists
if os.path.exists("tests"):
    report = run(paths=["tests"])

    if report.failed == 0:
        print("All tests passed!")
    else:
        print(f"{report.failed} test(s) failed")
```

### With Pattern Filtering

```python
from rustest import run

# Run only user-related tests
report = run(paths=["tests"], pattern="user")

print(f"User tests: {report.passed}/{report.total} passed")
```

### Disable Output Capture

```python
from rustest import run

# See print statements during test execution
report = run(paths=["tests"], capture_output=False)
```

### Disable Markdown Testing

```python
from rustest import run

# Only test Python files, skip markdown
report = run(paths=["tests"], enable_codeblocks=False)
```

### Analyzing Test Results

```python
from rustest import run

report = run(paths=["tests"])

# Find failed tests
failed_tests = [r for r in report.results if r.status == "failed"]
for test in failed_tests:
    print(f"FAILED: {test.name}")
    print(f"  {test.message}")
    print()

# Find slow tests
slow_tests = [r for r in report.results if r.duration > 1.0]
for test in slow_tests:
    print(f"SLOW: {test.name} ({test.duration:.3f}s)")
```

### Using iter_status()

The `RunReport` object provides a helper method to filter results by status:

```python
from rustest import run

report = run(paths=["tests"])

# Get only failed tests
for test in report.iter_status("failed"):
    print(f"Failed: {test.name} - {test.message}")

# Get only passed tests
for test in report.iter_status("passed"):
    print(f"Passed: {test.name}")

# Get only skipped tests
for test in report.iter_status("skipped"):
    print(f"Skipped: {test.name}")
```

### Custom Test Runner

```python
from rustest import run
import sys

def run_tests(*paths, fail_fast=False):
    """Custom test runner with fail-fast support."""
    report = run(paths=list(paths))

    # Print summary
    print(f"\n{report.total} tests: {report.passed} passed, "
          f"{report.failed} failed, {report.skipped} skipped "
          f"in {report.duration:.3f}s\n")

    # Exit with appropriate code
    sys.exit(1 if report.failed > 0 else 0)

if __name__ == "__main__":
    run_tests("tests", "integration")
```

### Integration with CI/CD

```python
from rustest import run
import json
import sys

def run_ci_tests():
    """Run tests and output JSON report for CI."""
    report = run(paths=["tests"])

    # Create JSON report
    results = {
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
        "duration": report.duration,
        "tests": [
            {
                "name": r.name,
                "status": r.status,
                "duration": r.duration,
                "message": r.message,
            }
            for r in report.results
        ]
    }

    # Write to file
    with open("test-results.json", "w") as f:
        json.dump(results, f, indent=2)

    # Exit with failure if any tests failed
    sys.exit(1 if report.failed > 0 else 0)

if __name__ == "__main__":
    run_ci_tests()
```

### Parallel Test Execution (Future)

!!! note "Parallel Execution"
    The `workers` parameter is reserved for future parallel test execution support.

```python
from rustest import run

# Future: parallel execution
# report = run(paths=["tests"], workers=4)

# Currently: sequential execution only
report = run(paths=["tests"])
```

### Testing Specific Files

<!--pytest.mark.skip-->
```python
from rustest import run

# Test specific files
report = run(paths=[
    "tests/test_user.py",
    "tests/test_auth.py",
    "README.md"
])
```

### Conditional Testing

```python
from rustest import run
import os

# Run different tests based on environment
env = os.getenv("ENV", "development")

if env == "production":
    report = run(paths=["tests"], pattern="smoke")
elif env == "integration":
    report = run(paths=["tests"], pattern="integration")
else:
    report = run(paths=["tests"])

print(f"Environment: {env}")
print(f"Tests: {report.passed}/{report.total} passed")
```

### Pre-commit Hook

```python
#!/usr/bin/env python3
"""Pre-commit hook to run tests."""

from rustest import run
import sys

def main():
    # Run fast tests only
    report = run(
        paths=["tests"],
        pattern="not slow",
        capture_output=True
    )

    if report.failed > 0:
        print("\n❌ Tests failed. Commit aborted.")
        print(f"{report.failed} test(s) failed\n")

        # Show failed tests
        for test in report.iter_status("failed"):
            print(f"  • {test.name}")
            if test.message:
                print(f"    {test.message}")

        sys.exit(1)

    print(f"\n✅ All tests passed ({report.total} tests)")
    sys.exit(0)

if __name__ == "__main__":
    main()
```

### Custom Reporting

```python
from rustest import run
from datetime import datetime

def run_with_custom_report():
    """Run tests with custom reporting."""
    start = datetime.now()

    report = run(paths=["tests"], capture_output=False)

    end = datetime.now()
    elapsed = (end - start).total_seconds()

    # Custom report
    print("\n" + "=" * 60)
    print(f"Test Run Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print(f"Total Tests: {report.total}")
    print(f"Passed:      {report.passed} ({report.passed/report.total*100:.1f}%)")
    print(f"Failed:      {report.failed}")
    print(f"Skipped:     {report.skipped}")
    print(f"Duration:    {report.duration:.3f}s (wall: {elapsed:.3f}s)")
    print("=" * 60)

    if report.failed > 0:
        print("\nFailed Tests:")
        for test in report.iter_status("failed"):
            print(f"  ❌ {test.name}")
            if test.message:
                print(f"     {test.message}")
    else:
        print("\n✅ All tests passed!")

run_with_custom_report()
```

## Type Hints

Rustest provides full type hints for the Python API:

```python
from rustest import run, RunReport, TestResult
from typing import Optional

def run_tests(path: str, pattern: Optional[str] = None) -> RunReport:
    """Type-safe test runner."""
    report: RunReport = run(
        paths=[path],
        pattern=pattern,
        capture_output=True
    )
    return report

# IDE autocomplete and type checking work perfectly
report = run_tests("tests", pattern="user")
print(report.passed)  # Type: int
```

## Error Handling

```python
from rustest import run

try:
    report = run(paths=["tests"])

    # Check for failures
    if report.failed > 0:
        print("Tests failed!")
        for test in report.iter_status("failed"):
            print(f"  {test.name}: {test.message}")

except Exception as e:
    print(f"Error running tests: {e}")
    raise
```

## Best Practices

### Check for Test Failures

```python
from rustest import run
import sys

report = run(paths=["tests"])

# Always check failures and exit appropriately
if report.failed > 0:
    sys.exit(1)
```

### Use Pattern Filtering

<!--pytest.mark.skip-->
```python
# Good - filter tests programmatically
report = run(paths=["tests"], pattern="integration")

# Less efficient - filter results after running
report = run(paths=["tests"])
integration_tests = [r for r in report.results if "integration" in r.name]
```

### Capture Output Appropriately

<!--pytest.mark.skip-->
```python
# In CI - capture output
report = run(paths=["tests"], capture_output=True)

# During development - see output
report = run(paths=["tests"], capture_output=False)
```

## Next Steps

- [CLI Usage](cli.md) - Command-line interface
- [API Reference](../api/overview.md) - Complete API documentation
- [Writing Tests](writing-tests.md) - Create tests to run with the API
