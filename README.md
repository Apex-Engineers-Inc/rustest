# rustest

Rustest (pronounced like Russ-Test) is a Rust-powered test runner that aims to provide the most common pytest ergonomics with a focus on raw performance. Get **~2x faster** test execution with familiar syntax and minimal setup.

## Why rustest?

- 🚀 **About 2x faster** than pytest on the rustest integration test suite
- ✅ Familiar `@fixture`, `@parametrize`, `@skip`, and `@mark` decorators
- 🔍 Automatic test discovery (`test_*.py` and `*_test.py` files)
- 📝 **Built-in markdown code block testing** (like pytest-codeblocks, but faster)
- 🎯 Simple, clean API—if you know pytest, you already know rustest
- 🧮 Built-in `approx()` helper for tolerant numeric comparisons across scalars, collections, and complex numbers
- 🪤 `raises()` context manager for precise exception assertions with optional message matching
- 📦 Easy installation with pip or uv
- ⚡ Low-overhead execution keeps small suites feeling instant

## Performance

Rustest is designed for speed. Our latest benchmarks on the rustest integration suite (~200 tests) show a consistent **2.1x wall-clock speedup** over pytest:

| Test Runner | Reported Runtime† | Wall Clock‡ | Speedup (wall) | Command |
|-------------|------------------|-------------|----------------|---------|
| pytest      | 0.43–0.59s       | 1.33–1.59s  | 1.0x (baseline) | `pytest tests/ examples/tests/ -q`
| rustest     | 0.003s           | 0.69–0.70s  | **~2.1x faster** | `python -m rustest tests/ examples/tests/`§

### Large parametrized stress test

We also profiled an extreme case with **10,000 parametrized invocations** to ensure rustest scales on synthetic but heavy workloads. The test lives in [`benchmarks/test_large_parametrize.py`](benchmarks/test_large_parametrize.py) and simply asserts `value + value == 2 * value` across every case. Running the module on its own shows a dramatic gap:

| Test Runner | Avg. Wall Clock (3 runs) | Speedup | Command |
|-------------|--------------------------|---------|---------|
| pytest      | 9.72s                    | 1.0x    | `pytest benchmarks/test_large_parametrize.py -q`§
| rustest     | 0.41s                    | **~24x** | `python -m rustest benchmarks/test_large_parametrize.py`§

† pytest and rustest both report only active test execution time; rustest's figure omits Python interpreter start-up overhead.

‡ Integration-suite wall-clock timing measured with the shell `time` builtin across two consecutive runs in the same environment.

§ Commands executed with `PYTHONPATH=python` in this repository checkout to exercise the local sources. Pytest relies on a small compatibility shim in [`benchmarks/conftest.py`](benchmarks/conftest.py) so it understands the rustest-style decorators. Large-parametrization timings come from averaging three `time.perf_counter()` measurements with output suppressed via `subprocess.DEVNULL`.

Rustest counts parametrized cases slightly differently than pytest, so you will see 199 executed cases vs. pytest's 201 discoveries on the same suite—the reported pass/skip counts still align.

**Why is rustest faster?**
- **Near-zero startup time**: Native Rust binary minimizes overhead before Python code starts running.
- **Rust-native test discovery**: Minimal imports until test execution keeps collection quick.
- **Optimized fixture resolution**: Efficient dependency graph resolution reduces per-test work.
- **Lean orchestration**: Rust handles scheduling and reporting so the Python interpreter focuses on running test bodies.

**Real-world impact:**
- **200 tests** (this repository): 1.46s → 0.70s (average wall-clock, ~0.76s saved per run)
- **1,000 tests** (projected): ~7.3s → ~3.4s assuming similar scaling
- **10,000 tests** (projected): ~73s → ~34s—minutes saved across CI runs

See [BENCHMARKS.md](BENCHMARKS.md) for detailed performance analysis and methodology.

## Installation

Rustest supports Python **3.10 through 3.14**.

### Using pip
```bash
pip install rustest
```

### Using uv
```bash
uv add rustest
```

### For Development
If you want to contribute to rustest, see [DEVELOPMENT.md](DEVELOPMENT.md) for setup instructions.

## Testing Markdown Code Blocks

Rustest can automatically discover and test Python code blocks in your markdown files, similar to pytest-codeblocks. This is perfect for ensuring your documentation examples stay up-to-date and functional.

### Enabling Code Block Tests

By default, rustest will automatically discover and test Python code blocks in markdown files (`.md`). Each Python code block is treated as a separate test case.

```bash
# Run tests including markdown code blocks
rustest

# Disable code block tests
rustest --no-codeblocks
```

### Example Markdown File

```markdown
# Example Documentation

## Basic Addition

\```python
x = 1 + 1
assert x == 2
\```

## String Operations

\```python
text = "hello world"
assert text.startswith("hello")
\```
```

