# 5-Minute Migration Guide

Migrating from pytest to rustest is straightforward. This guide shows you how to get up and running quickly.

## Step 0: Try Without Changing Anything

The fastest way to try rustest is with **zero code changes**:

```bash
pip install rustest
rustest --pytest-compat tests/
```

The `--pytest-compat` flag intercepts `import pytest` statements and provides rustest implementations. Your existing pytest tests run with rustest's performance.

**See the speedup immediately.** Then decide if you want to migrate.

!!! success "Risk-Free Trial"
    This mode lets you evaluate rustest without touching your code. If it works great! If not, you haven't lost any time.

---

## Step 1: Install Rustest

Choose your preferred installation method:

=== "pip"
    ```bash
    pip install rustest
    ```

=== "uv (recommended)"
    ```bash
    uv add rustest
    ```

=== "Try without installing"
    ```bash
    # Using uvx (instant, no install)
    uvx rustest --pytest-compat tests/

    # Or pipx
    pipx run rustest --pytest-compat tests/
    ```

---

## Step 2: Update Your Imports

Change your test imports from `pytest` to `rustest`:

### Before (pytest):

```python
import pytest
from pytest import fixture, mark, raises, approx

@fixture
def database():
    db = Database()
    yield db
    db.close()

@pytest.mark.parametrize("input,expected", [(1, 2), (3, 4)])
def test_something(database, input, expected):
    with pytest.raises(ValueError):
        result = database.process(input)
    assert result == pytest.approx(expected)
```

### After (rustest):

```python
from rustest import fixture, mark, parametrize, raises, approx

@fixture
def database():
    db = Database()
    yield db
    db.close()

@parametrize("input,expected", [(1, 2), (3, 4)])
def test_something(database, input, expected):
    with raises(ValueError):
        result = database.process(input)
    assert result == approx(expected)
```

### What Changed:

- ✅ `import pytest` → `from rustest import ...`
- ✅ `@pytest.mark.parametrize` → `@parametrize` (imported separately)
- ✅ `pytest.raises` → `raises`
- ✅ `pytest.approx` → `approx`

**That's it!** The API is nearly identical.

---

## Step 3: Run Your Tests

```bash
rustest
```

Or run specific paths:

```bash
rustest tests/
rustest tests/test_users.py
```

---

## Common Migration Patterns

### Fixtures

No changes needed! Fixtures work the same:

```python
from rustest import fixture

@fixture
def api_client():
    client = APIClient("https://api.example.com")
    yield client
    client.disconnect()

@fixture(scope="module")
def database():
    db = Database()
    db.connect()
    yield db
    db.disconnect()
```

### Parametrization

Import `parametrize` separately:

```python
# pytest
import pytest

@pytest.mark.parametrize("input,expected", [...])
def test_something(input, expected):
    ...
```

```python
# rustest
from rustest import parametrize

@parametrize("input,expected", [...])
def test_something(input, expected):
    ...
```

### Marks

```python
# pytest
import pytest

@pytest.mark.slow
@pytest.mark.integration
def test_api():
    ...
```

```python
# rustest
from rustest import mark

@mark.slow
@mark.integration
def test_api():
    ...
```

### Async Tests

**pytest (with plugin):**

```python
import pytest

@pytest.mark.asyncio
async def test_async_operation():
    result = await fetch_data()
    assert result.success
```

**rustest (built-in):**

```python
from rustest import mark

@mark.asyncio
async def test_async_operation():
    result = await fetch_data()
    assert result.success
```

No plugin installation needed! 🎉

### Mocking

**pytest (with pytest-mock):**

```python
def test_with_mock(mocker):
    mocker.patch("module.function", return_value=42)
    assert module.function() == 42
```

**rustest (built-in):**

```python
def test_with_mock(mocker):
    mocker.patch("module.function", return_value=42)
    assert module.function() == 42
```

Identical! The `mocker` fixture is built-in.

---

## Handling conftest.py

Your `conftest.py` files work as-is! Just update imports:

### Before:

```python
# conftest.py
import pytest

@pytest.fixture
def database():
    db = Database()
    db.connect()
    yield db
    db.disconnect()
```

### After:

```python
# conftest.py
from rustest import fixture

@fixture
def database():
    db = Database()
    db.connect()
    yield db
    db.disconnect()
```

---

## Plugin Migration

Many pytest plugins have built-in rustest equivalents:

| pytest plugin | rustest equivalent |
|--------------|-------------------|
| pytest-asyncio | **Built-in** with `@mark.asyncio` |
| pytest-mock | **Built-in** with `mocker` fixture |
| pytest-codeblocks | **Built-in** markdown testing |
| coverage.py | Works directly (no special plugin) |

