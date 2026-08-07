# Marks & skipping

A mark is a label attached to a test. Rustest uses marks for the behaviours pytest uses them
for (skipping, expected failures, async execution) and lets you invent your own to categorise
tests for `-m` filtering.

## Skipping tests

### The skip() function

`skip()` raises immediately, so anything after it in the test body never runs. Use it when the
decision depends on something only known at run time.

```python
from rustest import skip
import sys

def test_future_feature() -> None:
    skip("Feature not implemented yet")
    assert False  # This won't run

def test_platform_specific() -> None:
    if sys.platform == "win32":
        skip("Not supported on Windows")
    # Test code here
```

`skip()` raises a `BaseException` subclass, so an `except Exception:` in your own test helper
cannot swallow it and turn the skip into a pass.

At module level, `skip()` needs `allow_module_level=True`. Without it, a module-scope call is a
collection error carrying pytest's "pass `allow_module_level=True`" message. With it, the whole
module is skipped and nothing in it is collected.

### @mark.skip

The decorator form takes the decision at import time. Both the bare and the called spelling
work.

```python
from rustest import mark

@mark.skip(reason="Waiting for API update")
def test_deprecated_api() -> None:
    assert False

@mark.skip
def test_also_skipped() -> None:
    assert False
```

### Conditional skipping

For a condition you can evaluate at import time, `@mark.skipif` below is the direct tool. An
inline conditional decorator also works, since a decorator may be any expression:

```python
import os
from rustest import mark

should_skip = not os.getenv("RUN_EXPENSIVE_TESTS")

@mark.skip(reason="Expensive test - set RUN_EXPENSIVE_TESTS=1") if should_skip else lambda f: f
def test_expensive_operation() -> None:
    # This runs only if RUN_EXPENSIVE_TESTS is set
    pass
```

`skip()` inside the body is the readable version of the same thing, and the one to prefer when
the condition involves anything expensive to compute at import:

```python
import os
from rustest import skip

def test_expensive_operation() -> None:
    if not os.getenv("RUN_EXPENSIVE_TESTS"):
        skip("Expensive test - set RUN_EXPENSIVE_TESTS=1")
    # This runs only if RUN_EXPENSIVE_TESTS is set
    pass
```

## Standard Pytest marks

### @mark.skipif

```python
import sys
from rustest import mark

@mark.skipif(sys.platform == "win32", reason="Not supported on Windows")
def test_unix_only() -> None:
    """This test only runs on Unix-like systems."""
    pass

@mark.skipif(sys.version_info < (3, 10), reason="Requires Python 3.10+")
def test_modern_python() -> None:
    """This test only runs on Python 3.10 or newer."""
    pass
```

The condition may also be a **string**, which is evaluated against the decorated function's
module globals: `@mark.skipif("sys.platform == 'win32'", reason=...)`. Several `skipif` marks
on one test are a disjunction, so any one of them holding skips the test. A bare `@mark.skipif`
with no condition at all skips unconditionally, which is what pytest does with it.

Every `skipif` is considered before any `skip`, so a test carrying both is skipped for the
condition's reason when the condition holds.

### @mark.xfail

```python
import sys
from rustest import mark

@mark.xfail(reason="Known bug in backend #123")
def test_known_bug() -> None:
    """This test is expected to fail until the bug is fixed."""
    assert False  # Expected to fail

@mark.xfail(sys.platform == "darwin", reason="Not implemented on macOS")
def test_platform_specific() -> None:
    """This test is expected to fail on macOS."""
    pass

@mark.xfail(reason="Flaky test", strict=False)
def test_flaky_behavior() -> None:
    """Test may pass or fail; either is acceptable."""
    pass

@mark.xfail(reason="Must fail", strict=True)
def test_strict_xfail() -> None:
    """If this test passes unexpectedly, the suite will fail."""
    assert False
```

**Parameters:**

- `condition`: Optional boolean or string condition. When it evaluates false, the mark is
  ignored and the test is reported normally.
- `reason`: Explanation for why the test is expected to fail.
- `raises`: Expected exception type, or a tuple of them. An exception that does not match
  reports as a plain failure rather than an xfail.
- `run`: When `False`, the body is not executed. The test reports as **xfailed** with a
  `[NOTRUN]` prefix on the reason, not as skipped.
- `strict`: When `True`, a test that unexpectedly passes fails the suite, with pytest's
  `[XPASS(strict)]` prefix.

`strict` defaults to `False`. The `xfail_strict` ini option is not read, so a project that sets
it still gets non-strict behaviour here unless each mark says otherwise.

