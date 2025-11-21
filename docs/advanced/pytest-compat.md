# Pytest Compatibility Mode

rustest provides a `--pytest-compat` mode that allows you to run existing pytest test suites with minimal or no code changes. This mode intercepts `import pytest` statements and provides rustest implementations transparently.

## Quick Start

Try rustest on your existing pytest suite:

```bash
# Using uvx (no installation needed)
uvx rustest --pytest-compat tests/

# Or install and run
pip install rustest
rustest --pytest-compat tests/
```

That's it! Your existing pytest tests will run with rustest's performance benefits.

## Supported Features

### Core Decorators

- ✅ `@pytest.fixture` - All scopes (function, class, module, session)
- ✅ `@pytest.fixture(params=[...])` - Fixture parametrization with `request.param`
- ✅ `@pytest.mark.parametrize()` - Test parametrization
- ✅ `@pytest.mark.skip()` - Skip tests
- ✅ `@pytest.mark.skipif()` - Conditional skipping
- ✅ `@pytest.mark.xfail()` - Expected failures
- ✅ `@pytest.mark.asyncio` - Async test support (built-in, no plugin needed)
- ✅ Custom marks (`@pytest.mark.slow`, `@pytest.mark.integration`, etc.)

### Functions

- ✅ `pytest.raises()` - Exception assertions
- ✅ `pytest.skip()` - Dynamic test skipping
- ✅ `pytest.xfail()` - Mark test as expected to fail
- ✅ `pytest.fail()` - Explicitly fail a test
- ✅ `pytest.approx()` - Floating-point comparisons
- ✅ `pytest.warns()` - Warning assertions
- ✅ `pytest.deprecated_call()` - Deprecation warning capture
- ✅ `pytest.param()` - Parametrize with custom IDs
- ✅ `pytest.importorskip()` - Skip if module unavailable

### Built-in Fixtures

All pytest built-in fixtures are available:

- ✅ `tmp_path` - Temporary directory (pathlib.Path)
- ✅ `tmpdir` - Temporary directory (py.path.local)
- ✅ `tmp_path_factory` - Session-scoped temp path factory
- ✅ `tmpdir_factory` - Session-scoped tmpdir factory
- ✅ `monkeypatch` - Patching and mocking
- ✅ `capsys` - Capture stdout/stderr
- ✅ `capfd` - Capture file descriptors
- ✅ `caplog` - Capture logging output
- ✅ `cache` - Persistent cache between test runs
- ✅ `request` - **Enhanced with node and config support**

### Request Object Features

The `request` fixture now provides comprehensive test metadata and configuration access:

**request.param** - Current parameter value for parametrized fixtures:
```python
@pytest.fixture(params=[1, 2, 3])
def number(request):
    return request.param  # Access parameter value
```

**request.node** - Test node with marker access:
```python
@pytest.fixture
def conditional_setup(request):
    # Check for markers
    marker = request.node.get_closest_marker("slow")
    if marker:
        pytest.skip("Skipping slow test")

    # Access test name
    print(f"Setting up: {request.node.name}")

    # Check keywords
    if "integration" in request.node.keywords:
        return setup_integration()
    return setup_unit()
```

**request.config** - Configuration and options:
```python
@pytest.fixture
def database(request):
    # Get command-line options
    db_url = request.config.getoption("--db-url", default="sqlite:///:memory:")

    # Get ini configuration
    timeout = request.config.getini("timeout")

    # Access verbosity
    verbose = request.config.getoption("verbose", default=0)
    if verbose > 1:
        print(f"Connecting to {db_url}")

    return connect(db_url, timeout=timeout)
```

#### Request Object API

**Node attributes:**
- `node.name` - Test name
- `node.nodeid` - Full test identifier (e.g., "tests/test_foo.py::test_bar")
- `node.keywords` - Dictionary of keywords/markers
- `node.get_closest_marker(name)` - Get marker by name (returns marker object or None)
- `node.add_marker(marker)` - Add marker dynamically
- `node.listextrakeywords()` - Get set of marker names

**Config attributes:**
- `config.getoption(name, default=None)` - Get command-line option
- `config.getini(name)` - Get ini configuration value
- `config.option` - Namespace for accessing options as attributes
- `config.rootpath` - Root directory (pathlib.Path)
- `config.pluginmanager` - Stub PluginManager (limited functionality)

