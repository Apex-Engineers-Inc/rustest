# Marks & Skipping

Marks allow you to categorize and organize your tests. You can use marks to skip tests, mark slow tests, or create custom categories.

## Skipping Tests

### Using @skip

Skip tests that aren't ready or are temporarily broken:

```python
from rustest import skip

@skip("Feature not implemented yet")
def test_future_feature() -> None:
    assert False  # This won't run

@skip()
def test_temporarily_disabled() -> None:
    assert False
```

### Using @mark.skip

Alternative syntax using marks:

```python
from rustest import mark

@mark.skip(reason="Waiting for API update")
def test_deprecated_api() -> None:
    assert False

@mark.skip
def test_also_skipped() -> None:
    assert False
```

### Conditional Skipping

Skip tests based on runtime conditions:

```python
import os
from rustest import skip

should_skip = not os.getenv("RUN_EXPENSIVE_TESTS")

@skip("Expensive test - set RUN_EXPENSIVE_TESTS=1" if should_skip else None)
def test_expensive_operation() -> None:
    # This runs only if RUN_EXPENSIVE_TESTS is set
    pass
```

## Standard Pytest Marks

Rustest supports standard pytest marks for advanced test control:

### @mark.skipif - Conditional Skipping

Skip tests based on runtime conditions:

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

### @mark.xfail - Expected Failures

Mark tests that are expected to fail:

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
- `condition`: Optional boolean condition - if False, mark is ignored
- `reason`: Explanation for why the test is expected to fail
- `raises`: Expected exception type(s)
- `run`: Whether to run the test (False means skip it)
- `strict`: If True, passing test will fail the suite

### @mark.asyncio - Async Test Support

Mark async test functions to be executed with asyncio:

```python
from rustest import mark

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
- `loop_scope`: The scope of the event loop. One of:
  - `"function"`: New loop for each test function (default)
  - `"class"`: Shared loop across all test methods in a class
  - `"module"`: Shared loop across all tests in a module
  - `"session"`: Shared loop across all tests in the session

**Usage with classes:**

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

For more details, see the [Async Testing Guide](async-testing.md).

### @mark.usefixtures - Implicit Fixture Usage

Use fixtures without explicitly requesting them as parameters:

```python
from rustest import fixture, mark

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

This is useful when:
- A fixture has side effects but no return value
- You want to apply fixtures to an entire test class
- The fixture name would conflict with a parameter name

## Custom Marks

Create custom marks to categorize tests:

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

### Multiple Marks

Apply multiple marks to a single test:

```python
from rustest import mark

@mark.integration
@mark.slow
@mark.critical
def test_full_workflow() -> None:
    # This test has three marks
    pass
```

## Marks with Arguments

Marks can accept arguments and keyword arguments:

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

## Common Mark Patterns

### Speed Categories

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

### Test Levels

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

### Environment-Specific Tests

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

### Priority Levels

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

## Marks on Test Classes

Apply marks to all tests in a class:

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

You can also add marks to individual methods:

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

## Marks with Parametrization

Combine marks with parametrized tests:

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

## Filtering Tests by Marks

Use the `-m` flag to run only tests matching a mark expression:

### Basic Mark Filtering

<!--pytest.mark.skip-->
```bash
# Run only slow tests
rustest -m "slow"

# Run only integration tests
rustest -m "integration"

# Run only unit tests
rustest -m "unit"
```

### Negation

<!--pytest.mark.skip-->
```bash
# Run all tests except slow ones
rustest -m "not slow"

# Run all tests except integration tests
rustest -m "not integration"
```

### Boolean Expressions

Combine multiple mark filters with `and` and `or`:

<!--pytest.mark.skip-->
```bash
# Run tests marked as both slow AND integration
rustest -m "slow and integration"

# Run tests marked as either slow OR integration
rustest -m "slow or integration"

# Run slow tests that are not integration tests
rustest -m "slow and not integration"
```

### Complex Expressions

Use parentheses for complex boolean logic:

<!--pytest.mark.skip-->
```bash
# Run tests that are either (slow or fast) but not integration
rustest -m "(slow or fast) and not integration"

# Run critical tests or smoke tests, but not slow ones
rustest -m "(critical or smoke) and not slow"
```

### Combining with Pattern Matching

You can combine mark filtering with test name pattern matching:

<!--pytest.mark.skip-->
```bash
# Run slow database tests
rustest -m "slow" -k "database"

# Run integration tests matching "api" in the name
rustest -m "integration" -k "api"
```

### Common Filtering Patterns

<!--pytest.mark.skip-->
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

## Creating a Mark Registry

Document your marks in a central location:

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

## Best Practices

### Use Consistent Mark Names

Good - consistent naming:
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

Less ideal - inconsistent naming:
```text
❌ Avoid these inconsistent styles:
@mark.unit_test     # Inconsistent - uses underscore
@mark.Integration   # Inconsistent - uses Pascal case
@mark.end2end       # Inconsistent - abbreviated differently
```

### Document Custom Marks

If you create custom marks with special meaning, document them:

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

### Don't Overuse Marks

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

### Combine with Test Organization

Use both marks and file organization:

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

## Skip vs Mark.skip

Both approaches work identically:

```python
from rustest import skip, mark

# Using @skip
@skip("Not ready")
def test_a():
    pass

# Using @mark.skip
@mark.skip(reason="Not ready")
def test_b():
    pass
```

Choose whichever style you prefer. The `@skip` decorator is more concise, while `@mark.skip` is consistent with other marks.

## Next Steps

- [Test Classes](test-classes.md) - Use marks with test classes
- [CLI Usage](cli.md) - Filter tests using the command line
- [Writing Tests](writing-tests.md) - Organize your tests effectively
