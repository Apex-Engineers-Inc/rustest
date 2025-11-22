# Pytest Plugins and rustest

!!! warning "Pytest plugins are not supported"
    rustest **does not support pytest plugins** and this is an intentional design decision. This page explains why and provides concrete migration strategies for the most popular pytest plugins.

## Why rustest Doesn't Support Plugins

### The Technical Reasons

Pytest's plugin system is built on **pluggy**, a sophisticated hook-based framework with approximately 60 hooks across 9 different categories (initialization, collection, execution, reporting, fixtures, etc.). Supporting this system in rustest would require:

**1. Architectural Mismatch**

rustest's core value proposition is its **Rust-powered performance**. The Rust engine owns test discovery, execution, and reporting. Plugins would require frequent Rust↔Python FFI (Foreign Function Interface) boundary crossings:

- **Collection phase**: 10+ hook calls per test file
- **Execution phase**: 5+ hook calls per test
- **For 1,000 tests**: Potentially 15,000+ FFI calls

Each FFI call has overhead that would **significantly negate rustest's performance benefits**. Our benchmarks show 8.5× average speedup (up to 19×) - plugin support could reduce this to 2-3× or less.

**2. Implementation Complexity**

Full plugin support would require:

- Integrating the `pluggy` library into rustest
- Implementing ~60 hook specifications
- Exposing Rust internal state (Config, Session, Items, Reports) to Python
- Bidirectional state synchronization across the FFI boundary
- Hook execution ordering (tryfirst, trylast, wrappers)
- Dynamic argument injection and pruning

**Estimated effort**: 14-19 weeks of full-time development.

**3. Maintenance Burden**

- Must track pytest's hook API changes across versions
- Need to maintain compatibility matrix with pytest versions
- Debug interactions between multiple plugins
- Support overhead for plugin-related issues
- Some plugins use private pytest APIs that may not be replicable

### The Philosophical Reasons

rustest follows the **80/20 principle**: implement the 20% of pytest features that cover 80% of real-world use cases, with a focus on performance and simplicity.

**Core philosophy**:

- ✅ **Speed**: Dramatically faster test execution for most projects
- ✅ **Simplicity**: Clean codebase without complex plugin infrastructure
- ✅ **Focused**: Core testing features done extremely well
- ❌ **Not everything**: Deliberately excludes niche features

**Design goal**: Be the best fast test runner for 90% of Python projects, not a perfect pytest clone for 100% of projects.

### What About Migration?

The good news: **Most projects don't need plugins** to migrate! rustest already provides:

- ✅ Full fixture support (all scopes, teardown, dependency injection)
- ✅ Parametrization with custom IDs
- ✅ Marks and filtering
- ✅ Built-in fixtures (tmp_path, tmpdir, monkeypatch, capsys, capfd)
- ✅ Exception testing (raises, match patterns)
- ✅ Async testing support (@mark.asyncio)
- ✅ Warning capture (warns, deprecated_call)

For projects using popular plugins, we provide **built-in alternatives** or **migration strategies** below.

---

## Top 10 Pytest Plugins: Migration Guide

Based on download statistics (October 2025), here's how to migrate from the most popular pytest plugins.

### 1. pytest-cov (87.7M downloads/month)

**What it does**: Code coverage reporting

**Migration strategy**: Use Python's built-in `coverage.py` directly

=== "With pytest-cov"
    ```bash
    pytest --cov=myproject --cov-report=html --cov-report=term tests/
    ```

=== "With rustest"
    ```bash
    # Option 1: Use coverage.py directly
    coverage run -m rustest tests/
    coverage report
    coverage html

    # Option 2: Use coverage.py with rustest as a module
    coverage run --source=myproject -m rustest tests/
    coverage html
    ```

**Configuration**: Create a `.coveragerc` or `pyproject.toml` config:

```toml
[tool.coverage.run]
source = ["myproject"]
omit = ["tests/*", "*/venv/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
]
```

**CI/CD integration**:

```yaml
# GitHub Actions example
- name: Run tests with coverage
  run: |
    coverage run -m rustest tests/
    coverage report
    coverage xml  # For codecov.io, coveralls.io, etc.
```

!!! tip "Performance"
    Using coverage.py directly with rustest still provides significant speedup over pytest due to rustest's faster test execution.

---

### 2. pytest-xdist (60.3M downloads/month)

**What it does**: Parallel and distributed test execution