Each Python code block will be executed as a test. Code blocks with other language tags (like `javascript`, `bash`, etc.) are ignored.

### Features

- **Automatic Discovery**: All `.md` files are scanned for Python code blocks
- **Simple Testing**: Each `\```python` code block is executed as a test
- **CLI Control**: Use `--no-codeblocks` to disable code block testing
- **Fast Execution**: Rust-powered parsing and execution keeps tests fast

## Quick Start

### 1. Write Your Tests

Create a file `test_math.py`:

```python
from rustest import fixture, parametrize, mark, approx, raises

@fixture
def numbers() -> list[int]:
    return [1, 2, 3, 4, 5]

def test_sum(numbers: list[int]) -> None:
    assert sum(numbers) == approx(15)

@parametrize("value,expected", [(2, 4), (3, 9), (4, 16)])
def test_square(value: int, expected: int) -> None:
    assert value ** 2 == expected

@mark.slow
def test_expensive_operation() -> None:
    # This test is marked as slow for filtering
    result = sum(range(1000000))
    assert result > 0

def test_division_by_zero_is_reported() -> None:
    with raises(ZeroDivisionError, match="division by zero"):
        1 / 0
```

### 2. Run Your Tests

```bash
# Run all tests in the current directory
rustest

# Run tests in a specific directory
rustest tests/

# Run tests matching a pattern
rustest -k "test_sum"

# Show output during test execution
rustest --no-capture
```

## Usage Examples

### CLI Usage

```bash
# Run all tests in current directory (including markdown code blocks)
rustest

# Run tests in specific paths
rustest tests/ integration/

# Filter tests by name pattern
rustest -k "user"           # Runs test_user_login, test_user_signup, etc.
rustest -k "auth"           # Runs all tests with "auth" in the name

# Control output capture
rustest --no-capture        # See print statements during test execution

# Disable markdown code block tests
rustest --no-codeblocks     # Only run Python test files, skip .md files
```

### Python API Usage

You can also run rustest programmatically from Python:

```python
from rustest import run
import os

# Basic usage (specify your test directory)
if os.path.exists("tests"):
    report = run(paths=["tests"])
    print(f"Passed: {report.passed}, Failed: {report.failed}")

    # With pattern filtering
    report = run(paths=["tests"], pattern="user")

    # Without output capture (see print statements)
    report = run(paths=["tests"], capture_output=False)

    # Disable markdown code block tests
    report = run(paths=["tests"], enable_codeblocks=False)

    # Access individual test results
    for result in report.results:
        print(f"{result.name}: {result.status} ({result.duration:.3f}s)")
        if result.status == "failed":
            print(f"  Error: {result.message}")
```

### Writing Tests

#### Basic Test Functions

```python
def test_simple_assertion() -> None:
    assert 1 + 1 == 2

def test_string_operations() -> None:
    text = "hello world"
    assert text.startswith("hello")
    assert "world" in text
```

#### Using Fixtures

Fixtures provide reusable test data and setup:

```python
from rustest import fixture

@fixture
def database_connection() -> dict:
    # Setup: create a connection
    conn = {"host": "localhost", "port": 5432}
    return conn
    # Teardown happens automatically

@fixture
def sample_user() -> dict:
    return {"id": 1, "name": "Alice", "email": "alice@example.com"}

def test_database_query(database_connection: dict) -> None:
    assert database_connection["host"] == "localhost"

def test_user_email(sample_user: dict) -> None:
    assert "@" in sample_user["email"]
```

#### Fixtures with Dependencies

Fixtures can depend on other fixtures:

```python
from rustest import fixture

@fixture
def api_url() -> str:
    return "https://api.example.com"

@fixture
def api_client(api_url: str) -> dict:
    return {"base_url": api_url, "timeout": 30}

def test_api_configuration(api_client: dict) -> None:
    assert api_client["base_url"].startswith("https://")
    assert api_client["timeout"] == 30
```

#### Assertion Helpers

Rustest ships helpers for expressive assertions:

```python
from rustest import approx, raises

def test_nearly_equal() -> None:
    assert 0.1 + 0.2 == approx(0.3, rel=1e-9)

def test_raises_with_message() -> None:
    with raises(ValueError, match="invalid configuration"):
        raise ValueError("invalid configuration")
```

#### Yield Fixtures with Setup/Teardown

Fixtures can use `yield` to perform cleanup after tests:

```python
from rustest import fixture

@fixture
def database_connection():
    # Setup: create connection
    conn = create_db_connection()
    print("Database connected")

    yield conn

    # Teardown: close connection
    conn.close()
    print("Database connection closed")

@fixture
def temp_file():
    # Setup
    file = open("temp.txt", "w")
    file.write("test data")

    yield file

    # Teardown
    file.close()
    os.remove("temp.txt")

def test_database_query(database_connection):
    result = database_connection.query("SELECT 1")
    assert result is not None
```

