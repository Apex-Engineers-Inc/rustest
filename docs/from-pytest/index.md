# Coming from pytest

## You're Not Alone

If you've ever waited for a slow test suite to finish, wondering why Python testing can't be as fast as vitest or bun test—**you're in good company.**

I love pytest. The `@fixture` decorator is brilliant. The API is clean. Comprehensive tests make better software.

But here's the thing: **Python test suites are frustratingly slow** compared to modern JavaScript/TypeScript runners.

## What Fast Tests Actually Mean

If you write JS/TS, you already know:

- **Vitest**: Instant reruns, instant feedback
- **Bun test**: Thousands of tests in milliseconds
- **Developer experience**: You run tests constantly, catch bugs immediately, never lose flow

Fast tests aren't just nice-to-have. They change your entire development workflow:

✅ Run tests on every save
✅ Get instant feedback, not coffee breaks
✅ Make TDD actually enjoyable
✅ Stay in flow state

## That's Why Rustest Exists

Rustest brings that JavaScript testing experience to Python—**without sacrificing pytest's elegant API.**

Same decorators. Same fixtures. **8.5× faster on average.**

**Pytest is great. We're just making it faster.**

---

## The Quick Pitch

### Same API You Know

```python
# This is pytest...
from pytest import fixture, mark, raises

@fixture
def database():
    db = Database()
    yield db
    db.disconnect()

@mark.parametrize("name,email", [
    ("Alice", "alice@example.com"),
    ("Bob", "bob@example.com"),
])
def test_create_user(database, name, email):
    user = database.create_user(name, email)
    assert user.name == name
```

```python
# This is rustest. See the difference?
from rustest import fixture, mark, parametrize

@fixture
def database():
    db = Database()
    yield db
    db.disconnect()

@parametrize("name,email", [
    ("Alice", "alice@example.com"),
    ("Bob", "bob@example.com"),
])
def test_create_user(database, name, email):
    user = database.create_user(name, email)
    assert user.name == name
```

**That's it.** Change your imports. Get massive speedups.

### Performance at Every Scale

| Suite Size | pytest | rustest | Speedup |
|-----------|--------|---------|---------|
| Small (< 20 tests) | 0.45s | 0.12s | **3-4× faster** |
| Medium (100-500 tests) | 1.20s | 0.15s | **5-8× faster** |
| Large (1,000+ tests) | 1.85s | 0.17s | **11× faster** |
| Very Large (5,000 tests) | 7.81s | 0.40s | **19× faster** |

[:octicons-arrow-right-24: See Full Benchmarks](../advanced/performance.md)

### No Plugin Dependencies

Common pytest plugins? They're built-in:

| pytest | rustest |
|--------|---------|
| `pip install pytest-asyncio` | **Built-in** with `@mark.asyncio` |
| `pip install pytest-mock` | **Built-in** with `mocker` fixture |
| `pip install pytest-codeblocks` | **Built-in** markdown testing |

Less to install. Less to maintain. More time coding.

[:octicons-arrow-right-24: Plugin Migration Guide](plugins.md)

---

## Try It Risk-Free

Already have pytest tests? Run them with rustest in 10 seconds:

```bash
pip install rustest
rustest --pytest-compat tests/
```

The `--pytest-compat` flag intercepts `import pytest` and provides rustest implementations. **No code changes required.**

See the speedup immediately. Decide later if you want to migrate.

[:octicons-arrow-right-24: 5-Minute Migration Guide](migration.md)

---

## What Works, What Doesn't

We implement the 20% of pytest features that cover 80% of use cases.

**✅ Supported:**

- Core features: `@fixture`, `@parametrize`, `@mark`, test classes, `conftest.py`
- Built-in fixtures: `tmp_path`, `tmpdir`, `monkeypatch`, `mocker`, `capsys`, `capfd`, `caplog`, `cache`, `request`
- Async testing: `@mark.asyncio` (built-in, no plugin needed)
- Mocking: `mocker` fixture (pytest-mock compatible)
- Test utilities: `raises()`, `skip()`, `xfail()`, `fail()`, `approx()`, `warns()`
- Parametrization: Including `pytest.param()` with custom IDs
- Fixture parametrization: `@fixture(params=[...])`  with `request.param`
- Request object: `request.node`, `request.config`, `request.param`

**❌ Not Supported (by design):**

