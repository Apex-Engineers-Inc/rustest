# CLI Usage

The rustest command-line interface provides a simple and powerful way to run your tests.

`rustest <paths>` is the whole invocation. There is one engine and the pytest
compatibility shim is always installed, so `import pytest` resolves to rustest's own
implementation on every run — no flag turns that on and no flag turns it off. Two flags
that used to exist are gone and now **exit 4** with a message naming the change:

| Removed flag | What replaced it |
|---|---|
| `--pytest-compat` | Nothing — compatibility is the default behaviour, not a mode. See [pytest compatibility](pytest-compat.md). |
| `--v1` | Nothing — the legacy engine it selected was deleted, not frozen. |

`--v2` survives as an accepted no-op so that scripts written during the transition keep
running.

## Quick Reference

```bash
rustest --help
```

```
usage: rustest [-h] [-k PATTERN] [-m MARK_EXPR] [-n WORKERS] [-s] [-v] [-q]
               [--ascii] [--color {auto,always,never,yes,no}]
               [-o OPTION=VALUE] [--maxfail NUM] [--no-codeblocks] [--lf]
               [--ff] [-x] [--report-json PATH] [--cov [SOURCE]]
               [--cov-report TYPE] [--cov-branch] [--llm] [--llm-full]
               [--llm-schema] [--version] [--v2-collect-only] [--v2]
               [paths ...]

Run Python tests at blazing speed with a Rust powered core.

positional arguments:
  paths                 Files or directories to collect tests from.

options:
  -h, --help            show this help message and exit
  -k, --pattern PATTERN
                        Substring to filter tests by (case insensitive).
  -m, --marks MARK_EXPR
                        Run tests matching the given mark expression (e.g.,
                        "slow", "not slow", "slow and integration").
  -n, --workers WORKERS
                        Number of worker processes to use (default: 4, capped
                        by CPU count).
  -s, --no-capture      Do not capture stdout/stderr during test execution.
  -v, --verbose         Print one PASSED/FAILED line per test.
  -q, --quiet           Print only the summary line.
  --ascii               Accepted and ignored: the default engine's output is
                        already plain ASCII.
  --color {auto,always,never,yes,no}
                        Accepted and ignored: the default engine's output is
                        not colored.
  -o, --override-ini OPTION=VALUE
                        Override an ini option, e.g. -o addopts=. Supported
                        key: addopts. Registered here for --help and usage
                        errors; the values are consumed by
                        _extract_ini_overrides before the config is resolved,
                        because addopts has to be known before argv is
                        assembled.
  --maxfail NUM         Exit after the first NUM failures or errors (0 means
                        no limit).
  --no-codeblocks       Disable code block tests from markdown files.
  --lf, --last-failed   Rerun only the tests that failed in the last run.
  --ff, --failed-first  Run previously failed tests first, then all other
                        tests.
  -x, --exitfirst       Exit instantly on first error or failed test.
  --report-json PATH    Write a machine-readable JSON report to PATH.
  --cov [SOURCE]        Measure line coverage of SOURCE (a directory;
                        repeatable). With no value, measures the rootdir.
                        Needs the `cov` extra.
  --cov-report TYPE     Coverage report to produce: `term` (default) or
                        `xml[:PATH]`. Repeatable.
  --cov-branch          Not implemented: rustest measures line coverage only.
                        Refused rather than ignored.
  --llm                 Emit the run as JSONL on stdout for LLM tooling: one
                        object per line, meta first and a summary sentinel
                        last. Replaces the human output entirely.
  --llm-full            With --llm: attach captured output whole, instead of
                        the last 50 lines. Refused on its own, because it
                        would be inert.
  --llm-schema          Print the JSON Schema for --llm output on stdout and
                        exit 0. Runs nothing; every other option is ignored.
  --version             Print the rustest version on stdout and exit 0. Runs
                        nothing.
  --v2-collect-only     Collect tests and print their node ids one per line,
                        without running anything. Honours -k, -m and -n. Exits
                        0 with tests, 5 with none, 2 on collection errors.
                        None of the other options apply.
  --v2                  Deprecated no-op: there is one engine. Accepted so old
                        scripts keep working.
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

FAILURES
test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken
  → assert 2 + 2 == 5, "Math is broken"

test_failing_2 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: String doesn't start with x
  → assert "world".startswith("x"), "String doesn't start with x"

✗ 5/5 3 passing, 2 failed (1ms)
```

```bash
# Run only the 2 failed tests
rustest test_workflow.py --lf
```

```
✗✗

FAILURES
test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken
  → assert 2 + 2 == 5, "Math is broken"

test_failing_2 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: String doesn't start with x
  → assert "world".startswith("x"), "String doesn't start with x"

✗ 2/2 2 failed (1ms)
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

FAILURES
test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken
  → assert 2 + 2 == 5, "Math is broken"

test_failing_2 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: String doesn't start with x
  → assert "world".startswith("x"), "String doesn't start with x"

✗ 5/5 3 passing, 2 failed (1ms)
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

FAILURES
test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken
  → assert 2 + 2 == 5, "Math is broken"

✗ 3/3 2 passing, 1 failed (1ms)
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

FAILURES
test_failing_1 (test_workflow.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Math is broken
  → assert 2 + 2 == 5, "Math is broken"

✗ 1/1 1 failed (1ms)
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

### Collection Feedback

When rustest starts, it shows real-time progress during test collection:

```
⠋ Collecting tests • 52 files, 893 tests 0:00:00
✓ Collected 893 tests from 52 files (531ms)
```

The spinner animates while scanning your codebase, with live updates showing the number of files and tests discovered. After collection completes, a summary shows the total count and duration. This helps you know rustest is working when scanning large codebases.

If no tests are found, you'll see:

```
No tests collected (45ms)
```

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

✗ 5/5 3 passing, 1 failed, 1 skipped (3ms)
```