## Known Limitations

### Not Supported

❌ **Pytest plugins** - rustest does not support pytest plugins (by design)
- No pytest-django, pytest-flask, pytest-mock plugins
- See [Plugin Compatibility Guide](pytest-plugins.md) for alternatives

❌ **_pytest internals** - No access to pytest internal modules
- No `_pytest.assertion.rewrite`
- No `_pytest.fixtures`, `_pytest.config`, `_pytest.nodes`
- Projects importing these will need modification

❌ **Advanced hook system**
- No pytest hook specifications
- No `pytest_configure`, `pytest_collection_modifyitems`, etc.
- Custom conftest.py hooks won't work

❌ **Some request object features**
- `request.node.parent` - Always None
- `request.node.session` - Always None
- `request.function`, `request.cls`, `request.module` - Always None
- `request.addfinalizer()` - Not supported (use fixture yield instead)
- `request.getfixturevalue()` - Not supported (declare as parameter)

### Partial Support

⚠️ **request.config.pluginmanager** - Stub implementation
- Basic methods exist but return safe defaults
- `get_plugin(name)` always returns None
- `hasplugin(name)` always returns False
- Plugin registration is a no-op

⚠️ **Async support** - Built-in @mark.asyncio works differently
- No event_loop fixture
- No pytest_asyncio.fixture
- Auto mode (`asyncio_mode = "auto"`) not supported
- Use rustest's `@mark.asyncio` decorator

## Migration Examples

### Basic Migration

No changes needed for most tests:

```python
# This pytest code works as-is with rustest --pytest-compat
import pytest

@pytest.fixture
def database():
    db = setup_database()
    yield db
    db.close()

@pytest.mark.parametrize("value", [1, 2, 3])
def test_processing(database, value):
    result = database.process(value)
    assert result > 0
```

### Using Request Object

```python
import pytest

@pytest.fixture
def conditional_fixture(request):
    # Access test metadata
    test_name = request.node.name
    print(f"Running: {test_name}")

    # Check for markers
    if request.node.get_closest_marker("skip_db"):
        return None

    # Get configuration
    db_host = request.config.getoption("--db-host", default="localhost")

    return setup(db_host)

@pytest.mark.skip_db
def test_without_db(conditional_fixture):
    assert conditional_fixture is None

def test_with_db(conditional_fixture):
    assert conditional_fixture is not None
```

### Async Tests

```python
import pytest

# Works with rustest --pytest-compat
@pytest.mark.asyncio
async def test_async_operation():
    result = await async_function()
    assert result == expected

# Parametrized async test
@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, 2, 3])
async def test_async_parametrized(value):
    result = await process(value)
    assert result > 0
```

### Warning Capture

```python
import pytest
import warnings

def test_warning_capture():
    with pytest.warns(UserWarning, match="deprecated"):
        warnings.warn("This is deprecated", UserWarning)

def test_deprecation():
    with pytest.deprecated_call():
        warnings.warn("Old function", DeprecationWarning)
```

## Compatibility Checklist

Use this checklist to assess your pytest suite's compatibility:

### ✅ Highly Compatible (Should work with no changes)

- [ ] Uses `@pytest.fixture` for test setup
- [ ] Uses `@pytest.mark.parametrize` for test generation
- [ ] Uses built-in fixtures (tmp_path, tmpdir, monkeypatch, capsys)
- [ ] Uses `pytest.raises()`, `pytest.approx()`, `pytest.skip()`
- [ ] Uses custom marks (slow, integration, etc.)
- [ ] Uses `@pytest.mark.asyncio` for async tests
- [ ] No pytest plugins installed
- [ ] No imports from `_pytest` modules

### ⚠️ May Require Minor Changes

- [ ] Uses `request.param` for parametrized fixtures (supported)
- [ ] Uses `request.node.get_closest_marker()` (supported)
- [ ] Uses `request.config.getoption()` (supported)
- [ ] Uses pytest-mock (migrate to unittest.mock)
- [ ] Uses pytest-cov (use coverage.py directly)
- [ ] Imports from `_pytest` modules (remove or conditionally import)

### ❌ Requires Significant Work or Not Compatible