**Migration strategy**: **Not yet available** - parallel execution is planned for future releases

=== "With pytest-xdist"
    ```bash
    pytest -n 4 tests/              # Run on 4 CPUs
    pytest -n auto tests/           # Auto-detect CPU count
    pytest --dist=loadscope tests/  # Distribute by module
    ```

=== "With rustest"
    ```bash
    # Tests currently run sequentially
    rustest tests/

    # Parallel execution planned for future release
    # rustest -n 4 tests/      # Not yet available
    # rustest -n auto tests/   # Not yet available
    ```

**Current status**: Tests run **sequentially** in rustest. Parallel execution is a planned feature.

!!! warning "Not Yet Implemented"
    Rustest does **not** currently run tests in parallel. However, it's still **8.5× faster than pytest on average** even when running sequentially.

    **Why it's still faster:** Rust eliminates Python's overhead in test discovery, fixture resolution, and module imports. For most projects, sequential rustest outperforms parallel pytest-xdist.

**If you need parallelization today**:

Keep using pytest-xdist! It's a great tool. Or use both:

```bash
# Fast sequential tests with rustest
rustest tests/unit/

# Slow integration tests in parallel with pytest
pytest -n auto tests/integration/
```

---

### 3. pytest-asyncio (58.9M downloads/month)

**What it does**: Support for testing asyncio code

**Migration strategy**: rustest has **built-in async support**

=== "With pytest-asyncio"
    ```python
    import pytest

    @pytest.mark.asyncio
    async def test_async_function():
        result = await some_async_operation()
        assert result == expected
    ```

=== "With rustest"
    ```python
    from rustest import mark

    @mark.asyncio
    async def test_async_function():
        result = await some_async_operation()
        assert result == expected
    ```

**Advanced features**:

```python
from rustest import mark

# Specify event loop scope
@mark.asyncio(loop_scope="function")  # New loop per test (default)
async def test_with_function_scope():
    pass

@mark.asyncio(loop_scope="module")  # Shared loop across module
async def test_with_module_scope():
    pass

# Works with parametrization
from rustest import parametrize, mark

@mark.asyncio
@parametrize("value", [1, 2, 3])
async def test_parametrized_async(value):
    result = await process(value)
    assert result > 0
```

!!! success "Fully supported"
    rustest has full built-in support for async tests with `@mark.asyncio`. No plugin needed!

**Limitations**:

- Event loop fixture (`event_loop`) is not available
- Cannot use `pytest_asyncio.fixture` for async fixtures (use regular fixtures with async functions)
- Auto mode (`asyncio_mode = "auto"`) is not supported

**Async fixtures**:

```python
from rustest import fixture, mark

@fixture
async def async_database():
    """Async fixtures work without pytest-asyncio"""
    db = await setup_database()
    yield db
    await db.close()

@mark.asyncio
async def test_with_async_fixture(async_database):
    result = await async_database.query("SELECT 1")
    assert result == 1
```

---

### 4. pytest-mock (50.7M downloads/month)

**What it does**: Thin wrapper around `unittest.mock` providing a `mocker` fixture

**Migration strategy**: Use `unittest.mock` directly or create a simple fixture

=== "With pytest-mock"
    ```python
    def test_function(mocker):
        mock_obj = mocker.patch('module.ClassName')
        mock_obj.return_value = 42
        assert module.ClassName() == 42
    ```

=== "With rustest (Option 1: Direct)"
    ```python
    from unittest.mock import patch, MagicMock

    def test_function():
        with patch('module.ClassName') as mock_obj:
            mock_obj.return_value = 42
            assert module.ClassName() == 42
    ```

=== "With rustest (Option 2: Fixture)"
    ```python
    # In conftest.py
    from rustest import fixture
    from unittest.mock import Mock, patch, MagicMock

    @fixture
    def mocker():
        """pytest-mock compatible mocker fixture"""
        class Mocker:
            Mock = Mock
            MagicMock = MagicMock
            patch = patch

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        return Mocker()

    # In test file
    def test_function(mocker):
        mock_obj = mocker.patch('module.ClassName')
        mock_obj.return_value = 42
        assert module.ClassName() == 42
    ```

**Common patterns**:

<!--rustest.mark.skip-->
```python
from unittest.mock import patch, MagicMock, call

# Patching
def test_patch():
    with patch('requests.get') as mock_get:
        mock_get.return_value.status_code = 200
        pass  # Test code here

# Multiple patches
def test_multiple_patches():
    with patch('module.func1') as mock1, \
         patch('module.func2') as mock2:
        pass  # Test code here
```