**Verbose output:**
```
/home/user/project/tests/test_math.py
  ✓ test_addition 0ms
  ✓ test_subtraction 1ms
  ✓ test_multiplication 0ms
  ⊘ test_future_feature 0ms
  ✗ test_division_error 2ms

FAILURES
test_division_error (test_math.py)
──────────────────────────────────────────────────────────────────────
✗ AssertionError: Expected 5, got 4

✗ 5/5 3 passing, 1 failed, 1 skipped (3ms)
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

### Machine-Readable Output (`--llm`)

`--llm` replaces the human output with **JSONL** — one JSON object per line, meta
header first and a summary sentinel last — for LLM coding agents and other tools
that parse test output rather than read it.

```bash
rustest tests/ --llm              # failures + summary, as JSONL on stdout
rustest tests/ --llm --llm-full   # do not truncate captured output
rustest --llm-schema              # print the JSON Schema and exit 0
```

The exit code is unchanged, and `stdout` is JSONL and nothing else. See
[LLM Output Mode](llm-output.md) for the full contract, the verbosity ladder and
the `--lf` agent loop.

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
| `-n WORKERS, --workers WORKERS` | Number of worker processes (default 4, capped by CPU count) |
| `-s, --no-capture` | Don't capture stdout/stderr during test execution. Output reaches **stderr**, because a worker's stdout is the orchestrator protocol channel |
| `-v, --verbose` | One `PASSED`/`FAILED` line per test, with pytest's wording and percent column |
| `-q, --quiet` | Only the summary line. `-v` and `-q` cancel out, as they do under pytest |
| `-o, --override-ini OPTION=VALUE` | Override an ini option, e.g. `-o addopts=`. Supported key: `addopts` |
| `--maxfail NUM` | Exit after the first `NUM` failures or errors (`0` means no limit) |
| `--ascii` | Accepted and ignored: the output is already plain ASCII |
| `--color {auto,always,never,yes,no}` | Accepted and ignored: the output is not colored |
| `--no-codeblocks` | Disable markdown code block testing |
| `--lf, --last-failed` | Rerun only tests that failed in the last run |
| `--ff, --failed-first` | Run failed tests first, then all other tests |
| `-x, --exitfirst` | Exit instantly on first error or failed test (`--maxfail=1`) |
| `--report-json PATH` | Write a machine-readable JSON report to `PATH` |
| `--cov [SOURCE]` | Measure line coverage of `SOURCE` (a directory; repeatable, no value means the rootdir). Needs `pip install 'rustest[cov]'`. See [Coverage](coverage.md) |
| `--cov-report TYPE` | `term` (default) or `xml[:PATH]`. Repeatable |
| `--cov-branch` | Refused: branch coverage is not implemented, and measuring lines instead would overstate it |
| `--llm` | Emit the run as JSONL on stdout for LLM tooling. See [LLM output](llm-output.md) |
| `--llm-full` | With `--llm`: attach captured output whole instead of the last 50 lines. Refused on its own |
| `--llm-schema` | Print the JSON Schema for `--llm` output and exit 0 |
| `--version` | Print the installed version (`rustest 1.0.0rc1`) and exit 0. Runs nothing |
| `--v2-collect-only` | Collect and print node ids, one per line, without running anything |
| `--v2` | Deprecated no-op: there is one engine. Accepted so old scripts keep working |
| `-h, --help` | Show help message and exit |

## Checking the Version

```bash
rustest --version    # -> rustest 1.0.0rc1
```

Prints the installed version on `stdout` and exits 0, running nothing. Like `--llm-schema`,
it is a question rather than a run, so it is answered without collecting anything — it works
from a directory with no tests in it, and from a project whose `addopts` this runner would
otherwise refuse.

The string is read from the installed distribution's metadata, which is the same source the
`meta` line of [`--llm`](llm-output.md) reports, so the two cannot disagree.

## Exit Codes

Rustest uses pytest's exit codes:

| Code | Meaning |
|---|---|
| `0` | All tests passed |
| `1` | One or more tests failed |
| `2` | Session interrupted — a collection error, or `pytest.exit()` |
| `4` | Usage error: an unrecognised or removed flag, or a flag that would be inert |
| `5` | No tests were collected |

Exit **4** is the one worth knowing about when upgrading: it is what `--pytest-compat`
and `--v1` now produce, and what `--llm-full` produces without `--llm`. This CLI refuses
flags it cannot honour rather than accepting them silently.

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

rustest measures line coverage itself, and writes coverage.py's own data format:

```bash
pip install 'rustest[cov]'

rustest --cov=src tests/                  # terminal table
rustest --cov=src --cov-report=xml tests/ # Cobertura XML

# the run wrote an ordinary .coverage, so every other report is one command away
coverage html
```

Branch coverage is not implemented; run rustest under coverage.py for that:

```bash
coverage run --branch --source=src -m rustest tests/
coverage report
```

See [Coverage](coverage.md) for the full surface and its accuracy.

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