A `skip` mark beats an `xfail` mark on the same test. An `xfail` also applies during setup and
teardown, so a test with a broken fixture and an `xfail` mark reports XFAIL rather than ERROR.

### @mark.asyncio

```python
from rustest import mark

expected_value = 42

async def some_async_function():
    return expected_value

async def another_async_operation():
    return None

@mark.asyncio
async def test_async_operation() -> None:
    """Test async function execution."""
    result = await some_async_function()
    assert result == expected_value

@mark.asyncio(loop_scope="module")
async def test_with_module_loop() -> None:
    """Test with shared event loop across the module."""
    await another_async_operation()
```

**Parameters:**

- `loop_scope`: The scope of the event loop, one of `"function"`, `"class"`, `"module"`,
  `"package"` or `"session"`. Omitted, it falls back to the `asyncio_default_test_loop_scope`
  ini option, whose own default is `"function"`.
- `scope`: pytest-asyncio's deprecated spelling of `loop_scope`, accepted so a suite written
  against pytest-asyncio before 1.0 keeps working. Passing both is an error.
- `timeout`: Seconds after which the test is cancelled. Must be positive.

Rustest's `asyncio_mode` defaults to `auto`, so an unmarked `async def test_*` runs without
this mark. You need it to pin a `loop_scope` or a `timeout`, or when the project sets
`asyncio_mode = "strict"`.

Applied to a class, the mark reaches every coroutine method on it:

```python
import asyncio
from rustest import mark

async def async_operation_one():
    await asyncio.sleep(0.001)
    return "result1"

async def async_operation_two():
    await asyncio.sleep(0.001)
    return "result2"

@mark.asyncio(loop_scope="class")
class TestAsyncOperations:
    """All async methods in this class share an event loop."""

    async def test_async_one(self) -> None:
        result = await async_operation_one()
        assert result is not None

    async def test_async_two(self) -> None:
        result = await async_operation_two()
        assert result is not None
```

A test's loop scope does not widen to match the async fixtures it requests. See the
[Async Event Loops](async-event-loops.md) page for what does decide it, and the
[Async Testing Guide](async-testing.md) for writing async tests generally.

### @mark.usefixtures

Fixtures named here are set up for the test without appearing in its signature.

```python
from rustest import fixture, mark

_ROWS = []

def create_test_db():
    class DB:
        def cleanup(self):
            _ROWS.clear()
    _ROWS.append("ready")
    return DB()

def query_database():
    return _ROWS

@fixture
def setup_cache():
    """A second side-effect-only fixture, for the class example below."""
    yield

@fixture
def setup_database():
    """Initialize test database."""
    db = create_test_db()
    yield
    db.cleanup()

@mark.usefixtures("setup_database")
def test_without_explicit_fixture() -> None:
    """Uses setup_database fixture without requesting it."""
    # Database is already set up
    assert query_database() is not None

@mark.usefixtures("setup_database", "setup_cache")
class TestDatabaseOperations:
    """All tests in this class use both fixtures."""

    def test_query(self) -> None:
        pass

    def test_insert(self) -> None:
        pass
```

This is what you want when a fixture has side effects but no return value, when a whole class
needs the same setup, or when the fixture's name would collide with a parameter name.

## Custom marks

Any attribute on `mark` becomes a mark. Nothing needs registering first: rustest does not read
the `markers` ini option and emits no unknown-mark warning, and `--strict-markers` is accepted
and ignored with a note on stderr.

```python
from rustest import mark

@mark.unit
def test_calculation() -> None:
    assert 2 + 2 == 4

@mark.integration
def test_database_connection() -> None:
    # Integration test
    pass

@mark.slow
def test_long_running_process() -> None:
    # Slow test
    pass
```

### Multiple marks

```python
from rustest import mark

@mark.integration
@mark.slow
@mark.critical
def test_full_workflow() -> None:
    # This test has three marks
    pass
```

## Marks with arguments

A mark called with arguments carries them, and `-m` can filter on the keyword ones.

```python
from rustest import mark

@mark.timeout(seconds=30)
def test_with_timeout() -> None:
    # Should complete within 30 seconds
    pass

@mark.priority(level=1)
def test_critical_feature() -> None:
    pass

@mark.requires(database=True, cache=True)
def test_with_dependencies() -> None:
    pass
```

Rustest records these arguments and matches on them. It does not act on them: `@mark.timeout`
above is a label, not an enforced limit. For a real per-test timeout on an async test, use
`@mark.asyncio(timeout=...)`.

