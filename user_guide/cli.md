# CLI Usage

`rustest <paths>` is the whole invocation. There is one engine and the pytest
compatibility shim is always installed, so `import pytest` resolves to rustest's own
implementation on every run. No flag turns that on and no flag turns it off. Two flags
that used to exist are gone and now **exit 4** with a message naming the change:

| Removed flag | What replaced it |
|---|---|
| `--pytest-compat` | Nothing. Compatibility is the default behaviour, not a mode. See [pytest compatibility](pytest-compat.md). |
| `--v1` | Nothing. The legacy engine it selected was deleted, not frozen. |

`--v2` and `--v2-collect-only` existed during the rewrite, while naming the engine still
distinguished something. Neither reached a release, so neither is listed above as a
removal. Collect-only is spelled `--collect-only` (or `--co`), as in pytest.

## Quick Reference

```bash
rustest --help
```

```
usage: rustest [-h] [-k PATTERN] [-m MARK_EXPR] [-n WORKERS] [-s] [-v] [-q]
               [--ascii] [--color {auto,always,never,yes,no}]
               [-o OPTION=VALUE] [--maxfail NUM] [--codeblocks]
               [--no-codeblocks] [--lf] [--ff] [-x] [--report-json PATH]
               [--cov [SOURCE]] [--cov-report TYPE] [--cov-branch] [--llm]
               [--llm-full] [--llm-schema] [--version] [--collect-only]
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
  -q, --quiet           Drop the per-test progress lines. Failures are still
                        reported.
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
  --codeblocks          Collect and run python code blocks in markdown files
                        named as arguments.
  --no-codeblocks       Do not collect code blocks, overriding any config
                        setting.
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
  --collect-only, --co  Collect tests and print their node ids one per line,
                        without running anything. Honours -k, -m and -n. Exits
                        0 with tests, 5 with none, 2 on collection errors.
                        None of the other options apply.
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

Rustest discovers test files matching `test_*.py` and `*_test.py`, and skips the directories pytest skips. Both lists are pytest's own ini defaults, `python_files` and `norecursedirs`, so a tree collects the same files under either runner.

#### Automatically Excluded Directories

The following directories are excluded from test discovery to prevent running tests from dependencies:

**Virtual Environments:**
- `venv` - named outright in `norecursedirs` (`.venv` is caught by the `.*` rule below)
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
- `{arch}` - GNU Arch revision-control directories

**Other:**
- `node_modules` - Node.js dependencies
- `__pycache__` - pruned unconditionally, ahead of the list above, exactly as pytest prunes it

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
    The list is pytest's default `norecursedirs`, plus the two prunes pytest applies
    unconditionally: `__pycache__`, and any directory that looks like a virtualenv root.
    An explicit path argument bypasses all of it, which is why the examples above work.

### Running Specific Files

```bash
# Run a single test file
rustest tests/test_math.py

# Run multiple files
rustest tests/test_math.py tests/test_strings.py