- [ ] Heavy use of pytest plugins (pytest-django, etc.)
- [ ] Custom pytest hooks (pytest_configure, etc.)
- [ ] Uses `request.addfinalizer()` or `request.getfixturevalue()`
- [ ] Relies on pytest internals
- [ ] Custom collectors or test generation

## Performance Expectations

With `--pytest-compat`, expect:

- **3-4× faster** for small suites (< 100 tests)
- **5-8× faster** for medium suites (100-500 tests)
- **11-19× faster** for large suites (1000+ tests)

Performance is similar to native rustest, with a small overhead for pytest compatibility shim.

## Gradual Migration Strategy

You don't have to migrate everything at once:

### Phase 1: Try --pytest-compat

```bash
# Test your existing suite
rustest --pytest-compat tests/
```

If it works, you're done! Keep using pytest syntax with rustest's speed.

### Phase 2: Migrate Imports (Optional)

For better IDE support and type checking, migrate imports:

```python
# Before
import pytest

# After
from rustest import fixture, mark, parametrize, raises, approx
```

Use a conftest.py shim for compatibility:

```python
# conftest.py
try:
    from rustest import fixture, mark, parametrize
except ImportError:
    from pytest import fixture, mark
    from pytest import mark as parametrize_mark
    parametrize = parametrize_mark.parametrize
```

### Phase 3: Optimize (Optional)

Take advantage of rustest-specific features:

- Use rustest's built-in async support
- Leverage parallel execution
- Use rustest's optimized fixture injection

## Troubleshooting

### "ModuleNotFoundError: No module named '_pytest'"

Your code imports pytest internals. Solutions:

```python
# Option 1: Conditional import
try:
    from _pytest.fixtures import FixtureDef
except ImportError:
    # rustest compatibility - use alternative
    FixtureDef = None

# Option 2: Remove the import
# Many _pytest imports are only needed for type hints
# Replace with Any or remove type annotation
```

### "request.getfixturevalue() not supported"

Replace with fixture parameters:

```python
# Before
@pytest.fixture
def my_fixture(request):
    other = request.getfixturevalue('other_fixture')
    return setup(other)

# After
@pytest.fixture
def my_fixture(other_fixture):  # Direct parameter
    return setup(other_fixture)
```

### "request.addfinalizer() not supported"

Use fixture teardown with yield:

```python
# Before
@pytest.fixture
def my_fixture(request):
    resource = setup()
    request.addfinalizer(lambda: cleanup(resource))
    return resource

# After
@pytest.fixture
def my_fixture():
    resource = setup()
    yield resource
    cleanup(resource)
```

### Tests hang with @mark.asyncio

Ensure you're using async functions:

```python
# Wrong - will hang
@mark.asyncio
def test_async():  # Not async!
    await something()

# Correct
@mark.asyncio
async def test_async():  # async keyword
    await something()
```

## Best Practices

### 1. Test Compatibility First

Before migrating, run your suite with `--pytest-compat`:

```bash
rustest --pytest-compat tests/ -v
```

Check for errors and unsupported features.

### 2. Use Request Object Appropriately

```python
# Good - conditional setup based on markers
@pytest.fixture
def database(request):
    if request.node.get_closest_marker("mock_db"):
        return MockDatabase()
    return RealDatabase()

# Good - configuration-driven behavior
@pytest.fixture
def api_client(request):
    base_url = request.config.getoption("--api-url", default="http://localhost")
    return APIClient(base_url)
```

### 3. Avoid Pytest Internals

```python
# Bad - uses pytest internals
from _pytest.fixtures import FixtureDef

# Good - use public API
from rustest import fixture
```

### 4. Prefer Explicit Dependencies

```python
# Bad - dynamic fixture lookup
def test_example(request):
    db = request.getfixturevalue('database')

# Good - explicit dependency
def test_example(database):
    db = database
```

## See Also

- [Plugin Compatibility Guide](pytest-plugins.md) - Alternatives to popular pytest plugins
- [Migration Guide](../migration-guide.md) - Complete migration guide
- [Comparison with pytest](comparison.md) - Feature comparison
- [Request Object Documentation](../REQUEST_OBJECT_ENHANCEMENTS.md) - Detailed request object API
