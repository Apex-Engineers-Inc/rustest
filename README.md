# rustest

Rustest is a Rust-powered test runner that aims to provide the most common pytest ergonomics with a focus on raw performance. Get **2.5x faster** test execution with familiar syntax and minimal setup.

## Why rustest?

- 🚀 **2.5x faster** than pytest on average (3x faster for fixture-heavy tests)
- ✅ Familiar `@fixture`, `@parametrize`, `@skip`, and `@mark` decorators
- 🔍 Automatic test discovery (`test_*.py` and `*_test.py` files)
- 🎯 Simple, clean API—if you know pytest, you already know rustest
- 📦 Easy installation with pip or uv

## Performance

Rustest is designed for speed. Our benchmarks show **2.5x faster** execution compared to pytest on a comprehensive test suite with 161 tests:

| Test Runner | Avg Time | Tests/Second | Speedup |
|-------------|----------|--------------|---------|
| pytest      | 0.632s   | 254.5        | 1.0x (baseline) |
| rustest     | 0.253s   | 636.4        | **2.5x faster** |

**Performance by test type:**
- **Simple tests**: 2.5x faster
- **Fixture tests**: 3.0x faster (Rust-based fixture resolution shines here)
- **Parametrized tests**: 2.5x faster
- **Combined (fixtures + params)**: 2.8x faster

The performance advantage grows with larger test suites. For a 1,000-test suite, rustest can save ~2.4 seconds per run, which adds up quickly in CI/CD pipelines and during development.

See [BENCHMARKS.md](BENCHMARKS.md) for detailed performance analysis and methodology.

## Installation

Rustest supports Python **3.10 through 3.13**.

### Using pip
```bash
pip install rustest
```

### Using uv (recommended)
```bash
uv add rustest --dev
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

# Experimental: parallel execution
rustest -n 4                # Run with 4 worker processes
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
| `test_*.py` / `*_test.py` files | ✅ | ✅ | Rustest uses Rust for 2.5x faster discovery |
| Test function detection (`test_*`) | ✅ | ✅ | |
| Test class detection (`Test*`) | ✅ | ✅ | via `unittest.TestCase` support |
| Pattern-based filtering | ✅ | ✅ | `-k` pattern matching |
| **Fixtures** |
| `@fixture` decorator | ✅ | ✅ | Rust-based dependency resolution |
| Fixture dependency injection | ✅ | ✅ | 3x faster in rustest |
| Fixture scopes (function/class/module/session) | ✅ | 🚧 | Function-scope only (for now) |
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
| `conftest.py` | ✅ | 🚧 | Planned for fixture sharing |
| **Developer Experience** |
| Fully typed Python API | ⚠️ | ✅ | rustest uses `basedpyright` strict mode |
| Fast CI/CD runs | ⚠️ | ✅ | 2.5x faster = shorter feedback loops |

**Legend:**
- ✅ Fully supported
- 🚧 Planned or in progress
- ⚠️ Partial support
- ❌ Not planned

**Philosophy:** Rustest implements the 20% of pytest features that cover 80% of use cases, with a focus on raw speed and simplicity. If you need advanced pytest features like plugins or custom hooks, stick with pytest. If you want fast, straightforward testing with familiar syntax, rustest is for you.

## Contributing

We welcome contributions! See [DEVELOPMENT.md](DEVELOPMENT.md) for:
- Development environment setup
- Project structure overview
- How to build and test your changes
- Troubleshooting common issues

Whether you're a Python developer new to Rust or a Rust expert, we'd love your help making rustest better!

## License

rustest is distributed under the terms of the MIT license. See [LICENSE](LICENSE).
