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

!!! note "Mark Filtering"
    Mark-based test filtering (e.g., `-m "slow"`) is planned but not yet implemented in rustest. For now, use the `-k` pattern matching to filter tests by name.

Current workaround using test name patterns:

```bash
# Include "slow" in test names for slow tests
def test_slow_operation() -> None:
    pass

# Then filter with -k
rustest -k "slow"
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

```python
# Good - consistent naming
@mark.unit
@mark.integration
@mark.e2e

# Less ideal - inconsistent
@mark.unit_test
@mark.Integration
@mark.end2end
```

### Document Custom Marks

If you create custom marks with special meaning, document them:

```python
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
