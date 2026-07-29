# Known Limitations

Rustest focuses on the most common pytest use cases with dramatically better performance. This means some pytest features are intentionally not supported, while others are planned for future releases.

This page honestly documents what doesn't work (yet) so you can make informed decisions.

---

## By Design: Not Planned

These features are **intentionally excluded** because they conflict with rustest's performance and simplicity goals.

### No pytest Plugin System

**Status:** ❌ Not planned

**Why:** Plugins are a major performance bottleneck. Loading, initializing, and running plugin hooks slows down test execution significantly.

**Alternative:** Common plugin features are built-in:

| Plugin | Rustest Alternative |
|--------|-------------------|
| pytest-asyncio | Built-in `@mark.asyncio` |
| pytest-mock | Built-in `mocker` fixture |
| pytest-codeblocks | Built-in markdown testing |
| pytest-cov | Built-in `--cov` / `--cov-report` (needs the `cov` extra) |

For other plugins, see the [Plugin Migration Guide](plugins.md).

### No Hook System

**Status:** ❌ Not planned

**Why:** Pytest's hook system (pytest_configure, pytest_collection_modifyitems, etc.) adds complexity and overhead. Most users don't need it.

**Alternative:** Use fixtures for setup/teardown and conftest.py for sharing.

### No Custom Collectors

**Status:** ❌ Not planned

**Why:** Custom test collection adds unpredictability and complexity.

**Alternative:** Rustest's built-in collectors handle:
- Python test files (`test_*.py`, `*_test.py`)
- Markdown files (`.md` with Python code blocks)

This covers 99% of use cases.

## Planned: Coming Soon

These features are **planned** for future releases.

### JUnit XML Output

**Status:** 🚧 Planned

**Current:** No XML output

**Planned:** Generate JUnit-compatible test reports:

```bash
rustest --junit-xml=report.xml
```

Needed for CI systems that consume JUnit reports.

**Workaround:** Most modern CI systems support text output or can be configured without XML.

