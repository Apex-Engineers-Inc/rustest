# rustest

Rustest (pronounced like Russ-Test) is a Rust-powered test runner that aims to provide the most common pytest ergonomics with a focus on raw performance. Get **~2x faster** test execution with familiar syntax and minimal setup.

📚 **[Full Documentation](https://apex-engineers-inc.github.io/rustest)** | [Getting Started](https://apex-engineers-inc.github.io/rustest/getting-started/quickstart/) | [User Guide](https://apex-engineers-inc.github.io/rustest/guide/writing-tests/) | [API Reference](https://apex-engineers-inc.github.io/rustest/api/overview/)

## Why rustest?

- 🚀 **About 2x faster** than pytest on the rustest integration test suite
- ✅ Familiar `@fixture`, `@parametrize`, `@skip`, and `@mark` decorators
- 🔄 **Built-in async support** with `@mark.asyncio` (like pytest-asyncio)
- 🔍 Automatic test discovery (`test_*.py` and `*_test.py` files)
- 📝 **Built-in markdown code block testing** (like pytest-codeblocks, but faster)
- 🎯 Simple, clean API—if you know pytest, you already know rustest
- 🧮 Built-in `approx()` helper for tolerant numeric comparisons
- 🪤 `raises()` context manager for precise exception assertions
- 📦 Easy installation with pip or uv
- ⚡ Low-overhead execution keeps small suites feeling instant

## Performance

Rustest is designed for speed. Our latest benchmarks on the rustest integration suite (~200 tests) show a consistent **2.1x wall-clock speedup** over pytest:

| Test Runner | Wall Clock | Speedup | Command |
|-------------|------------|---------|---------|
| pytest      | 1.33–1.59s | 1.0x (baseline) | `pytest tests/ examples/tests/ -q` |
| rustest     | 0.69–0.70s | **~2.1x faster** | `python -m rustest tests/ examples/tests/` |

### Large Parametrized Stress Test

With **10,000 parametrized invocations**:

| Test Runner | Avg. Wall Clock | Speedup | Command |
|-------------|-----------------|---------|---------|
| pytest      | 9.72s           | 1.0x    | `pytest benchmarks/test_large_parametrize.py -q` |
| rustest     | 0.41s           | **~24x faster** | `python -m rustest benchmarks/test_large_parametrize.py` |

**[📊 View Detailed Performance Analysis →](https://apex-engineers-inc.github.io/rustest/advanced/performance/)**

## Installation

Rustest supports Python **3.10 through 3.14**.

<!--pytest.mark.skip-->
```bash
# Using pip
pip install rustest

# Using uv
uv add rustest
```

**[📖 Installation Guide →](https://apex-engineers-inc.github.io/rustest/getting-started/installation/)**

## Quick Start

### 1. Write Your Tests

Create a file `test_math.py`:

```python
from rustest import fixture, parametrize, mark, approx, raises
import asyncio

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
    result = sum(range(1000000))
    assert result > 0

@mark.asyncio
async def test_async_operation() -> None:
    # Example async operation
    await asyncio.sleep(0.001)
    result = 42
    assert result == 42

def test_division_by_zero() -> None:
    with raises(ZeroDivisionError, match="division by zero"):
        1 / 0
```

### 2. Run Your Tests

<!--pytest.mark.skip-->
```bash
# Run all tests
rustest

# Run specific tests
rustest tests/

# Filter by test name pattern
rustest -k "test_sum"

# Filter by marks
rustest -m "slow"                    # Run only slow tests
rustest -m "not slow"                # Skip slow tests
rustest -m "slow and integration"    # Run tests with both marks

# Rerun only failed tests
rustest --lf                         # Last failed only
rustest --ff                         # Failed first, then all others

# Exit on first failure
rustest -x                           # Fail fast

# Combine options
rustest --ff -x                      # Run failed tests first, stop on first failure

# Show output during execution
rustest --no-capture
```

**[📖 Full Quick Start Guide →](https://apex-engineers-inc.github.io/rustest/getting-started/quickstart/)**

## Documentation

**[📚 Full Documentation](https://apex-engineers-inc.github.io/rustest)**

### Getting Started
- [Installation](https://apex-engineers-inc.github.io/rustest/getting-started/installation/)
- [Quick Start](https://apex-engineers-inc.github.io/rustest/getting-started/quickstart/)

### User Guide
- [Writing Tests](https://apex-engineers-inc.github.io/rustest/guide/writing-tests/)
- [Fixtures](https://apex-engineers-inc.github.io/rustest/guide/fixtures/)
- [Parametrization](https://apex-engineers-inc.github.io/rustest/guide/parametrization/)
- [Marks & Skipping](https://apex-engineers-inc.github.io/rustest/guide/marks/)
- [Test Classes](https://apex-engineers-inc.github.io/rustest/guide/test-classes/)
- [Assertion Helpers](https://apex-engineers-inc.github.io/rustest/guide/assertions/)
- [Markdown Testing](https://apex-engineers-inc.github.io/rustest/guide/markdown-testing/)
- [CLI Usage](https://apex-engineers-inc.github.io/rustest/guide/cli/)
- [Python API](https://apex-engineers-inc.github.io/rustest/guide/python-api/)

### API Reference
- [API Overview](https://apex-engineers-inc.github.io/rustest/api/overview/)
- [Decorators](https://apex-engineers-inc.github.io/rustest/api/decorators/)
- [Test Execution](https://apex-engineers-inc.github.io/rustest/api/core/)
- [Reporting](https://apex-engineers-inc.github.io/rustest/api/reporting/)
- [Assertion Utilities](https://apex-engineers-inc.github.io/rustest/api/approx/)

### Advanced Topics
- [Performance](https://apex-engineers-inc.github.io/rustest/advanced/performance/)
- [Comparison with pytest](https://apex-engineers-inc.github.io/rustest/advanced/comparison/)
- [Development Guide](https://apex-engineers-inc.github.io/rustest/advanced/development/)

## Feature Comparison with pytest

Rustest implements the 20% of pytest features that cover 80% of use cases, with a focus on raw speed and simplicity.

**[📋 View Full Feature Comparison →](https://apex-engineers-inc.github.io/rustest/advanced/comparison/)**

✅ **Supported:** Fixtures, parametrization, marks, test classes, conftest.py, markdown testing
🚧 **Planned:** Parallel execution, mark filtering, JUnit XML output
❌ **Not Planned:** Plugins, hooks, custom collectors (keeps rustest simple)

## Contributing

We welcome contributions! See the [Development Guide](https://apex-engineers-inc.github.io/rustest/advanced/development/) for setup instructions.

Quick reference:

<!--pytest.mark.skip-->
```bash
# Setup
git clone https://github.com/Apex-Engineers-Inc/rustest.git
cd rustest
uv sync --all-extras
uv run maturin develop

# Run tests
uv run poe pytests  # Python tests
cargo test          # Rust tests

# Format and lint
uv run pre-commit install  # One-time setup
git commit -m "message"    # Pre-commit hooks run automatically
```

## License

rustest is distributed under the terms of the MIT license. See [LICENSE](LICENSE).