- pytest plugins (they're a major performance bottleneck)
- Hook system and custom collectors
- Advanced pytest internals (`_pytest.*`)

**🚧 Not Yet (but planned):**

- Parallel execution control (`-n` workers)
- JUnit XML output
- HTML reports

[:octicons-arrow-right-24: Complete Feature Comparison Table](comparison.md)
[:octicons-arrow-right-24: Known Limitations](limitations.md)

---

## Real-World Results

Our own test suite (~500 tests) shows **3.6× speedup**:

| Runner | Tests | Time | Notes |
|--------|-------|------|-------|
| pytest | 457 tests | 1.95-2.04s | Requires pytest-asyncio plugin |
| rustest | 497 tests | 0.54-0.58s | **Built-in async support** |

The same 457 tests run with both runners thanks to import compatibility. Rustest includes 40 additional tests for its pytest compatibility layer.

**Key takeaway:** Real projects see significant speedups without sacrificing features.

---

## Common Questions

### "Will my pytest tests work?"

Most will! The compatibility mode (`--pytest-compat`) handles:

- ✅ `@pytest.fixture`, `@pytest.mark.*`, `@pytest.mark.parametrize()`
- ✅ Built-in fixtures (`tmp_path`, `monkeypatch`, `mocker`, `capsys`, etc.)
- ✅ `pytest.raises()`, `pytest.skip()`, `pytest.xfail()`, `pytest.fail()`
- ✅ Async tests with `@pytest.mark.asyncio`
- ✅ `pytest.param()` with custom IDs
- ✅ Fixture parametrization with `request.param`

Won't work:

- ❌ pytest plugins (by design—they're slow!)
- ❌ Custom hooks and collectors
- ❌ Advanced `_pytest` internals

[:octicons-arrow-right-24: See Full Compatibility Matrix](comparison.md)

### "What about coverage?"

Coverage.py works seamlessly:

```bash
coverage run -m rustest tests/
coverage report
coverage html
```

No plugins, no configuration hassles.

[:octicons-arrow-right-24: Coverage Integration Guide](coverage.md)

### "Can I migrate gradually?"

Absolutely! You can:

1. Run existing tests with `--pytest-compat` (no changes)
2. Gradually change imports from `pytest` to `rustest`
3. Keep pytest around for tests that need plugins
4. Use both runners in different environments

[:octicons-arrow-right-24: Migration Strategies](migration.md)

### "What's the catch?"

Honest trade-offs:

- **No plugin ecosystem** — Built-in features only (async, mocking, markdown testing)
- **Fewer advanced features** — We focus on the most common use cases
- **Less mature** — pytest has 10+ years of development; rustest is newer

But you get:

- **Dramatically faster tests** — 3-19× speedup
- **Simpler stack** — Fewer dependencies to manage
- **Better developer experience** — Fast tests change how you code

---

## What's Next?

Ready to try rustest? Choose your path:

<div class="grid cards" markdown>

-   :material-table: **See What's Supported**

    ---

    Complete feature comparison table

    [:octicons-arrow-right-24: Feature Comparison](comparison.md)

-   :material-rocket-launch: **Migrate in 5 Minutes**

    ---

    Step-by-step migration guide

    [:octicons-arrow-right-24: Migration Guide](migration.md)

-   :material-power-plug: **Replace pytest Plugins**

    ---

    Built-in alternatives to common plugins

    [:octicons-arrow-right-24: Plugin Guide](plugins.md)

-   :material-chart-line: **Coverage Integration**

    ---

    How to use coverage.py with rustest

    [:octicons-arrow-right-24: Coverage Guide](coverage.md)

-   :material-alert-circle: **Known Limitations**

    ---

    What's not supported (yet)

    [:octicons-arrow-right-24: Limitations](limitations.md)

-   :material-speedometer: **Performance Details**

    ---

    Benchmarks, methodology, and replication

    [:octicons-arrow-right-24: Performance Analysis](../advanced/performance.md)

</div>

---

## Philosophy

Pytest set the gold standard for Python testing APIs. We have enormous respect for pytest and its contributors.

Rustest doesn't try to replace pytest completely. Instead, we optimize for:

- **Speed** — Rust performance for test discovery and execution
- **Simplicity** — Built-in features instead of plugin complexity
- **Developer experience** — Fast feedback loops that change how you work

If you need pytest's full plugin ecosystem and advanced features, keep using pytest! It's a fantastic tool.

If you want pytest's clean API with dramatically faster execution, **try rustest**.

**Pytest nailed the API. Rustest brings the speed.**
