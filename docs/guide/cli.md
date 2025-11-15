# CLI Usage

The rustest command-line interface provides a simple and powerful way to run your tests.

## Quick Reference

```bash
rustest --help
```

```
usage: rustest [-h] [-k PATTERN] [-m MARK_EXPR] [-n WORKERS] [--no-capture]
               [-v] [--ascii] [--no-color] [--no-codeblocks] [--lf] [--ff]
               [-x]
               [paths ...]

Run Python tests at blazing speed with a Rust powered core.

positional arguments:
  paths                 Files or directories to collect tests from.

options:
  -h, --help            show this help message and exit
  -k PATTERN, --pattern PATTERN
                        Substring to filter tests by (case insensitive).
  -m MARK_EXPR, --marks MARK_EXPR
                        Run tests matching the given mark expression (e.g.,
                        "slow", "not slow", "slow and integration").
  -n WORKERS, --workers WORKERS
                        Number of worker slots to use (experimental).
  --no-capture          Do not capture stdout/stderr during test execution.
  -v, --verbose         Show verbose output with hierarchical test structure.
  --ascii               Use ASCII characters instead of Unicode symbols for
                        output.
  --no-color            Disable colored output.
  --no-codeblocks       Disable code block tests from markdown files.
  --lf, --last-failed   Rerun only the tests that failed in the last run.
  --ff, --failed-first  Run previously failed tests first, then all other
                        tests.
  -x, --exitfirst       Exit instantly on first error or failed test.

```

## Basic Commands

### Running All Tests

```bash
# Run all tests in current directory
rustest

# Run all tests in specific directory
rustest tests/

# Run tests in multiple directories
rustest tests/ integration/ e2e/
```

### Test Discovery and Directory Exclusion

Rustest automatically discovers test files matching the patterns `test_*.py` and `*_test.py`, while **intelligently excluding directories that shouldn't contain tests**. This behavior exactly matches pytest's defaults.

#### Automatically Excluded Directories

The following directories are excluded from test discovery to prevent running tests from dependencies:

**Virtual Environments:**
- `venv`, `.venv` - Standard Python virtual environments
- Any directory containing `pyvenv.cfg` (PEP 405 marker)
- Any directory containing `conda-meta/history` (conda environments)

**Build Artifacts:**
- `build` - Build output directories
- `dist` - Distribution packages
- `*.egg` - Python egg directories

**Hidden Directories:**
- `.*` - Any directory starting with a dot (`.git`, `.pytest_cache`, `.tox`, etc.)

**Version Control:**
- `CVS`, `_darcs` - Legacy version control systems

**Other:**
- `node_modules` - Node.js dependencies
- `{arch}` - Arch Linux package directories

#### Why This Matters

When you run `rustest` without specifying a path, it searches the current directory for tests. Without directory exclusion, rustest would discover and run tests from your virtual environment's site-packages, which can be slow and produce confusing results:

```bash
# Without exclusions (old behavior):
rustest  # Would find thousands of tests in venv/lib/python3.11/site-packages/

# With exclusions (current behavior):
rustest  # Only finds your project's tests
```

#### Customizing Test Discovery

If you need to test specific directories that would normally be excluded, explicitly specify them:

```bash
# Test a specific directory that would normally be excluded
rustest .venv/custom_tests/

# Test specific files in build directory
rustest build/generated_tests/test_*.py
```

!!! tip "Pytest Compatibility"
    This directory exclusion behavior exactly matches pytest's default `norecursedirs` patterns, making rustest a true drop-in replacement.

### Running Specific Files

```bash
# Run a single test file
rustest tests/test_math.py

# Run multiple files
rustest tests/test_math.py tests/test_strings.py

# Run markdown files
rustest README.md docs/*.md
```

## Filtering Tests

### Pattern Matching (-k)

Filter tests by name pattern:

```bash
# Run tests with "user" in the name
rustest -k "user"
# Matches: test_user_login, test_create_user, test_user_email, etc.

# Run tests with "auth" in the name
rustest -k "auth"
# Matches: test_authentication, test_authorize, etc.

# Multiple patterns (OR logic)
rustest -k "user or admin"
# Matches tests with either "user" OR "admin"

# Exclude patterns (NOT logic)
rustest -k "test_user and not slow"
# Matches tests with "test_user" but NOT "slow"
```

Pattern matching works on:
- Test function names
- Test class names
- Test file names
- Parametrized test IDs

### Examples

```bash
# Run all database tests
rustest -k "database"

# Run integration tests
rustest -k "integration"

# Run all tests except slow ones
rustest -k "not slow"

# Run critical user tests
rustest -k "user and critical"
```

## Test Workflow Options

### Last Failed Tests (--lf)

Rerun only tests that failed in the previous run. This is helpful for quickly iterating on fixes:

```bash
# First run - some tests fail
rustest test_workflow.py
```