#### Fixture Scopes

Fixtures support different scopes to control when they are created and destroyed:

```python
from rustest import fixture

@fixture  # Default: function scope - new instance per test
def function_fixture() -> dict:
    return {"value": "reset each test"}

@fixture(scope="class")  # Shared across all tests in a class
def class_database() -> dict:
    return {"connection": "db://test", "shared": True}

@fixture(scope="module")  # Shared across all tests in a module
def module_config() -> dict:
    return {"env": "test", "timeout": 30}

@fixture(scope="session")  # Shared across entire test session
def session_cache() -> dict:
    return {"global_cache": {}}

# Fixtures can depend on fixtures with different scopes
@fixture(scope="function")
def request_handler(module_config: dict, session_cache: dict) -> dict:
    return {
        "config": module_config,  # module-scoped
        "cache": session_cache,   # session-scoped
        "request_id": id(object())  # unique per test
    }
```

**Scope Behavior:**
- `function` (default): New instance for each test function
- `class`: Shared across all test methods in a test class
- `module`: Shared across all tests in a Python module
- `session`: Shared across the entire test session

Scoped fixtures are especially useful for expensive setup operations like database connections, API clients, or configuration loading.

**Using conftest.py for Shared Fixtures:**

You can define fixtures in a `conftest.py` file to share them across multiple test files:

```python
# conftest.py
from rustest import fixture

@fixture(scope="session")
def database():
    """Shared database connection for all tests."""
    db = setup_database()
    yield db
    db.cleanup()

@fixture(scope="module")
def api_client():
    """API client shared across a module."""
    return create_api_client()
```

All test files in the same directory (and subdirectories) can use these fixtures automatically.

#### Parametrized Tests

Run the same test with different inputs:

```python
from rustest import parametrize

@parametrize("input,expected", [
    (1, 2),
    (2, 4),
    (3, 6),
])
def test_double(input: int, expected: int) -> None:
    assert input * 2 == expected

# With custom test IDs for better output
@parametrize("value,expected", [
    (2, 4),
    (3, 9),
    (4, 16),
], ids=["two", "three", "four"])
def test_square(value: int, expected: int) -> None:
    assert value ** 2 == expected
```

#### Combining Fixtures and Parameters

```python
from rustest import fixture, parametrize

@fixture
def multiplier() -> int:
    return 10

@parametrize("value,expected", [
    (1, 10),
    (2, 20),
    (3, 30),
])
def test_multiply(multiplier: int, value: int, expected: int) -> None:
    assert multiplier * value == expected
```

#### Skipping Tests

```python
from rustest import skip, mark

@skip("Not implemented yet")
def test_future_feature() -> None:
    assert False

@mark.skip(reason="Waiting for API update")
def test_deprecated_api() -> None:
    assert False
```

#### Using Marks to Organize Tests

```python
from rustest import mark

@mark.unit
def test_calculation() -> None:
    assert 2 + 2 == 4

@mark.integration
def test_database_integration() -> None:
    # Integration test
    pass

@mark.slow
@mark.integration
def test_full_workflow() -> None:
    # This test has multiple marks
    pass
```

#### Test Classes

Rustest supports pytest-style test classes, allowing you to organize related tests together:

```python
from rustest import fixture, parametrize, mark

class TestBasicMath:
    """Group related tests in a class."""

    def test_addition(self):
        assert 1 + 1 == 2

    def test_subtraction(self):
        assert 5 - 3 == 2

    def test_multiplication(self):
        assert 3 * 4 == 12
```

**Using Fixtures in Test Classes:**

Test methods can inject fixtures just like standalone test functions:

```python
from rustest import fixture

@fixture
def calculator():
    return {"add": lambda x, y: x + y, "multiply": lambda x, y: x * y}

class TestCalculator:
    """Test class using fixtures."""

    def test_addition(self, calculator):
        assert calculator["add"](2, 3) == 5

    def test_multiplication(self, calculator):
        assert calculator["multiply"](4, 5) == 20
```

**Class-Scoped Fixtures:**

Class-scoped fixtures are shared across all test methods in the same class, perfect for expensive setup operations:

```python
from rustest import fixture

@fixture(scope="class")
def database():
    """Expensive setup shared across all tests in a class."""
    db = {"connection": "db://test", "data": []}
    return db

class TestDatabase:
    """All tests share the same database fixture instance."""

    def test_connection(self, database):
        assert database["connection"] == "db://test"

    def test_add_data(self, database):
        database["data"].append("item1")
        assert len(database["data"]) >= 1

    def test_data_persists(self, database):
        # Same database instance, so previous test's data is still there
        assert len(database["data"]) >= 1
```

**Fixture Methods Within Test Classes:**

