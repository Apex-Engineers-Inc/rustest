# Pytest Plugins and rustest

!!! warning "Pytest plugins are not supported"
    rustest **does not support pytest plugins** and this is an intentional design decision. This page explains why and provides concrete migration strategies for the most popular pytest plugins.

Several of the plugins below have no migration to do, because rustest ports their behaviour
into the engine. `--cov`, the worker pool, the `mocker` fixture and async support are all
built in.

## Important Distinction: Fixture Modules vs Plugins

Two different things share the name "plugin", and only one of them works.

### Fixture Modules (SUPPORTED)

A `pytest_plugins` declaration names Python modules whose `@fixture` functions get registered
globally. rustest honours it in a `conftest.py` and in a test module, the same two places
pytest reads it:

<!--rustest.mark.skip-->
```python
# conftest.py -- a list of module names
pytest_plugins = ["fixtures.database", "fixtures.api"]

# ...or a single module, as a bare string
pytest_plugins = "my_fixtures"
```

The named module is imported and its fixtures are registered with an empty base id, so they
are visible to the whole run rather than only below the conftest that named them. That is all
that happens: it is a module import, not a plugin registration. Hooks defined in the named
module are not called.

See [Loading fixtures from external modules](fixtures.md#loading-fixtures-from-external-modules)
for details.

### Pytest Plugins (NOT SUPPORTED)

rustest does not implement any of the machinery a real pytest plugin needs:

- the pluggy hook system (`pytest_configure`, `pytest_collection_modifyitems`, and the rest)
- setuptools entry points (`pytest11`)
- plugin packages from PyPI, except where the behaviour has been reimplemented in the engine
- hook wrappers and hook ordering (`tryfirst`, `trylast`)

A conftest that defines `pytest_collection_modifyitems` is silently inert rather than an error.
`pytest_generate_tests` is not called either; decorator metadata and fixture `params=` are the
only sources of parametrization.

This page covers plugins from PyPI. For the `pytest_plugins` fixture-module mechanism, see
above.

## Why rustest Doesn't Support Plugins

### The Technical Reasons

pytest's plugin system is built on pluggy. `_pytest/hookspec.py` in pytest 8.4.2 declares 52
hooks, covering initialization, collection, execution, reporting and fixtures. Implementing
them in rustest would mean:

**1. Architectural Mismatch**

The Rust engine owns test discovery, execution and reporting. Every hook call would cross the
Rust/Python FFI boundary, several times per file during collection and several times per test
during execution, which for a thousand tests is thousands of crossings that do not exist today.

The margin being defended is not enormous. rustest ran seventeen real pytest suites between
[1.1x and 5.7x](performance.md) faster, and what a runner can win at all is bounded by how much
of a suite is framework rather than test body. Re-entering Python on every hook, for every
item, is exactly the cost that margin is made of.

**2. Implementation Complexity**

Full plugin support would require:

- Integrating the `pluggy` library into rustest
- Implementing the hook specifications
- Exposing Rust internal state (Config, Session, Items, Reports) to Python
- Bidirectional state synchronization across the FFI boundary
- Hook execution ordering (tryfirst, trylast, wrappers)
- Dynamic argument injection and pruning

**3. Maintenance Burden**

pytest's hook API changes between versions, so a compatibility matrix would have to be tracked
and tested. Plugins interact with each other in ways that are hard to reproduce, and some reach
into private pytest APIs that have no stable equivalent to port.

### The Philosophical Reasons

rustest implements the parts of pytest that most suites actually use, and leaves the rest to
pytest. The goal is a fast runner with a faithful core, not a pytest clone.

What that buys: a codebase without plugin infrastructure, and a collection and execution path
that stays in Rust. What it costs: niche features, and any plugin whose behaviour has not been
ported.

### What About Migration?

Most suites need no plugins to migrate. rustest already provides:

- Full fixture support (all scopes, teardown, dependency injection)
- Parametrization with custom IDs
- Marks and filtering
- Exception testing (raises, match patterns)
- Async testing, with no marker required by default
- Warning capture (warns, deprecated_call)
- These built-in fixtures: `tmp_path`, `tmp_path_factory`, `tmpdir`, `tmpdir_factory`,
  `monkeypatch`, `capsys`, `capfd`, `caplog`, `cache`, `mocker`, `pytestconfig`, `recwarn`

Requesting one of pytest's remaining built-in fixtures is an error naming the gap rather than a
"fixture not found": `capsysbinary`, `capfdbinary`, `capteesys`, `doctest_namespace`,
`pytester`, `testdir`, `record_property`, `record_testsuite_property` and
`record_xml_attribute`.

Cosmetic pytest flags are accepted and ignored rather than rejected, so an `addopts` line that
carries them still runs. Each one prints a line on stderr naming what was dropped: `--tb`,
`--durations`, `--durations-min`, `--import-mode`, `--strict`, `--strict-markers`,
`--strict-config`, `-p`, `--showlocals`, `-l`, `--full-trace` and the `-r` report characters.
Anything not on that list is still an error, because a flag that changes what runs must never
be silently ignored.

---

## Top 10 Pytest Plugins: Migration Guide

Download figures are from October 2025.

### 1. pytest-cov (87.7M downloads/month)

**What it does**: Code coverage reporting

**Migration strategy**: rustest has its own `--cov`, and it writes coverage.py's own data
format. See [Coverage](coverage.md) for the whole surface.

=== "With pytest-cov"
    ```bash
    pytest --cov=myproject --cov-report=html --cov-report=term tests/
    ```

=== "With rustest"
    ```bash
    pip install 'rustest[cov]'

    # `term` and `xml` are built in
    rustest --cov=myproject --cov-report=term tests/

    # ...and every other report is one `coverage` command away, because the run wrote
    # an ordinary `.coverage` file
    rustest --cov=myproject tests/
    coverage html
    ```

    Branch coverage is not implemented yet. `--cov-branch` is refused rather than
    silently downgraded to lines. For branches, run rustest under coverage.py instead:

    ```bash
    coverage run --branch --source=myproject -m rustest tests/
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

---

### 2. pytest-xdist (60.3M downloads/month)

**What it does**: Parallel and distributed test execution

**Migration strategy**: built in. rustest runs a worker pool by default.

**With pytest-xdist:**

```bash
pytest -n 4 tests/              # Run on 4 CPUs
pytest -n auto tests/           # Auto-detect CPU count
pytest --dist=loadscope tests/  # Distribute by module
```

**With rustest:**

```bash
rustest tests/          # Already parallel: 4 workers by default
rustest -n 8 tests/     # Pick the pool size
rustest -n 1 tests/     # Force sequential
```

`-n` / `--workers` sets the pool size, defaulting to 4 and capped by the CPU count.

Two differences from pytest-xdist worth knowing:

- **Distribution is at file granularity**, comparable to xdist's `--dist=loadscope`. A
  suite whose tests are concentrated in one enormous file cannot be parallelised past that
  file, so splitting it is a real speedup.
- **There is no `--dist` choice and no `-n auto`.** The default is already CPU-aware, and
  the other distribution strategies are not implemented. `-n` takes an integer, so `-n auto`
  is a usage error.

Session- and package-scoped fixtures are instantiated once per worker process, which is
pytest-xdist's contract for them too.

!!! warning "Shared external state"
    Parallel workers are separate processes. A suite that shares a database, a port, or a
    filesystem path across tests needs `-n 1`, exactly as it would need `--dist=loadfile`
    or a lock under xdist. rustest has no `xdist_group` equivalent yet.

---

### 3. pytest-asyncio (58.9M downloads/month)

**What it does**: Support for testing asyncio code

**Migration strategy**: built in. rustest ports pytest-asyncio's model directly.

Loop scopes, the three `asyncio_*` ini options, `@mark.asyncio(loop_scope=...)`, async
fixtures and the `event_loop_policy` fixture all work. `import pytest_asyncio` resolves to
rustest's compatibility module, so `@pytest_asyncio.fixture` keeps working unchanged.

=== "With pytest-asyncio"
    ```python
    import asyncio
    import pytest

    async def some_async_operation():
        await asyncio.sleep(0)
        return "expected"

    expected = "expected"

    @pytest.mark.asyncio
    async def test_async_function():
        result = await some_async_operation()
        assert result == expected
    ```

=== "With rustest"
    ```python
    import asyncio
    from rustest import mark

    async def some_async_operation():
        await asyncio.sleep(0)
        return "expected"

    expected = "expected"

    @mark.asyncio
    async def test_async_function():
        result = await some_async_operation()
        assert result == expected
    ```

rustest's default `asyncio_mode` is `auto`, where pytest-asyncio's is `strict`, so the marker
is optional:

```python
import asyncio

async def some_async_operation():
    await asyncio.sleep(0)
    return "ok"

async def test_no_marker_needed():
    result = await some_async_operation()
    assert result == "ok"
```

**Advanced features**:

```python
import asyncio
from rustest import mark

async def process(value):
    await asyncio.sleep(0)
    return value

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

rustest adds a `timeout` keyword that pytest-asyncio has no equivalent for. See
[Async testing](async-testing.md) for that and for the loop-scope rules.

**Differences from pytest-asyncio**:

- The `event_loop` fixture is not available. It was removed from pytest-asyncio itself in 1.0;
  override `event_loop_policy` instead.
- `asyncio_debug` is not read, so loops always run with debug mode off.
- Tests sharing a loop scope run one at a time, as they do under pytest-asyncio. Parallelism
  comes from the worker pool distributing files.
- A session-scoped loop lives once per worker process, not once per run.

**Async fixtures**:

```python
import asyncio
from rustest import fixture, mark

class Database:
    async def query(self, sql):
        await asyncio.sleep(0)
        return 1
    async def close(self):
        await asyncio.sleep(0)

async def setup_database():
    await asyncio.sleep(0)
    return Database()

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

**Migration strategy**: built in. `mocker` is a rustest fixture, ported from pytest-mock
3.15.1. Existing tests need no changes.

```python
class Service:
    def fetch(self):
        return "real"

def test_mocker_patches(mocker):
    mocker.patch.object(Service, "fetch", return_value="mocked")
    assert Service().fetch() == "mocked"

def test_patch_is_undone_afterwards(mocker):
    assert Service().fetch() == "real"
```

The string-target form works the same way, so a suite written against pytest-mock runs
unchanged:

<!--rustest.mark.skip-->
```python
def test_function(mocker):
    mock_obj = mocker.patch('mypackage.mymodule.ClassName')
    mock_obj.return_value = 42
    assert mypackage.mymodule.ClassName() == 42
```

The fixture carries `patch` (with `.object`, `.multiple` and `.dict`), `spy`, `stub`,
`async_stub`, `create_autospec`, `resetall`, `stop`, `stopall`, and the usual aliases:
`Mock`, `MagicMock`, `AsyncMock`, `PropertyMock`, `NonCallableMock`, `NonCallableMagicMock`,
`call`, `ANY`, `DEFAULT`, `sentinel`, `mock_open` and `seal`. Patches are undone in reverse
registration order after every test, so two patches of the same attribute nest correctly.

<!--rustest.mark.skip-->
```python
def test_spy(mocker):
    obj = MyClass()
    spy = mocker.spy(obj, 'method')
    obj.method(42)
    spy.assert_called_once_with(42)

def test_patch_object(mocker):
    mocker.patch.object(MyClass, 'method', return_value='patched')
    assert MyClass().method() == 'patched'
```

Four pieces of pytest-mock are not ported:

- `mocker.patch.context_manager`, whose only difference from `patch.object` is suppressing a
  warning, and rustest has no warnings channel to suppress on
- `class_mocker`, `module_mocker`, `package_mocker` and `session_mocker`, the same fixture at
  wider scopes
- the `mock_use_standalone_module` ini option; the module is always `unittest.mock`
- the `assert_called_with` failure-message introspection, which pytest-mock installs by
  monkey-patching `unittest.mock` process-wide

`unittest.mock` also works directly, with no fixture involved:

```python
from unittest.mock import patch

class Gateway:
    def send(self):
        return "real"

def test_function_without_mocker():
    with patch.object(Gateway, "send", return_value="mocked"):
        assert Gateway().send() == "mocked"
    assert Gateway().send() == "real"
```

---

### 5. pytest-metadata (20.7M downloads/month)

**What it does**: Access to test session metadata

**Migration strategy**: Not supported. pytest-metadata mostly exists to feed other plugins such
as pytest-html, so there is usually nothing to replace. If you need the values, a
session-scoped fixture holds them:

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

**Migration strategy**: async tests have `@mark.asyncio(timeout=...)`. Synchronous tests have
no built-in timeout; use the `signal` module or a thread.

For an async test, the timeout is applied inside the loop with `asyncio.wait_for`, so an
overrunning test is cancelled:

```python
import asyncio
from rustest import mark

async def slow_operation():
    await asyncio.sleep(0.01)

@mark.asyncio(timeout=5.0)
async def test_slow_async_operation():
    await slow_operation()
```

For a synchronous test:

=== "With pytest-timeout"
    ```python
    import pytest

    def slow_operation():
        return "done"

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

    def slow_operation():
        return "done"

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

    def slow_operation():
        return "done"

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

`@mark.timeout(...)` is accepted as an ordinary mark, so it can be selected with `-m`, but it
has no effect on how long a test may run.

---

### 7. pytest-rerunfailures (19.6M downloads/month)

**What it does**: Re-run failed tests to detect flaky tests

**Migration strategy**: Not supported. Retry outside the runner, or inside the test.

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

    # Option 2: rerun only what failed, using rustest's own cache
    rustest tests/ || rustest --lf tests/
    ```

`--lf` / `--last-failed` and `--ff` / `--failed-first` are built in, which covers the common
case of re-running only what broke without re-running the whole suite.

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
class Response:
    status = 200

def unreliable_api_call():
    return Response()

@retry(times=3)
def test_flaky_api():
    result = unreliable_api_call()
    assert result.status == 200
```

---

### 8. pytest-sugar (UI enhancement)

**What it does**: Prettier pytest output with progress bar

**Migration strategy**: Not supported. rustest has its own output, which does not match
pytest-sugar's styling but does provide pass/fail markers, per-test progress percentages,
colour, and failure detail with filtered tracebacks.

Colour is controlled by `--color` and `--ascii`; `-v` and `-q` set verbosity, and `--tb` is
accepted and ignored because traceback style is rustest's own.

---

### 9. pytest-django

**What it does**: Django integration and fixtures

**Migration strategy**: Not supported. rustest has no Django-specific code at all.

For Django projects:

1. **Option 1**: Continue using pytest with pytest-django for now
2. **Option 2**: Use rustest for non-Django tests, pytest for Django-specific tests
3. **Option 3**: Write Django test setup manually using fixtures

**Basic Django test setup** (without pytest-django):

<!--rustest.mark.skip-->
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

**Migration strategy**: Not supported. There is no `benchmark` fixture. Use `timeit` or time
the operation yourself.

=== "With pytest-benchmark"
    <!--rustest.mark.skip-->
    ```python
    def expensive_function(a, b):
        return a + b

    arg1, arg2 = 1, 2
    expected = 3

    def test_benchmark(benchmark):
        result = benchmark(expensive_function, arg1, arg2)
        assert result == expected
    ```

=== "With rustest"
    ```python
    import time

    def expensive_function(a, b):
        return a + b

    arg1, arg2 = 1, 2
    expected = 3

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

Neither approach gives you the statistical analysis pytest-benchmark does. For that, run those
benchmarks under pytest with the plugin, or use `py-spy` / `pyinstrument` for profiling and
`asv` for tracking performance over time.

---

## Plugin Categories Not Supported

Beyond the top 10, here are categories of plugins that rustest doesn't support:

### Framework Integration Plugins

- **pytest-django**: Use Django's test runner or pytest
- **pytest-flask**: Use Flask's test client directly
- **pytest-fastapi**: Use fastapi.testclient directly
- **pytest-tornado**: Use tornado testing utilities

None of these frameworks have rustest-specific handling. Their own test utilities work inside
ordinary rustest fixtures.

### Async Frameworks Other Than asyncio

- **anyio**, **pytest-trio**, **pytest-tornasync**, **pytest-twisted**: not supported

rustest's async support is asyncio only. In `asyncio_mode = "strict"`, an unmarked `async def`
test fails with pytest's own message, which lists these plugins as things you might install
under pytest; that is a reproduction of pytest's wording, not a statement that they work here.

### Advanced Test Manipulation

- **pytest-randomly**: Randomize test order (not supported)
- **pytest-repeat**: Repeat tests N times (use bash loop or test-level retry decorator)
- **pytest-ordering**: Control test execution order (not supported by design)

`-p no:randomly` and similar `-p` arguments are accepted and ignored, so an `addopts` line
carrying one does not break the run.

### Specialized Output Formats

- **pytest-html**: HTML reports (not implemented)
- **pytest-json-report**: use `--report-json PATH`, which writes a schema-2 JSON report of the
  run, or `--llm` for JSONL aimed at LLM tooling
- **pytest-junit**: JUnit XML (not implemented). The `record_property`,
  `record_testsuite_property` and `record_xml_attribute` fixtures are unavailable for the same
  reason.

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
# (pytest-django activates on install; point it at your settings with --ds or
# DJANGO_SETTINGS_MODULE)
pytest --cov=myapp tests/integration/
```

### Strategy 2: Gradual Migration

```python
# conftest.py - Compatible with both
try:
    from rustest import fixture, parametrize, mark
    TEST_RUNNER = "rustest"
except ImportError:
    import pytest
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
      - run: pytest --cov=myapp tests/
```

rustest gives faster feedback during development; pytest with its plugins covers whatever
rustest does not implement.

---

## Creating Your Own Solutions

For plugins not covered above, you can often replicate functionality with fixtures:

### Template: Creating a Plugin Replacement

```python
# conftest.py
from rustest import fixture

class Resource:
    def do_something(self):
        return "result"

expected = "result"

def setup_resource():
    return Resource()

def cleanup_resource(resource):
    pass

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

Package the fixtures and name the module in `pytest_plugins`, or import them into a conftest:

<!--rustest.mark.skip-->
```python
# my_test_utils/fixtures.py
from rustest import fixture

@fixture
def common_fixture():
    return setup_common_resource()

# In your project's conftest.py, after `pip install -e ./my_test_utils`
pytest_plugins = "my_test_utils.fixtures"
```

---

## Decision Tree: Should You Use rustest?

```
Do you use pytest plugins?
├─ No → Use rustest. Easy migration.
└─ Yes → Which plugins?
    ├─ cov, xdist, asyncio, mock
    │   └─ Use rustest; all four are built in
    ├─ Framework plugins (django, flask, etc.)
    │   └─ Use pytest or a hybrid approach
    ├─ Custom conftest.py hooks
    │   └─ Hooks do not run; evaluate what depends on them
    └─ Many niche plugins
        └─ Stick with pytest for now
```

## Future Plans

Full plugin support is not planned. Built-in equivalents for common plugin use cases are.

**Shipped**:

- Coverage: `--cov`, `--cov-report` (needs the `cov` extra)
- Parallel execution: `-n` / `--workers`
- Mocking: the `mocker` fixture
- Async: `@mark.asyncio`, loop scopes, async fixtures, the `pytest_asyncio` shim
- JSON reporting: `--report-json`, `--llm`

**Planned**:

- Timeouts for synchronous tests
- Branch coverage, once PEP 669's branch events are available across supported versions
- HTML reports
- JUnit XML

**Not planned**:

- Full plugin system (hooks, pluggy integration)
- Custom collectors
- Advanced plugin hooks

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

---

## Conclusion

rustest does not run pytest plugins. It does implement the behaviour of the four most-installed
ones: coverage, parallel execution, mocking and asyncio support are all part of the engine, and
a suite using only those needs no migration work beyond changing the command.

What is left unimplemented is real: Django and other framework integrations, benchmarking,
HTML and JUnit reporting, test reordering and reruns, and any conftest that relies on pytest
hooks. For those, pytest remains the right tool, either outright or alongside rustest during a
migration.

---

## See Also

- [Comparison with pytest](comparison.md) - Feature-by-feature comparison
- [Migration Guide](migration-guide.md) - General pytest to rustest migration
- [Performance](performance.md) - Detailed performance benchmarks
- [Fixtures](intro-fixtures.md) - rustest fixture documentation
- [Async testing](async-testing.md) - The asyncio surface in full
- [Coverage](coverage.md) - `--cov` and coverage.py integration
