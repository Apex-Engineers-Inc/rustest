# Comparison with pytest

Rustest aims to provide the most commonly-used pytest features with dramatically better performance. This page compares the two in detail.

## Feature Comparison Table

| Feature | pytest | rustest | Notes |
|---------|--------|---------|-------|
| **Core Test Discovery** |
| `test_*.py` / `*_test.py` files | ✅ | ✅ | Rustest uses Rust for dramatically faster discovery |
| Test function detection (`test_*`) | ✅ | ✅ | |
| Test class detection (`Test*`) | ✅ | ✅ | Full pytest-style class support with fixture methods |
| Pattern-based filtering | ✅ | ✅ | `-k` pattern matching |
| Markdown code block testing | ✅ (`pytest-codeblocks`) | ✅ | Built-in support for testing Python blocks in `.md` files |
| **Fixtures** |
| `@fixture` decorator | ✅ | ✅ | Rust-based dependency resolution |
| Fixture dependency injection | ✅ | ✅ | Much faster in rustest |
| Fixture scopes (function/class/module/session) | ✅ | ✅ | Full support for all scopes |
| Yield fixtures (setup/teardown) | ✅ | ✅ | Full support with cleanup |
| Fixture methods within test classes | ✅ | ✅ | Define fixtures as class methods |
| Fixture parametrization | ✅ | ✅ | Full support with `@fixture(params=[...])` and `request.param` |
| `conftest.py` | ✅ | ✅ | Shared fixtures across test files |
| **Built-in Fixtures** |
| `tmp_path` / `tmp_path_factory` | ✅ | ✅ | Temporary directories with pathlib.Path |
| `tmpdir` / `tmpdir_factory` | ✅ | ✅ | Legacy py.path support |
| `monkeypatch` | ✅ | ✅ | Patch attributes, env vars, dict items |
| `capsys` / `capfd` | ✅ | ✅ | Capture stdout/stderr |
| `caplog` | ✅ | ✅ | Capture logging output |
| `cache` | ✅ | ✅ | Persistent cache between test runs |
| `request` | ✅ | ✅ | Access to fixture parameters and metadata |
| `request.param` | ✅ | ✅ | Parameter value for parametrized fixtures |
| `request.node` | ✅ | ✅ | Test metadata, markers (name, nodeid, get_closest_marker, add_marker, keywords) |
| `request.config` | ✅ | ✅ | Configuration access (getoption, getini, option namespace) |
| **Test Utilities** |
| `pytest.raises()` | ✅ | ✅ | Exception assertion context manager |
| `pytest.skip()` | ✅ | ✅ | Dynamically skip a test |
| `pytest.xfail()` | ✅ | ✅ | Mark test as expected to fail |
| `pytest.fail()` | ✅ | ✅ | Explicitly fail a test |
| `pytest.approx()` | ✅ | ✅ | Numeric comparison with tolerance |
| `pytest.warns()` | ✅ | ✅ | Warning assertion context manager |
| `pytest.deprecated_call()` | ✅ | ✅ | Check for deprecation warnings |
| `pytest.importorskip()` | ✅ | ✅ | Skip if module unavailable |
| **Async Support** |
| `@pytest.mark.asyncio` | ✅ (plugin) | ✅ | Built-in async test support (no plugin needed) |
| Async fixtures | ✅ (plugin) | ✅ | Native support for async fixture functions |
| Event loop scopes | ✅ (plugin) | ✅ | Loop scope control (function, module, session) |
| **Parametrization** |
| `@parametrize` decorator | ✅ | ✅ | Full support with custom IDs |
| Multiple parameter sets | ✅ | ✅ | |
| Parametrize with fixtures | ✅ | ✅ | |
| **Marks** |
| `@mark.skip` / `@skip` | ✅ | ✅ | Skip tests with reasons |
| Custom marks (`@mark.slow`, etc.) | ✅ | ✅ | Full mark support |
| Mark with arguments | ✅ | ✅ | `@mark.timeout(30)` |
| Selecting tests by mark (`-m`) | ✅ | ✅ | Full support including boolean expressions (`not`, `and`, `or`) |
| **Test Execution** |
| Detailed assertion introspection | ✅ | ❌ | Uses standard Python assertions |
| Parallel execution | ✅ (`pytest-xdist`) | 🚧 | Planned (Rust makes this easier) |
| Test isolation | ✅ | ✅ | |
| Stdout/stderr capture | ✅ | ✅ | `--no-capture` / `-s` |
| **Reporting** |
| Pass/fail/skip summary | ✅ | ✅ | |
| Failure tracebacks | ✅ | ✅ | Full Python traceback support |
| Duration reporting | ✅ | ✅ | Per-test timing |
| JUnit XML output | ✅ | 🚧 | Planned |
| HTML reports | ✅ (`pytest-html`) | 🚧 | Planned |
| **Advanced Features** |
| Plugins | ✅ | ❌ | Not supported by design ([see why](pytest-plugins.md)) |
| Hooks | ✅ | ❌ | Not supported by design |
| Custom collectors | ✅ | ❌ | Not supported by design |
| **Developer Experience** |
| Fully typed Python API | ⚠️ | ✅ | rustest uses `basedpyright` strict mode |
| Fast CI/CD runs | ⚠️ | ✅ | 8.5× average speedup (peaks at 19×) for dramatically shorter feedback loops |

**Legend:**
- ✅ Fully supported
- 🚧 Planned or in progress
- ⚠️ Partial support
- ❌ Not planned

## Philosophy

**Rustest implements the 20% of pytest features that cover 80% of use cases**, with a focus on raw speed and simplicity.