## Common mark patterns

### Speed categories

```python
from rustest import mark

@mark.fast
def test_quick_operation() -> None:
    assert 1 + 1 == 2

@mark.slow
def test_expensive_computation() -> None:
    result = sum(range(1000000))
    assert result > 0
```

### Test levels

```python
from rustest import mark

@mark.unit
def test_function_unit() -> None:
    """Tests a single function in isolation."""
    pass

@mark.integration
def test_components_together() -> None:
    """Tests multiple components working together."""
    pass

@mark.e2e
def test_end_to_end_workflow() -> None:
    """Tests the entire system."""
    pass
```

### Environment-specific tests

```python
from rustest import mark

@mark.requires_postgres
def test_postgres_specific_feature() -> None:
    pass

@mark.requires_redis
def test_cache_operations() -> None:
    pass

@mark.production_only
def test_production_behavior() -> None:
    pass
```

### Priority levels

```python
from rustest import mark

@mark.smoke
def test_basic_functionality() -> None:
    """Smoke tests run first in CI."""
    pass

@mark.critical
def test_core_feature() -> None:
    """Critical tests that must pass."""
    pass

@mark.regression
def test_bug_fix() -> None:
    """Regression test for a specific bug."""
    pass
```

## Marks on test classes

A mark on a class applies to every test in it.

```python
from rustest import mark

@mark.integration
class TestDatabaseOperations:
    """All tests in this class are marked as integration."""

    def test_insert(self) -> None:
        pass

    def test_update(self) -> None:
        pass

    def test_delete(self) -> None:
        pass
```

Methods can add their own on top:

```python
from rustest import mark

@mark.integration
class TestAPI:
    def test_get_user(self) -> None:
        pass

    @mark.slow
    def test_list_all_users(self) -> None:
        # This test has both @mark.integration (from class)
        # and @mark.slow (from method)
        pass
```

## Marks with parametrization

```python
from rustest import parametrize, mark

@mark.unit
@parametrize("value,expected", [
    (2, 4),
    (3, 9),
    (4, 16),
])
def test_square(value: int, expected: int) -> None:
    assert value ** 2 == expected
```

`param(..., marks=...)` marks a single case rather than the whole function, so one
parametrized case can be xfailed while its siblings run normally.

## Filtering tests by marks

`-m` takes a boolean expression over mark names. Rustest ports pytest's expression grammar,
including its error messages and their 1-based column numbers, so a bad expression exits 4 with
the wording you would grep for under pytest.

Deselection happens after collection and before anything runs. A file that fails to import is
still a collection error and still exits 2, however aggressively `-m` deselects.

### Basic mark filtering

<!--rustest.mark.skip-->
```bash
# Run only slow tests
rustest -m "slow"

# Run only integration tests
rustest -m "integration"

# Run only unit tests
rustest -m "unit"
```

Mark names match exactly, and case matters: `-m slo` selects nothing when the mark is `slow`.

### Negation

<!--rustest.mark.skip-->
```bash
# Run all tests except slow ones
rustest -m "not slow"

# Run all tests except integration tests
rustest -m "not integration"
```

### Boolean expressions

`and` binds tighter than `or`, and `not` tighter than both.

<!--rustest.mark.skip-->
```bash
# Run tests marked as both slow AND integration
rustest -m "slow and integration"

# Run tests marked as either slow OR integration
rustest -m "slow or integration"

# Run slow tests that are not integration tests
rustest -m "slow and not integration"
```

### Complex expressions

<!--rustest.mark.skip-->
```bash
# Run tests that are either (slow or fast) but not integration
rustest -m "(slow or fast) and not integration"

# Run critical tests or smoke tests, but not slow ones
rustest -m "(critical or smoke) and not slow"
```

### Matching a mark's arguments

A name followed by parentheses constrains the mark's keyword arguments. At least one mark of
that name has to satisfy every constraint given. Values may be quoted strings, integers,
`True`, `False` or `None`, and a keyword the mark does not carry never matches.

<!--rustest.mark.skip-->
```bash
# Only tests marked @mark.net(scope="wide")
rustest -m "net(scope='wide')"

# Two constraints, both of which one mark must satisfy
rustest -m "net(scope='wide', retries=3)"
```

### Combining with pattern matching

`-k` matches a case-insensitive substring against the test's node names: each directory in its
path, the file name (with the `.py`), each enclosing class, the function name plus any
`[param_id]`, and the names of its marks. `-m` and `-k` compose, `-k` first.

