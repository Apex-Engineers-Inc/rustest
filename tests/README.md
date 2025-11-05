# Integration Test Suite

This directory contains integration tests that validate rustest's test running capabilities. These tests are unique because **they can be run by both pytest and rustest**.

## Dual-Runner Architecture

### How It Works

Tests in this directory use `from rustest import parametrize, fixture, skip`, but thanks to the `conftest.py` compatibility shim, they work with both runners:

**With pytest:**
```bash
pytest tests/
```
The `conftest.py` intercepts `from rustest import ...` and redirects to pytest's native decorators (`pytest.mark.parametrize`, `pytest.fixture`, etc.)

**With rustest:**
```bash
python -m rustest tests/
```
Uses rustest's actual decorators and runs through the Rust-powered test engine.

## Benefits

1. **Single source of truth** - Same tests validate both implementations
2. **Compatibility validation** - If tests pass in both, rustest matches pytest behavior
3. **Regression testing** - Changes that break pytest compatibility will fail pytest run
4. **Easy to understand** - One test suite instead of separate pytest and rustest tests

## Test Files

### Passing Tests (Run in CI)
- `test_basic.py` - Simple test functions
- `test_complex_parametrize.py` - Advanced parametrization patterns
- `test_fixtures.py` - Fixture dependency injection
- `test_nested_fixtures.py` - Fixtures that depend on other fixtures
- `test_parametrized.py` - Parametrized tests with custom IDs
- `test_skip.py` - Skipped tests

### Error Tests (Excluded from CI)
- `test_errors.py` - **Intentional failures** to test error reporting
- `test_output.py` - Contains `test_failure_with_output` which intentionally fails

## Running Tests

### Run with pytest
```bash
# All tests (includes intentional failures)
pytest tests/ -v

# Only passing tests
pytest tests/test_basic.py \
       tests/test_complex_parametrize.py \
       tests/test_fixtures.py \
       tests/test_nested_fixtures.py \
       tests/test_parametrized.py \
       tests/test_skip.py -v
```

### Run with rustest
```bash
# All tests (includes intentional failures)
python -m rustest tests/

# Only passing tests
python -m rustest tests/test_basic.py \
                  tests/test_complex_parametrize.py \
                  tests/test_fixtures.py \
                  tests/test_nested_fixtures.py \
                  tests/test_parametrized.py \
                  tests/test_skip.py
```

## Expected Results

Both runners should produce identical results on the same test files:

```
pytest:  34 passed, 5 skipped
rustest: 34 passed, 5 skipped
```

Any discrepancy indicates a compatibility issue that needs fixing.

## Adding New Tests

When adding tests to this suite:

1. Use rustest imports: `from rustest import parametrize, fixture, skip`
2. Test with both runners to ensure compatibility
3. If testing error handling, add to `test_errors.py` (excluded from CI)
4. If testing normal functionality, create a new test file or add to existing ones