### When to Use rustest

✅ **Use rustest when:**

- You want faster test execution (8.5× average speedup, up to 19×)
- You want predictable gains: **~3–4×** on tiny suites, **~5–8×** once you reach a few hundred tests, and **11–19×** on thousand-test workloads
- You use standard pytest features (fixtures, parametrization, marks)
- You want a simple, focused testing tool
- You value fast CI/CD feedback loops
- You're writing new tests and want modern tooling
- You test markdown documentation

### When to Use pytest

✅ **Use pytest when:**

- You need pytest plugins (pytest-django, pytest-cov, etc.) - [see plugin migration guide](pytest-plugins.md)
- You rely on advanced pytest features (hooks, custom collectors)
- You have complex existing pytest infrastructure
- You need detailed assertion introspection
- You require specific output formats (JUnit XML, HTML reports)

## Migration from pytest

### Easy Migration

Most pytest test suites can switch to rustest with minimal changes:

<!--rustest.mark.skip-->
```python
# pytest code
from pytest import fixture, parametrize, mark, approx, raises

@fixture
def database():
    return setup_database()

@parametrize("value,expected", [(1, 2), (2, 4)])
def test_double(value, expected):
    assert value * 2 == expected

# rustest code - identical!
from rustest import fixture, parametrize, mark, approx, raises

@fixture
def database():
    return setup_database()

@parametrize("value,expected", [(1, 2), (2, 4)])
def test_double(value, expected):
    assert value * 2 == expected
```

### What Stays the Same

- Test discovery patterns
- Fixture syntax and scopes
- Parametrization syntax
- Mark syntax
- Exception testing with `raises()`
- Numeric comparison with `approx()`
- Test class structure

### What Changes

#### Import Statements

<!--rustest.mark.skip-->
```python
# pytest
import pytest
from pytest import fixture, parametrize

# rustest
import rustest
from rustest import fixture, parametrize
```

#### Running Tests

```bash
# pytest
pytest tests/

# rustest
rustest tests/
```

#### Configuration

pytest uses `pytest.ini` or `pyproject.toml`:

```toml
# pytest config
[tool.pytest.ini_options]
testpaths = ["tests"]
```

rustest uses command-line arguments (no config file yet):

```bash
rustest tests/
```

### Compatibility Layer

For gradual migration, you can use both in the same project:

<!--rustest.mark.skip-->
```python
# tests/conftest.py
try:
    from rustest import fixture, parametrize, mark
except ImportError:
    from pytest import fixture, mark
    from pytest import param as parametrize
```

## Feature Deep Dive

### Fixtures

Both support the same fixture features:

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import fixture  # or from pytest import fixture

@fixture(scope="session")
def database():
    db = setup()
    yield db
    db.cleanup()
```

**Rustest advantage:** Faster fixture resolution due to Rust-based dependency graph.

### Parametrization

Both use the same syntax:

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import parametrize  # or from pytest import parametrize

@parametrize("x,y", [(1, 2), (3, 4)], ids=["first", "second"])
def test_values(x, y):
    assert x < y
```

**Rustest advantage:** Much faster with large parameter sets (see [Performance](performance.md)).

### Marks

Both support custom marks:

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import mark  # or from pytest import mark

@mark.slow
@mark.integration
def test_expensive():
    pass
```

**Both support mark filtering:** Filter by marks with `-m "slow"`, `-m "not slow"`, `-m "slow and integration"`.

### Assertion Helpers

Both provide `approx()` and `raises()`:

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import approx, raises  # or from pytest import approx, raises

def test_comparison():
    assert 0.1 + 0.2 == approx(0.3)

    with raises(ValueError, match="invalid"):
        raise ValueError("invalid input")
```

### Test Classes

Both support the same class structure:

<!--rustest.mark.skip-->
```python
# Works identically in both
class TestMath:
    @fixture(scope="class")
    def calculator(self):
        return Calculator()

    def test_add(self, calculator):
        assert calculator.add(2, 3) == 5
```

## Performance Comparison

See the [Performance](performance.md) page for detailed benchmarks.

**Summary:**
- **~2.1x faster** on typical test suites
- **~24x faster** on heavily parametrized tests
- Scales better with larger test suites

## Ecosystem

### pytest Ecosystem

- **Huge plugin ecosystem**: hundreds of plugins
- **Wide adoption**: industry standard
- **Extensive documentation**: many tutorials and guides
- **IDE integration**: built into most Python IDEs

### rustest Ecosystem

- **No plugins**: focused on core functionality
- **Growing adoption**: new but actively developed
- **Modern documentation**: clean, comprehensive docs
- **Standard IDE support**: works with any Python IDE

## Future Roadmap

Planned rustest features to increase pytest compatibility:

- 🚧 JUnit XML output
- 🚧 Parallel test execution
- 🚧 HTML reports

Features not planned:

- ❌ Plugin system (by design)
- ❌ Hooks (by design)
- ❌ Custom collectors (by design)

## Conclusion

**Choose rustest if** you want:
- Fast test execution
- Simple, focused tooling
- Modern development experience
- Standard pytest patterns

**Stick with pytest if** you need:
- Plugin ecosystem
- Advanced customization
- Specific output formats
- Maximum compatibility

Both are excellent tools! Rustest aims to complement pytest by providing a faster alternative for common use cases.

## See Also

- [Pytest Plugins](pytest-plugins.md) - Migration guide for popular pytest plugins
- [Performance](performance.md) - Detailed performance analysis
- [Getting Started](../getting-started/quickstart.md) - Try rustest
- [Development](development.md) - Contribute to rustest