# Run markdown files (needs --codeblocks, or [tool.rustest] codeblocks = true in config --
# this repository sets the config key, which is why the plain form works from its own root)
rustest README.md user_guide/*.md --codeblocks
```

Markdown has to be **named**, as it is above. A directory argument collects no `.md` at
all, because pytest walking the same tree collects none either.

!!! warning "A `path::node::id` argument selects the file, not the node"
    Copying a node id out of a failure report and pasting it back as a path is pytest
    muscle memory, and it does not work here. Rustest ignores everything after `::` in a
    path argument, so `rustest test_mixed.py::test_a` runs **every** test in
    `test_mixed.py`. A node id that no longer exists behaves the same way, where pytest
    would answer `no tests ran`. A CI line aimed at one test can therefore go red for a
    neighbour, or green for a test that has been deleted.

    Select with `-k` instead, which does understand the parametrized id:
    `rustest test_mixed.py -k "test_a"`. The node ids rustest prints are byte-identical to
    pytest's; it is only selection *by* them that is missing.

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

An identifier matches if it is a case-insensitive substring of any of the test's node
names, which is pytest's own rule. Those names are:

- The test function's name, with its `[param_id]` suffix when it is parametrized, so
  `-k "test_param[1]"` selects a single case
- Every enclosing class name, outermost first
- The file's basename, including the `.py`
- Every directory component of the file's path below the rootdir
- The name of every mark the test carries

One difference from pytest is worth knowing if you rely on it: pytest treats
`parametrize` as a mark, so `-k parametrize` there selects every parametrized case.
Rustest consumes `@parametrize` into the test's id and records no mark of that name, so
the same expression selects nothing.

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
================================== FAILURES ===================================
_______________________________ test_failing_1 ________________________________
Traceback (most recent call last):
  File "/path/to/test_workflow.py", line 8, in test_failing_1
    assert 2 + 2 == 5, "Math is broken"
AssertionError: Math is broken
assert (2 + 2) == 5
_______________________________ test_failing_2 ________________________________
Traceback (most recent call last):
  File "/path/to/test_workflow.py", line 14, in test_failing_2
    assert "world".startswith("x"), "String doesn't start with x"
AssertionError: String doesn't start with x
assert False
 +  where False = <built-in method startswith of str object at 0x...>('x')
 +    where <built-in method startswith of str object at 0x...> = 'world'.startswith
=========================== short test summary info ===========================
FAILED test_workflow.py::test_failing_1
FAILED test_workflow.py::test_failing_2

2 failed, 3 passed in 0.39s
```

Your own message and the rewritten expression both survive: `AssertionError: Math is broken`
is what you wrote, and `assert (2 + 2) == 5` underneath is rustest showing its work.

```bash
# Run only the 2 failed tests
rustest test_workflow.py --lf
```

```
... the same two FAILURES blocks ...
=========================== short test summary info ===========================
FAILED test_workflow.py::test_failing_1
FAILED test_workflow.py::test_failing_2

2 failed, 3 deselected in 0.30s
```

The word to watch is **deselected**: the other three were not run, not skipped. The summary
line always accounts for every collected test, so `2 + 3` still adds up to the 5 that were
found.

!!! tip "Cache Location"
    Failed test information is stored in `.rustest_cache/v/cache/lastfailed`, created
    and updated after every run. The path inside `.rustest_cache/` is pytest's own
    cache-value layout, and the document is pytest's `{nodeid: true}` map, so the `cache`
    fixture reads the same file: `cache.get("cache/lastfailed", {})`.

    Entries for tests that did not run this time are kept, which is what makes a `--lf`
    loop converge. Run the 3 failures, fix 1, and the next `--lf` still knows about the
    other 2. A test that passes or skips loses its entry.

### Failed First (--ff)

Run previously failed tests first, then continue with all other tests. This helps you see failures quickly while still running the full suite:

```bash
# Run failed tests first, then all others
rustest test_workflow.py --ff
```

Everything runs, but the previously-failing tests go first. Add `-v` to see the reordering,
since at the default rung the order is not visible:

```
test_workflow.py::test_failing_1 FAILED                                 [ 20%]
test_workflow.py::test_failing_2 FAILED                                 [ 40%]
test_workflow.py::test_passing_1 PASSED                                 [ 60%]
test_workflow.py::test_passing_2 PASSED                                 [ 80%]
test_workflow.py::test_passing_3 PASSED                                 [100%]
... FAILURES blocks ...

2 failed, 3 passed in 0.47s
```

`test_failing_1` and `test_failing_2` are declared third and fifth in the file, but ran
first and second. That is `--ff`. Nothing is deselected here, so the counts match a plain
run; only the order changed.

### Fail Fast (-x)

Stop execution immediately after the first test failure. Useful for quick feedback during development:

```bash
# Stop on first failure
rustest test_workflow.py -x
```

```
... the test_failing_1 FAILURES block ...
=========================== short test summary info ===========================
FAILED test_workflow.py::test_failing_1
stopping after 1 failures (-x)

1 failed, 2 passed in 0.31s
```

Three tests ran instead of five, and `stopping after 1 failures (-x)` says why. The counts
only ever describe what actually ran.

### Combining Workflow Options

Combine `--ff` and `-x` to run failed tests first and stop on first failure:

```bash
# Run failed tests first, stop on first failure
rustest test_workflow.py --ff -x
```

```
... the test_failing_1 FAILURES block ...
=========================== short test summary info ===========================
FAILED test_workflow.py::test_failing_1
stopping after 1 failures (-x)

1 failed in 0.33s
```

One test ran: `--ff` put a known failure first and `-x` stopped there. This is the tightest
loop available for iterating on a fix, and unlike pasting a node id back as a path
argument, it actually narrows the run.

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

There isn't any, and that is deliberate. Rustest prints no spinner and no collection
banner, because collection is fast enough (a warm collect over 5,000 tests is roughly
230 ms) that a progress indicator would flash past. The first thing you see is the result.

If nothing is found, you get pytest's line and pytest's exit code:

```
no tests ran in 0.02s
```

```bash
echo $?    # 5, pytest's EXIT_NOTESTSCOLLECTED
```

!!! note "Piping changes the width, and nothing else"
    Rustest never varies its *content* by whether stdout is a terminal. There are no
    colour codes to strip out of a CI log, no progress frames to filter, and no
    `--no-progress` flag to remember. The one thing that does vary is the line width:
    separator rules and the `-v` percent column are laid out against the terminal's
    column count, and a redirected stdout has none, so they fall back to 80 columns.
    Set `COLUMNS` if you want a redirected run to match a particular width.

### Verbose Mode

Rustest has pytest's verbosity ladder, narrowed to three rungs:

```bash
rustest -q          # summary line only
rustest             # + ERRORS/FAILURES blocks + short test summary info
rustest -v          # + one line per test
rustest --verbose   # same as -v
```

**Quiet (`-q`):**
```
1 failed, 3 passed, 1 skipped in 0.44s
```

**Default:**
```
================================== FAILURES ===================================
_____________________________ test_broken_feature _____________________________
Traceback (most recent call last):
  File "/path/to/test_example.py", line 12, in test_broken_feature
    assert result == 5
AssertionError: assert 4 == 5
=========================== short test summary info ===========================
FAILED test_example.py::test_broken_feature

1 failed, 3 passed, 1 skipped in 0.44s
```

**Verbose (`-v`):**
```
test_example.py::test_basic_assertion PASSED                            [ 20%]
test_example.py::test_string_operations PASSED                          [ 40%]
test_example.py::test_list_operations PASSED                            [ 60%]
test_example.py::test_future_feature SKIPPED (not implemented yet)      [ 80%]
test_example.py::test_broken_feature FAILED                             [100%]
================================== FAILURES ===================================
... as above ...

1 failed, 3 passed, 1 skipped in 0.40s
```

!!! tip "Outcome words"
    `PASSED`, `FAILED`, `SKIPPED (reason)`, `XFAIL`, `XPASS`, `ERROR`. These are pytest's
    wording, so anything that greps your CI logs keeps working. Skip *reasons* appear only
    at `-v`.

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
def calculate():
    return 42


def test_with_print():
    print("Debug information")
    print(f"Value: {calculate()}")
    assert calculate() == 42
```

```bash
# Won't see prints
rustest

# Will see prints
rustest --no-capture
```

### Machine-Readable Output (`--llm`)

`--llm` replaces the human output with **JSONL**: one JSON object per line, a meta
header first and a summary sentinel last. It exists for LLM coding agents and other tools
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

Markdown code blocks are **off by default**. Naming a `.md` file with nothing enabling the
tier is a usage error, exit 4 with `found no collectors for <path>` — pytest's own answer
for the same argument, since pytest collects nothing from a `.md` file either. `--codeblocks`
and `--no-codeblocks` are a tri-state pair: pass one to force it on or off for a single run,
or leave both unset and `[tool.rustest] codeblocks` (or the pytest ini section's own
`codeblocks` key) decides:

```bash
# Nothing enables the tier: a usage error, exit 4
rustest README.md user_guide/*.md

# --codeblocks turns it on for this run, no config needed
rustest README.md user_guide/*.md --codeblocks

# --no-codeblocks turns it off for this run, even if config turned it on
rustest README.md user_guide/*.md --no-codeblocks

# A directory argument collects no markdown either way -- a directory walk never
# picks up .md, which is what pytest does with the same tree
rustest tests/

# Markdown alongside a normal test tree, both named
rustest tests/ README.md --codeblocks
```

Naming a `.md` file with `--codeblocks` off (explicitly, or by leaving everything unset) is
a **usage error**, exit 4 with `found no collectors for <path>`, matching `pytest
README.md` exactly. Reach for `--no-codeblocks` when a command line is shared between the
two runners and their answers have to agree, or when config has turned the tier on and one
run needs it off. See [Markdown Testing](markdown-testing.md) for the config spellings, the
node-id shape a block with inner tests produces, and the full execution model.

## Command-Line Reference

### Full Command Format

```bash
rustest [OPTIONS] [PATHS...]
```

### Options

| Option | Description |
|--------|-------------|
| `[PATHS...]` | Paths to test files or directories. With **no** path argument the `testpaths` ini decides the roots, falling back to the current directory when it is unset. Passing `.` explicitly is an argument, and suppresses `testpaths`, exactly as under pytest |
| `-k PATTERN, --pattern PATTERN` | Substring to filter tests by (case insensitive) |
| `-m MARK_EXPR, --marks MARK_EXPR` | Run tests matching mark expression (e.g., "slow", "not slow") |
| `-n WORKERS, --workers WORKERS` | Number of worker processes (default 4, capped by CPU count) |
| `-s, --no-capture` | Don't capture stdout/stderr during test execution. Output reaches **stderr**, because a worker's stdout is the orchestrator protocol channel |
| `-v, --verbose` | One `PASSED`/`FAILED` line per test, with pytest's wording and percent column |
| `-q, --quiet` | Drop the per-test progress lines. The failure report is still printed, as it is under pytest's own `-q`. `-v` and `-q` cancel out, as they do under pytest |
| `-o, --override-ini OPTION=VALUE` | Override an ini option, e.g. `-o addopts=`. Supported key: `addopts` |
| `--maxfail NUM` | Exit after the first `NUM` failures or errors (`0` means no limit) |
| `--ascii` | Accepted and ignored: the output is already plain ASCII |
| `--color {auto,always,never,yes,no}` | Accepted and ignored: the output is not colored |
| `--codeblocks` | Collect and run python code blocks in markdown files named as arguments. Off by default; see [Markdown Testing](markdown-testing.md) |
| `--no-codeblocks` | Do not collect code blocks, overriding any config setting that turned them on |
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
| `--collect-only`, `--co` | Collect and print node ids, one per line, without running anything. Honours `-k`, `-m` and `-n`; refuses `--llm` and `--cov`, since a run that executes nothing has no result to report or measure |
| `-h, --help` | Show help message and exit |

### pytest flags that are accepted and ignored

A handful of pytest options change how pytest *reports* rather than what it runs, and they
are the kind of thing that sits in a project's `addopts` for years. Rustest accepts them,
does nothing with them, and prints one line on stderr per flag naming what it dropped:

```
NOTE: --tb=short is a pytest reporting option rustest does not implement; it was ignored.
```

The full list: `--tb`, `--durations`, `--durations-min`, `--import-mode`,
`--strict-markers`, `--strict-config`, `--strict`, `-p`, `--showlocals`, `-l`,
`--full-trace`, and `-r` with any report characters attached (`-ra`, `-rfE`). Each is
accepted in both spellings, `--tb=short` and `--tb short`, and the value is dropped with
the flag rather than left behind to be read as a path.

Anything outside that list is still a usage error, exit 4. The division is deliberate: a
flag that changes what *runs* must never be ignored quietly, so `--doctest-modules` is
refused rather than swallowed. Use `-o addopts=` to run a project whose ini carries such a
flag without editing the project.

## Checking the Version

```bash
rustest --version    # -> rustest 1.0.0rc1
```

Prints the installed version on `stdout` and exits 0, running nothing. Like `--llm-schema`,
it is a question rather than a run, so it is answered without collecting anything. That
means it works from a directory with no tests in it, and from a project whose `addopts`
this runner would otherwise refuse.

The string is read from the installed distribution's metadata, which is the same source the
`meta` line of [`--llm`](llm-output.md) reports, so the two cannot disagree.

## Exit Codes

Rustest uses pytest's exit codes:

| Code | Meaning |
|---|---|
| `0` | All tests passed |
| `1` | One or more tests failed |
| `2` | Session interrupted: a collection error, or `pytest.exit()` |
| `3` | Internal error. The worker pool itself failed, and the line on stderr starts `INTERNALERROR:` |
| `4` | Usage error: an unrecognised or removed flag, or a flag that would be inert |
| `5` | No tests were collected |

Exit **4** is the one worth knowing about when upgrading. This CLI refuses flags it cannot
honour rather than accepting them silently, so you get it from:

- `--pytest-compat` or `--v1`, both removed
- any flag rustest does not recognise
- `--llm-full` without `--llm`, and `--cov-report` without `--cov`, each of which would
  otherwise be inert
- `--cov-branch`, or `branch = True` in the coverage configuration, since branch coverage
  is not implemented and reporting line coverage instead would overstate it
- `--llm` or `--cov` alongside `--collect-only`
- `-o`/`--override-ini` naming any key but `addopts`
- a path argument that does not exist, or a `-k`/`-m` expression that does not parse

An unrecognised flag prints pytest's `inifile:` and `rootdir:` lines under the error,
whether you typed it or a config file did, so you know which file to check. A *removed*
flag prints them only when it really did come out of `addopts`.

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
rustest README.md --codeblocks --no-capture

# Test one page, verbosely
rustest user_guide/fixtures.md -v --codeblocks

# Test every page on the site: this is the line CI runs. No --codeblocks needed here
# because this repository's own pyproject.toml sets [tool.rustest] codeblocks = true.
rustest README.md user_guide/*.md
```

The site's pages live in a flat `user_guide/` directory, so one glob reaches all of them.
Each block executes in its own fresh namespace, which is why every block has to import
what it uses.

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

`COLUMNS` overrides the terminal width rustest lays its separator rules and `-v` percent
column out against, which is the one way to make redirected output match a chosen width.
For the `pythonpath` ini, which does the same job as `PYTHONPATH` but travels with the
project, see [Project Structure](project-structure.md).

**`PYTEST_ADDOPTS` is not read.** The `addopts` *ini* is applied and prepended to your
arguments, as pytest does it, but options exported into the environment are ignored.

One variable is set *by* rustest in each worker's environment, for suites that need to know
which runner they are under:

| Variable | Value |
|---|---|
| `RUSTEST_RUNNING` | `1` on every rustest run |

A second variable, `RUSTEST_ENGINE`, existed while rustest shipped two engines and a suite
might need to tell them apart. There is one engine, so it named nothing and was removed
before 1.0.0. Use `RUSTEST_RUNNING` for "am I under rustest?".

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