**Tracking:** [GitHub Issue #XXX]

### HTML Test Reports

**Status:** 🚧 Planned

**Current:** No HTML reports

**Planned:** Generate HTML test reports:

```bash
rustest --html=report.html
```

**Workaround:** Use verbose output (`-v`) for detailed results. Coverage HTML reports work today ([guide](coverage.md)).

### Test Timeouts

**Status:** 🚧 Planned

**Current:** No built-in timeout support

**Planned:** Mark tests with timeout limits:

```python
from rustest import mark

@mark.timeout(5)  # Fail if takes longer than 5 seconds
def test_slow_operation():
    result = expensive_computation()
    assert result.success
```

**Workaround:** Use OS-level timeout commands or signal.alarm():

```python
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Test timed out")

def test_with_timeout():
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(5)  # 5 second timeout
    try:
        expensive_computation()
    finally:
        signal.alarm(0)  # Disable alarm
```

### Fixture Finalization on Ctrl+C

**Status:** 🚧 Planned

**Current:** Fixtures may not clean up if you interrupt tests with Ctrl+C

**Planned:** Ensure fixture teardown runs even when interrupted.

**Workaround:** Let tests finish normally. If you interrupt, manually clean up resources if needed.

---

## Partially Supported

These features work, but with limitations.

### Request Object

**Status:** ⚠️ Partial support

**What works:**

- `request.param` — Get parametrized fixture value
- `request.node.name` — Test name
- `request.node.nodeid` — Full node ID
- `request.node.get_closest_marker(name)` — Get marker
- `request.node.add_marker(mark)` — Add marker
- `request.config.getoption(name)` — Get CLI option
- `request.config.getini(name)` — Get config value
- `request.config.option` — Access option namespace

**What doesn't work:**

- `request.node.parent` — Always None
- `request.node.session` — Always None
- Advanced pytest-specific request features

Most common `request` usage patterns work fine.

---

## Not Supported: pytest Internals

These pytest internals are **not available**:

### _pytest Module

**Status:** ❌ Not available

**What:** Internal pytest implementation details (`_pytest.*` modules)

**Why:** These are pytest-specific and not part of the public API.

**Impact:** If your tests import from `_pytest`, they won't work with rustest. Refactor to use public APIs.

### pytest_plugins Variable

**Status:** ❌ Not available

**What:** The `pytest_plugins` variable for loading plugins

**Why:** Rustest doesn't support plugins.

**Impact:** If your conftest.py has `pytest_plugins = [...]`, remove it.

---

## pytest Compatibility Shim Limitations

The shim that makes `import pytest` work is installed on **every** run -- there is no flag to
turn it on or off. (`--pytest-compat` used to be that flag; it was removed, and passing it now
exits 4 with a message saying so.) These limits therefore apply to every run, not to a mode:

### Plugin APIs Are Stubbed

**Impact:** Plugins can import but won't function.

For example, `pytest_asyncio` imports without error, but rustest uses its own async implementation.

**Workaround:** Don't rely on plugins. Use native rustest features.

### Some Advanced Features Don't Work

**Impact:** Advanced pytest features that rely on internals may fail.

**Examples:**

- Custom pytest collectors
- Complex hook interactions

**Workaround:** Migrate to native rustest imports for full functionality.

---

## Workarounds & Alternatives

### Need Plugins?

**Keep using pytest!** It's a great tool. Rustest isn't meant to replace pytest completely—just provide a faster alternative for common use cases.

**Hybrid approach:**

- Use rustest for fast unit tests
- Use pytest for tests that need specific plugins
- Run both in CI: `rustest tests/unit/ && pytest tests/integration/`

### Need Custom Collectors?

**Alternative:** Use Python's standard import system and test discovery patterns.

Most custom collectors are for non-standard test file layouts. Consider restructuring to use standard `test_*.py` patterns.

---

## Feature Requests

Missing something important? Let us know!

- [View existing feature requests](https://github.com/Apex-Engineers-Inc/rustest/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement)
- [Open a new feature request](https://github.com/Apex-Engineers-Inc/rustest/issues/new)

We prioritize features based on:

1. **Impact:** How many users need it?
2. **Performance:** Does it conflict with speed goals?
3. **Simplicity:** Does it add complexity?

---

## Philosophy: Focus on Common Use Cases

Rustest intentionally focuses on **the 20% of pytest features that cover 80% of use cases**.

We optimize for:

- ✅ **Speed** — Tests should be fast
- ✅ **Simplicity** — Less configuration, fewer dependencies
- ✅ **Common patterns** — Fixtures, parametrization, marks

We don't optimize for:

- ❌ **Edge cases** — Obscure pytest features few people use
- ❌ **Plugin complexity** — Plugins are slow and add maintenance burden
- ❌ **Perfect pytest compatibility** — We aim for common-case compatibility

**If you need pytest's full feature set, use pytest!** It's an excellent tool. Rustest is for users who want pytest's elegant API with dramatically better performance and don't need advanced features.

---

## Honest Assessment: Is Rustest Right for You?

### ✅ Rustest is great if you:

- Use standard pytest features (fixtures, parametrization, marks)
- Want dramatically faster tests (8.5× average speedup)
- Don't rely on pytest plugins (or only use common ones we've built in)
- Value simplicity and performance
- Are writing new tests or willing to migrate

### ❌ Stick with pytest if you:

- Heavily use pytest plugins
- Rely on custom hooks or collectors
- Need 100% pytest feature compatibility
- Have complex plugin interactions
- Don't have performance issues with pytest

**Both tools are excellent.** Choose based on your needs.

---

## What's Next?

Now that you know the limitations:

- [Feature Comparison](comparison.md) — See what IS supported
- [Migration Guide](migration.md) — Migrate from pytest
- [Plugin Guide](plugins.md) — Replace common plugins
- [Core Guide](../guide/writing-tests.md) — Learn all rustest features

**We're always improving!** Check the [changelog](../CHANGELOG.md) for new features.
