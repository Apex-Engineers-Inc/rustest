# rustest

Rustest (pronounced like Russ-Test) is a Rust-powered test runner that aims to provide the most common pytest ergonomics with a focus on raw performance. Get **78x faster** test execution with familiar syntax and minimal setup.

## Why rustest?

- 🚀 **78x faster** than pytest (measured on real-world integration tests)
- ✅ Familiar `@fixture`, `@parametrize`, `@skip`, and `@mark` decorators
- 🔍 Automatic test discovery (`test_*.py` and `*_test.py` files)
- 🎯 Simple, clean API—if you know pytest, you already know rustest
- 📦 Easy installation with pip or uv
- ⚡ Sub-10ms execution for small test suites—tests feel instant

## Performance

Rustest is designed for speed. Our benchmarks show **78x faster** execution compared to pytest on the rustest integration test suite (~199 tests):

| Test Runner | Time | Tests/Second | Speedup |
|-------------|------|--------------|---------|
| pytest      | 0.39s | 502 | 1.0x (baseline) |
| rustest     | 0.005s | 39,800 | **78x faster** |

**Actual CI measurements:**
- **pytest**: 196 passed, 5 skipped in 0.39s
- **rustest**: 194 passed, 5 skipped in 0.005s

**Why so fast?**
- **Near-zero startup time**: Native Rust binary vs Python interpreter startup
- **Rust-native test discovery**: Minimal imports until test execution
- **Optimized fixture resolution**: Efficient dependency graph in Rust
- **Efficient orchestration**: ~50-100μs per-test overhead vs ~1-2ms in pytest

**Real-world impact:**
- **200 tests**: 0.39s → 0.005s (instant feedback)
- **1,000 tests**: ~2s → ~0.025s (tests complete before you can switch tabs)
- **10,000 tests**: ~20s → ~0.25s (dramatically faster feedback loops)

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

## Quick Start

### 1. Write Your Tests

Create a file `test_math.py`:

```python
from rustest import fixture, parametrize, mark

@fixture
def numbers() -> list[int]:
    return [1, 2, 3, 4, 5]

def test_sum(numbers: list[int]) -> None:
    assert sum(numbers) == 15

@parametrize("value,expected", [(2, 4), (3, 9), (4, 16)])
def test_square(value: int, expected: int) -> None:
    assert value ** 2 == expected

@mark.slow
def test_expensive_operation() -> None:
    # This test is marked as slow for filtering
    result = sum(range(1000000))
    assert result > 0
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
# Run all tests in current directory
rustest

# Run tests in specific paths
rustest tests/ integration/

# Filter tests by name pattern
rustest -k "user"           # Runs test_user_login, test_user_signup, etc.
rustest -k "auth"           # Runs all tests with "auth" in the name

# Control output capture
rustest --no-capture        # See print statements during test execution
```

### Python API Usage

You can also run rustest programmatically from Python:

```python
from rustest import run

# Basic usage
report = run(paths=["tests"])
print(f"Passed: {report.passed}, Failed: {report.failed}")

# With pattern filtering
report = run(paths=["tests"], pattern="user")

# Without output capture (see print statements)
report = run(paths=["tests"], capture_output=False)

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
| Test class detection (`Test*`) | ✅ | ✅ | via `unittest.TestCase` support |
| Pattern-based filtering | ✅ | ✅ | `-k` pattern matching |
| **Fixtures** |
| `@fixture` decorator | ✅ | ✅ | Rust-based dependency resolution |
| Fixture dependency injection | ✅ | ✅ | Much faster in rustest |
| Fixture scopes (function/class/module/session) | ✅ | ✅ | Full support for all scopes |
| Yield fixtures (setup/teardown) | ✅ | ✅ | Full support with cleanup |
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