<!--rustest.mark.skip-->
```python
# Spy on methods
def test_spy():
    obj = MyClass()
    with patch.object(obj, 'method', wraps=obj.method) as spy:
        obj.method(42)
        spy.assert_called_once_with(42)

# Mock attributes
def test_mock_attributes():
    mock = MagicMock()
    mock.attribute.return_value = 'value'
    assert mock.attribute() == 'value'
```

!!! tip "No plugin needed"
    Python's `unittest.mock` is powerful enough for most use cases. The pytest-mock plugin is just a thin convenience wrapper.

---

### 5. pytest-metadata (20.7M downloads/month)

**What it does**: Access to test session metadata

**Migration strategy**: Not needed for most use cases

pytest-metadata primarily serves other plugins (like pytest-html). If you need metadata:

```python
# Store metadata in a fixture
from rustest import fixture
import platform
import sys

@fixture(scope="session")
def test_metadata():
    return {
        "Python": sys.version,
        "Platform": platform.platform(),
        "Packages": {
            # Add your package versions here
        }
    }

def test_something(test_metadata):
    # Use metadata in tests if needed
    print(f"Running on {test_metadata['Platform']}")
```

---

### 6. pytest-timeout (20.0M downloads/month)

**What it does**: Abort tests that run longer than a specified timeout

**Migration strategy**: Use Python's built-in `signal` module or a fixture

=== "With pytest-timeout"
    ```python
    import pytest

    @pytest.mark.timeout(5)  # 5 second timeout
    def test_slow_function():
        slow_operation()
    ```

=== "With rustest (Unix/Linux)"
    ```python
    # In conftest.py
    from rustest import fixture
    import signal
    from contextlib import contextmanager

    class TimeoutError(Exception):
        pass

    @contextmanager
    def timeout(seconds):
        def timeout_handler(signum, frame):
            raise TimeoutError(f"Test timed out after {seconds} seconds")

        # Set the signal handler
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

    # In test file
    def test_slow_function():
        with timeout(5):
            slow_operation()
    ```

=== "With rustest (Cross-platform)"
    ```python
    # In conftest.py
    from rustest import fixture
    import threading

    class TimeoutError(Exception):
        pass

    def timeout(seconds):
        def decorator(func):
            def wrapper(*args, **kwargs):
                result = [TimeoutError(f"Test timed out after {seconds}s")]

                def target():
                    try:
                        result[0] = func(*args, **kwargs)
                    except Exception as e:
                        result[0] = e

                thread = threading.Thread(target=target)
                thread.daemon = True
                thread.start()
                thread.join(seconds)

                if thread.is_alive():
                    raise TimeoutError(f"Test timed out after {seconds}s")
                if isinstance(result[0], Exception):
                    raise result[0]
                return result[0]
            return wrapper
        return decorator

    # In test file
    @timeout(5)
    def test_slow_function():
        slow_operation()
    ```

!!! warning "Platform differences"
    The `signal` module approach only works on Unix/Linux. For Windows compatibility, use the threading approach or a third-party library like `timeout-decorator`.

**Planned feature**: Built-in timeout support is planned for a future rustest release.

---

### 7. pytest-rerunfailures (19.6M downloads/month)

**What it does**: Re-run failed tests to detect flaky tests

**Migration strategy**: Not currently supported, use external retry logic

=== "With pytest-rerunfailures"
    ```bash
    pytest --reruns 3 --reruns-delay 1 tests/
    ```

=== "With rustest"
    ```bash
    # Option 1: Simple bash retry loop
    for i in {1..3}; do
        rustest tests/ && break
        echo "Retry $i failed, attempting again..."
        sleep 1
    done

    # Option 2: Use a retry script
    ./scripts/retry.sh 3 rustest tests/
    ```

**Test-level retries** (workaround with fixtures):

```python
from rustest import fixture
import functools

def retry(times=3, exceptions=(AssertionError,)):
    """Decorator to retry flaky tests"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(times):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == times - 1:
                        raise
                    print(f"Retry {attempt + 1}/{times} after failure: {e}")
        return wrapper
    return decorator

# Usage
@retry(times=3)
def test_flaky_api():
    result = unreliable_api_call()
    assert result.status == 200
```

!!! info "Planned feature"
    Test retry functionality is being considered for a future rustest release.