```
✓✓✗✓✗

test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken

  → assert 2 + 2 == 5, "Math is broken"

test_failing_2 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: String doesn't start with x

  → assert "world".startswith("x"), "String doesn't start with x"

✗ 5 tests: 3 passed, 2 failed, 0 skipped in 0.00s
```

```bash
# Run only the 2 failed tests
rustest test_workflow.py --lf
```

```
✗✗

test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken

  → assert 2 + 2 == 5, "Math is broken"

test_failing_2 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: String doesn't start with x

  → assert "world".startswith("x"), "String doesn't start with x"

✗ 2 tests: 0 passed, 2 failed, 0 skipped in 0.00s
```

!!! tip "Cache Location"
    Failed test information is stored in `.rustest_cache/lastfailed`. This file is automatically created and updated after each test run.

### Failed First (--ff)

Run previously failed tests first, then continue with all other tests. This helps you see failures quickly while still running the full suite:

```bash
# Run failed tests first, then all others
rustest test_workflow.py --ff
```

```
✗✗✓✓✓

test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken

  → assert 2 + 2 == 5, "Math is broken"

test_failing_2 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: String doesn't start with x

  → assert "world".startswith("x"), "String doesn't start with x"

✗ 5 tests: 3 passed, 2 failed, 0 skipped in 0.00s
```

Notice the output shows `✗✗✓✓✓` - failed tests run first!

### Fail Fast (-x)

Stop execution immediately after the first test failure. Useful for quick feedback during development:

```bash
# Stop on first failure
rustest test_workflow.py -x
```

```
✓✓✗

test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken

  → assert 2 + 2 == 5, "Math is broken"

✗ 3 tests: 2 passed, 1 failed, 0 skipped in 0.00s
```

Only 3 tests ran instead of all 5 - execution stopped after the first failure!

### Combining Workflow Options

Combine `--ff` and `-x` to run failed tests first and stop on first failure:

```bash
# Run failed tests first, stop on first failure
rustest test_workflow.py --ff -x
```

```
✗

test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken

  → assert 2 + 2 == 5, "Math is broken"

✗ 1 tests: 0 passed, 1 failed, 0 skipped in 0.00s
```

Only the first failed test ran! This is extremely fast for iterative development.

### Workflow Use Cases

```bash
# Quick fix iteration - run only what failed
rustest --lf

# CI pipeline - see failures first but run everything
rustest --ff

# Local development - fast feedback on first issue
rustest -x

# Super fast iteration - fix one failure at a time
rustest --ff -x

# Combine with pattern filtering
rustest -k "database" --lf      # Only failed database tests
rustest -k "integration" -x     # Stop on first integration test failure
```

!!! tip "Pytest Compatibility"
    These options work exactly like pytest's `--lf`, `--ff`, and `-x` flags, making rustest a drop-in replacement for your existing workflow.

## Output Control

### Verbose Mode

Show detailed test information with names and timing:

```bash
# Default: compact output (✓✗⊘ symbols only)
rustest

# Verbose: show test names and timing
rustest -v
rustest --verbose
```

**Compact output:**
```
✓✓✓⊘✗

5 tests: 3 passed, 1 failed, 1 skipped in 0.003s
```

**Verbose output:**
```
/home/user/project/tests/test_math.py
  ✓ test_addition 0ms
  ✓ test_subtraction 1ms
  ✓ test_multiplication 0ms
  ⊘ test_future_feature 0ms
  ✗ test_division_error 2ms
    AssertionError: Expected 5, got 4

5 tests: 3 passed, 1 failed, 1 skipped in 0.003s
```

!!! tip "Output Symbols"
    - `✓` = Passed test
    - `✗` = Failed test
    - `⊘` = Skipped test

### Capture Mode

By default, rustest captures stdout/stderr during tests:

```bash
# Default: capture output
rustest

# Disable capture to see print statements
rustest --no-capture
```

Example with output:

```python
def test_with_print():
    print("Debug information")
    print(f"Value: {calculate()}")
    assert True
```

```bash
# Won't see prints
rustest

# Will see prints
rustest --no-capture
```

## Markdown Code Block Testing

### Enable/Disable

```bash
# Default: test markdown code blocks
rustest

# Disable markdown testing
rustest --no-codeblocks

# Test only markdown files
rustest docs/*.md

# Test markdown with other tests
rustest tests/ README.md
```

## Command-Line Reference

### Full Command Format

```bash
rustest [OPTIONS] [PATHS...]
```

### Options

| Option | Description |
|--------|-------------|
| `[PATHS...]` | Paths to test files or directories (default: current directory) |
| `-k PATTERN, --pattern PATTERN` | Substring to filter tests by (case insensitive) |
| `-m MARK_EXPR, --marks MARK_EXPR` | Run tests matching mark expression (e.g., "slow", "not slow") |
| `-n WORKERS, --workers WORKERS` | Number of worker slots to use (experimental) |
| `--no-capture` | Don't capture stdout/stderr during test execution |
| `-v, --verbose` | Show verbose output with hierarchical test structure |
| `--ascii` | Use ASCII characters instead of Unicode symbols |
| `--no-color` | Disable colored output |
| `--no-codeblocks` | Disable markdown code block testing |
| `--lf, --last-failed` | Rerun only tests that failed in the last run |
| `--ff, --failed-first` | Run failed tests first, then all other tests |
| `-x, --exitfirst` | Exit instantly on first error or failed test |
| `-h, --help` | Show help message and exit |

