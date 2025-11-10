<div align="center">

![rustest logo](assets/logo.svg)

</div>

Rustest (pronounced like Russ-Test) is a Rust-powered test runner that aims to provide the most common pytest ergonomics with a focus on raw performance. Get **massive speedups (8.5× average, up to 19× faster)** with familiar syntax and minimal setup.

📚 **[Full Documentation](https://apex-engineers-inc.github.io/rustest)** | [Getting Started](https://apex-engineers-inc.github.io/rustest/getting-started/quickstart/) | [User Guide](https://apex-engineers-inc.github.io/rustest/guide/writing-tests/) | [API Reference](https://apex-engineers-inc.github.io/rustest/api/overview/)

## Why rustest?

- 🚀 **8.5× average speedup** over pytest on the synthetic benchmark matrix (peaking at 19× on 5k-test suites)
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

Rustest is designed for speed. The new benchmark matrix generates identical pytest and rustest suites ranging from 1 to 5,000 tests and runs each command five times. Rustest delivers an **8.5× average speedup** and reaches **19× faster** execution on the largest suite:

| Test Count | pytest (mean) | rustest (mean) | Speedup | pytest tests/s | rustest tests/s |
|-----------:|--------------:|---------------:|--------:|----------------:|-----------------:|
|          1 |       0.428s |        0.116s |    3.68x |             2.3 |              8.6 |
|          5 |       0.428s |        0.120s |    3.56x |            11.7 |             41.6 |
|         20 |       0.451s |        0.116s |    3.88x |            44.3 |            171.7 |
|        100 |       0.656s |        0.133s |    4.93x |           152.4 |            751.1 |
|        500 |       1.206s |        0.146s |    8.29x |           414.4 |           3436.1 |
|      1,000 |       1.854s |        0.171s |   10.83x |           539.4 |           5839.4 |
|      2,000 |       3.343s |        0.243s |   13.74x |           598.3 |           8219.9 |
|      5,000 |       7.811s |        0.403s |   19.37x |           640.2 |          12399.7 |

### What speedup should you expect?

- **Tiny suites (≤20 tests):** Expect **~3–4× faster** runs. Startup costs dominate here, so both runners feel instant, but rustest still trims a few hundred milliseconds on every run.
- **Growing suites (≈100–500 tests):** Expect **~5–8× faster** execution. Once you have a few dozen files, rustest's lean discovery and fixture orchestration start to compound.
- **Large suites (≥1,000 tests):** Expect **~11–19× faster** runs. Bigger suites amortize startup overhead entirely, letting rustest's Rust core stretch its legs and deliver order-of-magnitude gains.

Highlights:

- **8.5× average speedup** across the matrix (geometric mean 7.0×)
- **16.2× weighted speedup** when weighting by the number of executed tests
- **1.45s total runtime** for rustest vs **16.18s** for pytest across all suites

Reproduce the matrix locally:

```bash
python3 profile_tests.py --runs 5
python3 generate_comparison.py
```

### Real-world integration suite (~200 tests)

Our integration suite remains a great proxy for day-to-day use and still shows a **~2.1× wall-clock speedup**:

| Test Runner | Wall Clock | Speedup | Command |
|-------------|------------|---------|---------|
| pytest      | 1.33–1.59s | 1.0x (baseline) | `pytest tests/ examples/tests/ -q` |
| rustest     | 0.69–0.70s | **~2.1x faster** | `python -m rustest tests/ examples/tests/` |

### Large parametrized stress test

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