---

### 8. pytest-sugar (UI enhancement)

**What it does**: Prettier pytest output with progress bar

**Migration strategy**: Use rustest's output (already clean and fast)

rustest provides clean, fast output by default. While it doesn't have pytest-sugar's specific styling:

- ✅ Clear pass/fail indicators
- ✅ Real-time progress
- ✅ Detailed failure information
- ✅ Color-coded output
- ✅ Duration reporting

**If you miss pytest-sugar**: Consider that rustest's speed means you'll spend less time watching test output anyway!

---

### 9. pytest-django

**What it does**: Django integration and fixtures

**Migration strategy**: Not currently supported

For Django projects:

1. **Option 1**: Continue using pytest with pytest-django for now
2. **Option 2**: Use rustest for non-Django tests, pytest for Django-specific tests
3. **Option 3**: Write Django test setup manually using fixtures

**Basic Django test setup** (without pytest-django):

```python
# conftest.py
from rustest import fixture
import django
from django.conf import settings
from django.test.utils import setup_test_environment, teardown_test_environment

@fixture(scope="session", autouse=True)
def django_setup():
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            DATABASES={
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': ':memory:',
                }
            },
            INSTALLED_APPS=[
                'django.contrib.contenttypes',
                'django.contrib.auth',
                # Your apps here
            ],
        )
    django.setup()
    setup_test_environment()
    yield
    teardown_test_environment()

@fixture
def db():
    """Simple database fixture"""
    from django.core.management import call_command
    call_command('migrate', verbosity=0)
    yield
    # Cleanup handled by SQLite :memory:
```

!!! warning "Limited Django support"
    rustest does not have full Django integration. For Django projects with complex requirements, pytest-django is recommended.

---

### 10. pytest-benchmark

**What it does**: Benchmark testing with statistical analysis

**Migration strategy**: Use Python's `timeit` module or simple timing

=== "With pytest-benchmark"
    ```python
    def test_benchmark(benchmark):
        result = benchmark(expensive_function, arg1, arg2)
        assert result == expected
    ```

=== "With rustest"
    ```python
    import time

    def test_performance():
        start = time.perf_counter()
        result = expensive_function(arg1, arg2)
        duration = time.perf_counter() - start

        assert result == expected
        assert duration < 1.0  # Should complete in under 1 second

    # Or use timeit for more accurate results
    import timeit

    def test_with_timeit():
        duration = timeit.timeit(
            lambda: expensive_function(arg1, arg2),
            number=100
        )
        average = duration / 100
        assert average < 0.01  # Average under 10ms
    ```

**For more sophisticated benchmarking**, consider:

- **pytest-benchmark** with pytest (for detailed statistical analysis)
- **py-spy** or **pyinstrument** (for profiling)
- **asv** (Airspeed Velocity - for tracking performance over time)

---

## Plugin Categories Not Supported

Beyond the top 10, here are categories of plugins that rustest doesn't support:

### Framework Integration Plugins

- **pytest-django**: Use Django's test runner or pytest
- **pytest-flask**: Use Flask's test client directly
- **pytest-fastapi**: Use fastapi.testclient directly
- **pytest-tornado**: Use tornado testing utilities

**Recommendation**: For framework-specific testing, use the framework's built-in test utilities or pytest with the appropriate plugin.

### Advanced Test Manipulation

- **pytest-randomly**: Randomize test order (not supported)
- **pytest-repeat**: Repeat tests N times (use bash loop or test-level retry decorator)
- **pytest-ordering**: Control test execution order (not supported by design)

### Specialized Output Formats

- **pytest-html**: HTML reports (planned for rustest)
- **pytest-json-report**: JSON output (not planned)
- **pytest-junit**: JUnit XML (planned for rustest)

**Workaround**: Parse rustest's text output or wait for built-in support.

### IDE/Tool Integration

