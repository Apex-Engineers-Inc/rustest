# Pytest Compatibility Tests

This directory contains configuration for running rustest against pytest's own test suite to validate compatibility.

## Overview

Running rustest against pytest's actual tests helps ensure that rustest correctly implements pytest-compatible behavior. Since rustest currently supports a subset of pytest's functionality, we carefully select which tests to run.

## Current Support Level

Rustest currently supports:
- Basic test discovery (`test_*.py` and `*_test.py`)
- Simple test functions
- `@pytest.fixture` decorator
- `@pytest.mark.parametrize` decorator
- `@pytest.mark.skip` decorator
- Test result reporting

## Running Pytest Compatibility Tests

To run rustest against a subset of pytest's tests:

```bash
# Run with rustest (your test runner)
uv run rustest tests/pytest_compat/selected/

# Compare with pytest's own results
uv run pytest tests/pytest_compat/selected/
```

## Test Selection Strategy

Currently, we focus on tests that validate core functionality that rustest supports:

1. **Basic test collection** - Tests that verify test discovery works correctly
2. **Simple parametrization** - Tests for `@pytest.mark.parametrize`
3. **Fixture basics** - Tests for basic `@pytest.fixture` usage
4. **Skip markers** - Tests for `@pytest.mark.skip`

As rustest grows in compatibility, we'll expand this test suite.

## Adding More Tests

To add more pytest tests to the compatibility suite:

1. Identify a pytest test file that tests functionality rustest supports
2. Copy or symlink it to `tests/pytest_compat/selected/`
3. Run both rustest and pytest against it to verify behavior matches
4. Document any known differences or limitations

## Known Limitations

Rustest does NOT yet support:
- Fixtures with complex dependencies
- `setup`/`teardown` methods
- `conftest.py` files
- Most pytest plugins
- Advanced parametrization features
- Markers beyond `skip` and `parametrize`
- Test collection hooks
- Custom assertions

These limitations mean many of pytest's own tests won't work with rustest yet - and that's expected! This compatibility test suite helps track progress.