## Exit Codes

Rustest uses standard exit codes:

- `0`: All tests passed
- `1`: One or more tests failed
- Other: Error occurred (e.g., no tests found, invalid arguments)

Use in scripts:

```bash
#!/bin/bash

if rustest; then
    echo "Tests passed!"
else
    echo "Tests failed!"
    exit 1
fi
```

## Real-World Examples

### Development Workflow

```bash
# Quick test during development
rustest -k "test_feature" --no-capture

# Test specific component
rustest tests/test_user_service.py

# Test and see debug output
rustest --no-capture

# Fix-iterate workflow with last failed
rustest --lf                      # Run only failed tests
# Fix the issue, then run again
rustest --lf                      # Verify the fix

# Fast feedback during TDD
rustest -x                        # Stop on first failure
# Fix issue
rustest -x                        # Continue to next failure

# Maximum speed iteration
rustest --ff -x                   # Run failed tests first, stop on first failure
```

### CI/CD Pipeline

```bash
# Run all tests
rustest

# Run fast tests only
rustest -k "not slow"

# Run smoke tests
rustest -k "smoke"

# Run different test levels separately
rustest -k "unit"
rustest -k "integration"
rustest -k "e2e"

# See failures first but run everything
rustest --ff                      # Failed tests run first for quick feedback

# Quick CI feedback (fail fast on main branch)
rustest -x                        # Stop on first failure to save CI time
```

### Pre-commit Checks

```bash
# Run fast tests before commit
rustest -k "not slow and not integration"

# Test changed files only (with git)
rustest $(git diff --name-only '*.py' | grep test_)
```

### Documentation Testing

```bash
# Test README examples
rustest README.md --no-capture

# Test all documentation
rustest docs/**/*.md

# Test docs without code blocks
rustest docs/ --no-codeblocks
```

## Advanced Usage

### Testing Specific Patterns

```bash
# Test only parametrized tests
rustest -k "case_"

# Test only fixture-related tests
rustest -k "fixture"

# Test specific test class
rustest -k "TestUserService"

# Test specific method in class
rustest -k "TestUserService and test_create"
```

### Combining Options

```bash
# Multiple options together
rustest tests/ -k "integration" --no-capture

# Test specific directory with pattern
rustest integration/ -k "database" --no-codeblocks

# Complex pattern with output
rustest -k "user and (create or update)" --no-capture
```

### Using with Other Tools

#### With Coverage

```bash
# Using coverage.py
coverage run -m rustest
coverage report
```

#### With Timeout

```bash
# Using timeout command (Unix/Linux)
timeout 60 rustest  # 60 second timeout
```

#### With Watch Tools

```bash
# Using entr (requires entr installed)
find . -name "*.py" | entr rustest

# Using watch
watch -n 2 rustest
```

## Module Invocation

Run rustest as a Python module:

```bash
# Same as rustest command
python -m rustest

# With options
python -m rustest tests/ -k "user"

# Useful in environments without PATH setup
python3 -m rustest
```

## Environment Variables

Rustest respects standard Python environment variables:

```bash
# Set Python path
PYTHONPATH=/path/to/src rustest

# Control Python behavior
PYTHONDONTWRITEBYTECODE=1 rustest

# Debug mode
PYTHONDEVMODE=1 rustest
```

## Troubleshooting

### No Tests Found

```bash
# Check test discovery
rustest tests/

# Verify file patterns
rustest tests/test_*.py

# Check current directory
rustest .
```

### Import Errors

```bash
# Set PYTHONPATH
PYTHONPATH=src:python rustest

# Or use Python module
python -m rustest
```

### See Test Output

```bash
# Use --no-capture to see print statements
rustest --no-capture
```

## Best Practices

### Use Pattern Matching Effectively

```bash
# Good - specific patterns
rustest -k "test_user_authentication"

# Good - logical grouping
rustest -k "integration and not slow"

# Less effective - too broad
rustest -k "test"
```

### Organize Tests for Easy Filtering

```python
# Name tests with clear patterns
def test_unit_calculation():  # Can filter with -k "unit"
    pass

def test_integration_database():  # Can filter with -k "integration"
    pass

def test_slow_full_workflow():  # Can filter with -k "slow"
    pass
```

### Use --no-capture Selectively

```bash
# During debugging - see all output
rustest --no-capture

# In CI - keep output clean
rustest

# For specific tests
rustest -k "debug" --no-capture
```

## Next Steps

- [Python API](python-api.md) - Run tests programmatically
- [Writing Tests](writing-tests.md) - Create discoverable tests
- [Marks & Skipping](marks.md) - Organize tests for filtering