<!--rustest.mark.skip-->
```bash
# Run slow database tests
rustest -m "slow" -k "database"

# Run integration tests matching "api" in the name
rustest -m "integration" -k "api"
```

Because mark names are among the node names, `-k slow` selects marked tests too. `-k` is the
loose one and `-m` the exact one.

Two emptiness rules differ, which is pytest's asymmetry and worth knowing before it surprises
you: `-k "   "` filters nothing and selects everything, while `-m "   "` compiles to the empty
expression and deselects everything. Only `-m ""` skips mark filtering.

### Common filtering patterns

<!--rustest.mark.skip-->
```bash
# Fast feedback loop - run only fast unit tests
rustest -m "unit and not slow"

# Pre-commit checks - run non-slow tests
rustest -m "not slow"

# Full test suite except integration tests (for local dev)
rustest -m "not integration"

# CI smoke tests - run critical and smoke tests
rustest -m "critical or smoke"

# Nightly builds - run all slow and integration tests
rustest -m "slow or integration"
```

## Keeping a mark registry

Since rustest requires no registration, a module docstring is a decent place to record what
each mark in the project is supposed to mean.

```python
# marks.py
"""
Test mark definitions for this project.

Available marks:
- @mark.unit: Unit tests (fast, isolated)
- @mark.integration: Integration tests (slower, use external services)
- @mark.slow: Tests that take >1 second
- @mark.critical: Tests that must pass before deployment
- @mark.smoke: Quick smoke tests for basic functionality
- @mark.requires_db: Tests that require database connection
"""
```

Then reference it in your tests:

```python
from rustest import mark

@mark.unit
def test_calculation():
    """Unit test - see marks.py for mark definitions."""
    assert 2 + 2 == 4
```

## Best practices

### Use consistent mark names

Since `-m` matches exactly, a typo is a mark nobody will ever select, and rustest will not warn
you about it. Pick one spelling convention.

Good, consistent naming:

```python
from rustest import mark

@mark.unit
def test_calculation():
    assert 2 + 2 == 4

@mark.integration
def test_api_call():
    assert True

@mark.e2e
def test_full_workflow():
    assert True
```

Less ideal:

```text
Avoid these inconsistent styles:
@mark.unit_test     # Inconsistent - uses underscore
@mark.Integration   # Inconsistent - uses Pascal case
@mark.end2end       # Inconsistent - abbreviated differently
```

### Document custom marks

A custom mark carries no behaviour of its own, so its meaning has to live in prose or in
whatever runs your CI.

```python
from rustest import mark

@mark.flaky(max_retries=3)
def test_external_api():
    """Test may fail intermittently due to external API.

    Mark 'flaky' indicates this test should be retried up to 3 times
    before being marked as failed.
    """
    pass
```

Rustest does not retry anything on its own. The mark above records an intent for a wrapper
script or a CI job to act on.

### Don't overuse marks

```python
from rustest import mark

# Good - meaningful categorization
@mark.integration
@mark.slow
def test_database_migration():
    pass

# Overkill - too many marks
@mark.integration
@mark.slow
@mark.database
@mark.migration
@mark.critical
@mark.version_2
def test_database_migration():
    pass
```

### Combine with test organization

Directory layout and marks answer different questions. The directory says where a test lives;
the mark says what a CI job should do with it.

```
tests/
├── unit/              # Unit tests
│   ├── test_math.py
│   └── test_strings.py
├── integration/       # Integration tests (also marked @mark.integration)
│   ├── test_api.py
│   └── test_database.py
└── e2e/              # E2E tests (also marked @mark.e2e)
    └── test_workflows.py
```

## skip() versus @mark.skip

`skip()` decides at run time and `@mark.skip` at import time. That is the whole distinction.

```python
from rustest import skip, mark
import os

# Using skip() function - for runtime conditional skipping
def test_a() -> None:
    if not os.getenv("FEATURE_READY"):
        skip("Not ready")
    # Test code here - only runs if FEATURE_READY is set

# Using @mark.skip decorator - for decoration-time skipping
@mark.skip(reason="Not ready")
def test_b() -> None:
    pass
```

Use `skip()` when the answer depends on the machine the tests are running on, and `@mark.skip`
when you already know at import that the test should not run.

## Next steps

- [Test Classes](test-classes.md) - Use marks with test classes
- [CLI Usage](cli.md) - Filter tests using the command line
- [Writing Tests](writing-tests.md) - Organize your tests effectively