For other plugins, see the [Plugin Migration Guide](plugins.md).

---

## Gradual Migration Strategy

You don't have to migrate everything at once:

### Option 1: Dual-Run During Transition

Keep pytest installed and gradually migrate files:

```bash
# Run pytest on old tests
pytest tests/old/

# Run rustest on migrated tests
rustest tests/new/
```

### Option 2: Use Compatibility Mode for Old Tests

```bash
# Migrated tests use native rustest
rustest tests/migrated/

# Old tests use compatibility mode
rustest --pytest-compat tests/legacy/
```

### Option 3: Feature Branch Migration

1. Create a branch: `git checkout -b migrate-to-rustest`
2. Update imports in batches (one module at a time)
3. Run tests after each batch: `rustest`
4. Merge when all tests pass

---

## Troubleshooting

### Import Errors

**Error:** `ImportError: cannot import name 'fixture' from 'pytest'`

**Solution:** You mixed pytest and rustest imports:

```python
# ❌ WRONG
from pytest import fixture
from rustest import mark

# ✅ CORRECT
from rustest import fixture, mark
```

### Plugin Not Found

**Error:** `Plugin 'my_plugin' not found`

**Solution:** Rustest doesn't support pytest plugins. Check if there's a built-in alternative:

- pytest-asyncio → Use `@mark.asyncio` (built-in)
- pytest-mock → Use `mocker` fixture (built-in)
- pytest-cov → Use `coverage run -m rustest` ([guide](coverage.md))

See [Plugin Migration Guide](plugins.md) for more.

### Tests Are Slower

If rustest is slower than expected:

1. **Check you're not using `--pytest-compat`** — Compatibility mode has overhead
2. **Ensure you changed imports** — Native rustest imports are faster
3. **Profile your tests** — The slowness might be in your test code, not rustest

### Missing Features

If a feature doesn't work:

1. Check the [Feature Comparison](comparison.md) to see if it's supported
2. Check [Known Limitations](limitations.md) for planned features
3. [Report an issue](https://github.com/Apex-Engineers-Inc/rustest/issues) if it's important to you

---

## Verifying Migration Success

After migration, verify everything works:

```bash
# Run all tests
rustest -v

# Run with coverage
coverage run -m rustest tests/
coverage report

# Run specific marks
rustest -m "not slow"

# Run last failed tests
rustest --lf
```

If all tests pass, you're done! 🎉

---

## Migration Checklist

Use this checklist to track your migration:

- [ ] Install rustest (`pip install rustest` or `uv add rustest`)
- [ ] Try compatibility mode (`rustest --pytest-compat tests/`)
- [ ] Update imports in test files (`from rustest import ...`)
- [ ] Update imports in `conftest.py`
- [ ] Replace pytest plugin features with rustest built-ins
- [ ] Run tests and verify they pass (`rustest -v`)
- [ ] Update CI/CD configuration ([see examples](#cicd-integration))
- [ ] Update documentation/README with new test commands
- [ ] (Optional) Remove pytest-asyncio, pytest-mock from dependencies
- [ ] Enjoy dramatically faster tests! 🚀

---

## CI/CD Integration

Update your CI configuration to use rustest:

### GitHub Actions

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -e .
          pip install rustest coverage
      - name: Run tests
        run: rustest -v
      - name: Coverage
        run: |
          coverage run -m rustest tests/
          coverage report
```

### GitLab CI

```yaml
# .gitlab-ci.yml
test:
  image: python:3.11
  script:
    - pip install -e .
    - pip install rustest coverage
    - rustest -v
    - coverage run -m rustest tests/
    - coverage report
```

---

## Getting Help

Need help migrating?

- 📖 [Feature Comparison](comparison.md) — See what's supported
- 🔌 [Plugin Migration Guide](plugins.md) — Replace pytest plugins
- 📊 [Coverage Integration](coverage.md) — Set up coverage
- ⚠️ [Known Limitations](limitations.md) — What's not supported (yet)
- 🐛 [Report Issues](https://github.com/Apex-Engineers-Inc/rustest/issues) — Found a bug?

---

## What's Next?

Now that you've migrated:

- [Explore Built-in Features](../guide/fixtures.md) — Fixtures, parametrization, marks
- [Optimize for Performance](../advanced/performance.md) — Get the most out of rustest
- [Set Up Coverage](coverage.md) — Integrate coverage.py seamlessly
- [Contribute](../advanced/development.md) — Help make rustest better!

**Welcome to faster testing!** 🎉