You can define fixtures as methods inside test classes, providing class-specific setup:

```python
from rustest import fixture

class TestWithFixtureMethod:
    """Test class with its own fixture methods."""

    @fixture(scope="class")
    def class_resource(self):
        """Fixture method shared across tests in this class."""
        resource = {"value": 42, "name": "test_resource"}
        yield resource
        # Teardown happens after all tests in class
        resource["closed"] = True

    @fixture
    def per_test_data(self, class_resource):
        """Fixture method that depends on another fixture."""
        return {"id": id(self), "resource": class_resource}

    def test_uses_class_resource(self, class_resource):
        assert class_resource["value"] == 42

    def test_uses_per_test_data(self, per_test_data):
        assert "resource" in per_test_data
        assert per_test_data["resource"]["value"] == 42
```

**Class Variables and Instance Variables:**

Test classes can use class variables for shared state and instance variables for per-test isolation:

```python
class TestWithVariables:
    """Test class with class and instance variables."""

    class_variable = "shared_data"  # Shared across all tests

    def test_class_variable(self):
        # Access class variable
        assert self.class_variable == "shared_data"
        assert TestWithVariables.class_variable == "shared_data"

    def test_instance_variable(self):
        # Each test gets a fresh instance
        self.instance_var = "test_specific"
        assert self.instance_var == "test_specific"
```

**Parametrized Test Methods:**

Use `@parametrize` on class methods just like regular test functions:

```python
from rustest import parametrize

class TestParametrized:
    """Test class with parametrized methods."""

    @parametrize("value,expected", [(2, 4), (3, 9), (4, 16)])
    def test_square(self, value, expected):
        assert value ** 2 == expected
```

### Test Output

When you run rustest, you'll see clean, informative output:

```
  PASSED   0.001s test_simple_assertion
  PASSED   0.002s test_string_operations
  PASSED   0.001s test_database_query
  PASSED   0.003s test_square[two]
  PASSED   0.001s test_square[three]
  PASSED   0.002s test_square[four]
 SKIPPED   0.000s test_future_feature
  FAILED   0.005s test_broken_feature
----------------------------------------
AssertionError: Expected 5, got 4
  at test_example.py:42

8 tests: 6 passed, 1 failed, 1 skipped in 0.015s
```

## Feature Comparison with pytest

Rustest aims to provide the most commonly-used pytest features with dramatically better performance. Here's how the two compare:

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
| Fixture parametrization | ✅ | 🚧 | Planned |
| **Parametrization** |
| `@parametrize` decorator | ✅ | ✅ | Full support with custom IDs |
| Multiple parameter sets | ✅ | ✅ | |
| Parametrize with fixtures | ✅ | ✅ | |
| **Marks** |
| `@mark.skip` / `@skip` | ✅ | ✅ | Skip tests with reasons |
| Custom marks (`@mark.slow`, etc.) | ✅ | ✅ | Just added! |
| Mark with arguments | ✅ | ✅ | `@mark.timeout(30)` |
| Selecting tests by mark (`-m`) | ✅ | 🚧 | Mark metadata collected, filtering planned |
| **Test Execution** |
| Detailed assertion introspection | ✅ | ❌ | Uses standard Python assertions |
| Parallel execution | ✅ (`pytest-xdist`) | 🚧 | Planned (Rust makes this easier) |
| Test isolation | ✅ | ✅ | |
| Stdout/stderr capture | ✅ | ✅ | |
| **Reporting** |
| Pass/fail/skip summary | ✅ | ✅ | |
| Failure tracebacks | ✅ | ✅ | Full Python traceback support |
| Duration reporting | ✅ | ✅ | Per-test timing |
| JUnit XML output | ✅ | 🚧 | Planned |
| HTML reports | ✅ (`pytest-html`) | 🚧 | Planned |
| **Advanced Features** |
| Plugins | ✅ | ❌ | Not planned (keeps rustest simple) |
| Hooks | ✅ | ❌ | Not planned |
| Custom collectors | ✅ | ❌ | Not planned |
| `conftest.py` | ✅ | ✅ | Shared fixtures across test files |
| **Developer Experience** |
| Fully typed Python API | ⚠️ | ✅ | rustest uses `basedpyright` strict mode |
| Fast CI/CD runs | ⚠️ | ✅ | 78x faster = dramatically shorter feedback loops |

**Legend:**
- ✅ Fully supported
- 🚧 Planned or in progress
- ⚠️ Partial support
- ❌ Not planned

**Philosophy:** Rustest implements the 20% of pytest features that cover 80% of use cases, with a focus on raw speed and simplicity. If you need advanced pytest features like plugins or custom hooks, stick with pytest. If you want fast, straightforward testing with familiar syntax, rustest is for you.

## License

rustest is distributed under the terms of the MIT license. See [LICENSE](LICENSE).