- **pytest-pycharm**: PyCharm integration (use IDE's test runner)
- **pytest-vscode**: VS Code integration (use test explorer)

Most IDEs can run rustest tests via Python's unittest discovery or by configuring rustest as a custom test runner.

---

## Hybrid Approach: Using Both pytest and rustest

For projects with complex pytest plugin dependencies, you can use both tools:

### Strategy 1: Split by Test Type

```bash
# Fast unit tests with rustest
rustest tests/unit/

# Integration tests requiring plugins with pytest
pytest --cov=myapp --django tests/integration/
```

### Strategy 2: Gradual Migration

```python
# conftest.py - Compatible with both
try:
    from rustest import fixture, parametrize, mark
    TEST_RUNNER = "rustest"
except ImportError:
    from pytest import fixture, mark
    parametrize = pytest.mark.parametrize
    TEST_RUNNER = "pytest"

# Use TEST_RUNNER to conditionally enable features
if TEST_RUNNER == "pytest":
    pytest_plugins = ["pytest_django", "pytest_cov"]
```

### Strategy 3: Development vs CI

```yaml
# .github/workflows/test.yml
jobs:
  fast-tests:
    name: Fast unit tests (rustest)
    steps:
      - run: rustest tests/unit/

  full-tests:
    name: Full test suite (pytest)
    steps:
      - run: pytest --cov --django tests/
```

**Use rustest for fast feedback during development, pytest for comprehensive CI testing.**

---

## Creating Your Own Solutions

For plugins not covered above, you can often replicate functionality with fixtures:

### Template: Creating a Plugin Replacement

```python
# conftest.py
from rustest import fixture

@fixture
def my_custom_fixture():
    """Replace plugin functionality with a fixture"""
    # Setup
    resource = setup_resource()

    # Provide to test
    yield resource

    # Teardown
    cleanup_resource(resource)

# Usage in tests
def test_something(my_custom_fixture):
    result = my_custom_fixture.do_something()
    assert result == expected
```

### Sharing Fixtures Across Projects

Create a shared conftest.py or package:

```python
# my_test_utils/conftest.py
from rustest import fixture

@fixture
def common_fixture():
    return setup_common_resource()

# Install as package
# pip install -e ./my_test_utils

# Import in your project's conftest.py
from my_test_utils.conftest import common_fixture
```

---

## Decision Tree: Should You Use rustest?

```
Do you use pytest plugins?
├─ No → ✅ Use rustest! Easy migration, huge speedup
└─ Yes → Which plugins?
    ├─ Only top 5 (cov, xdist, asyncio, mock, timeout)
    │   └─ ✅ Use rustest with built-in alternatives
    ├─ Framework plugins (django, flask, etc.)
    │   └─ ⚠️  Use pytest or hybrid approach
    ├─ Custom conftest.py hooks
    │   └─ ⚠️  Evaluate complexity, may need pytest
    └─ Many niche plugins
        └─ ❌ Stick with pytest for now
```

## Future Plans

While full plugin support is not planned, rustest aims to provide built-in alternatives for the most popular plugin use cases:

**Planned features**:

- 🚧 **Coverage integration**: Built-in coverage reporting
- 🚧 **Parallel control**: CLI options for worker count (`-j`, `--workers`)
- 🚧 **Timeout support**: Built-in test timeouts with `@mark.timeout(seconds)`
- 🚧 **HTML reports**: Generate HTML test reports
- 🚧 **JUnit XML**: JUnit-compatible XML output
- 🚧 **Retry logic**: Built-in test retry for flaky tests

**Not planned**:

- ❌ Full plugin system (hooks, pluggy integration)
- ❌ Custom collectors
- ❌ Advanced plugin hooks

---

## Getting Help

If you're migrating from pytest and encounter issues:

1. **Check this guide** for your specific plugin
2. **Search the docs** at https://apex-engineers-inc.github.io/rustest
3. **Open an issue** at https://github.com/Apex-Engineers-Inc/rustest/issues
4. **Ask questions** in GitHub Discussions

Include:

- Which pytest plugins you're using
- Your current test setup
- What you've tried
- Specific error messages

The rustest community is here to help you migrate successfully!

---

## Conclusion

While rustest doesn't support pytest plugins, it provides:

✅ **Built-in alternatives** for the most popular plugins
✅ **Excellent pytest API compatibility** for standard features
✅ **8.5× average speedup** (up to 19×) for faster development
✅ **Simple architecture** without plugin complexity

**For 90% of Python projects**, the speed gains and simplicity of rustest far outweigh the lack of plugin support.

**For plugin-heavy projects**, pytest remains an excellent choice, or consider a hybrid approach during migration.

---

## See Also

- [Comparison with pytest](comparison.md) - Feature-by-feature comparison
- [Migration Guide](../migration-guide.md) - General pytest to rustest migration
- [Performance](performance.md) - Detailed performance benchmarks
- [Fixtures](../guide/fixtures.md) - rustest fixture documentation
