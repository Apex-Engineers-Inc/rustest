<div align="center">

![rustest logo](assets/logo.svg)

</div>

Rustest (pronounced like Russ-Test) is a Rust-powered test runner that aims to provide the most common pytest ergonomics with a focus on raw performance. Get **massive speedups (8.5× average, up to 19× faster)** with familiar syntax and minimal setup.

📚 **[Full Documentation](https://apex-engineers-inc.github.io/rustest)** | [Getting Started](https://apex-engineers-inc.github.io/rustest/getting-started/quickstart/) | [User Guide](https://apex-engineers-inc.github.io/rustest/guide/writing-tests/) | [API Reference](https://apex-engineers-inc.github.io/rustest/api/overview/)

## 🚀 Try It Now — Zero Commitment

**Test rustest on your existing pytest suite in 10 seconds:**

<!--pytest.mark.skip-->
```bash
# Using uv
uv add rustest
uv run rustest --pytest-compat tests/

# Or using pip
pip install rustest
python -m rustest --pytest-compat tests/
```

**That's it!** The `--pytest-compat` flag lets you run your existing pytest tests with rustest **without changing a single line of code**. See the speedup immediately, then decide if you want to migrate.

<details>
<summary><b>What does --pytest-compat do?</b></summary>

The `--pytest-compat` mode intercepts `import pytest` statements and provides rustest implementations transparently:

- ✅ Works with existing `@pytest.fixture`, `@pytest.mark.*`, `@pytest.mark.parametrize()`
- ✅ **Fixture parametrization**: `@pytest.fixture(params=[...])` with `request.param`
- ✅ **Request object**: `request.node` (test metadata/markers), `request.config` (options/ini values)
- ✅ Supports `pytest.param()` with custom IDs
- ✅ Built-in fixtures: `tmp_path`, `tmpdir`, `monkeypatch`, `mocker`, `capsys`, `capfd`, `caplog`, `cache`, `request`
- ✅ Handles `pytest.raises()`, `pytest.fail()`, `pytest.skip()`, `pytest.xfail()`, `pytest.approx()`
- ✅ **Async support**: `@pytest.mark.asyncio` for async tests (built-in, no plugin needed)
- ✅ **Mocking built-in**: `mocker` fixture (like pytest-mock, no plugin needed)
- ✅ Warning capture: `pytest.warns()`, `pytest.deprecated_call()`
- ✅ Module skipping: `pytest.importorskip()`
- ✅ No code changes required — just run and compare!

**Known Limitations:**
- ⚠️ No pytest plugin support (by design - see [Plugin Compatibility](https://apex-engineers-inc.github.io/rustest/advanced/pytest-plugins/))
- ⚠️ No `_pytest` module internals (assertion rewriting, hook system)
- ⚠️ `request.node.parent`, `request.node.session` are always None
- ⚠️ Advanced pytest features require migration (see [Migration Guide](https://apex-engineers-inc.github.io/rustest/migration-guide/))

**Example output:**

```
╔════════════════════════════════════════════════════════════╗
║             RUSTEST PYTEST COMPATIBILITY MODE              ║
╠════════════════════════════════════════════════════════════╣
║ Running pytest tests with rustest.                         ║
║                                                            ║
║ Supported: fixtures, parametrize, marks, approx            ║
║ Built-ins: tmp_path, tmpdir, monkeypatch, mocker, capsys,  ║
║            capfd, caplog, cache, request (with node & cfg) ║
║ Functions: skip(), xfail(), fail(), raises(), warns()      ║
║ Async: @mark.asyncio (built-in, no plugin needed)          ║
║ Mocking: mocker fixture (pytest-mock compatible)           ║
║                                                            ║
║ NOTE: Plugin APIs are stubbed (non-functional).            ║
║ pytest_asyncio and other plugins can import,               ║
║ but advanced plugin features won't work.                   ║
║                                                            ║
║ For full features, use native rustest:                     ║
║   from rustest import fixture, mark, ...                   ║
╚════════════════════════════════════════════════════════════╝
```

Once you see the performance gains, migrate to native rustest imports for the full feature set.

</details>

## 💭 Why Rustest Exists

**Short version:** Python testing is too slow. We can do better.

**Longer version:** I love pytest—the API is elegant, fixtures are powerful, and good tests make better code. But if you've used **vitest** or **bun test** in JavaScript/TypeScript, you know what fast testing feels like:

- Tests run in milliseconds, not seconds
- You get instant feedback on every save
- TDD becomes enjoyable, not tedious
- You stay in flow instead of context-switching

**Why doesn't Python have this?**

Rustest brings that experience to Python. Same pytest API you know, backed by Rust's performance. Fast tests aren't just convenient—they change how you develop.

**Pytest nailed the API. Rustest brings the speed.**

## Why rustest?

### For pytest Users: Same API, Better Performance

- 🚀 **8.5× average speedup** over pytest (peaking at 19× on large suites)
- 🧪 **Drop-in compatibility** — Use `--pytest-compat` to run existing tests unchanged
- ✅ **Same decorators**: `@fixture`, `@parametrize`, `@mark` — you already know these
- 🔄 **Built-in async** — No pytest-asyncio plugin needed, just `@mark.asyncio`
- 🎭 **Built-in mocking** — No pytest-mock plugin needed, `mocker` fixture works out of the box
- 📊 **Simple coverage** — Works seamlessly with coverage.py ([guide](https://apex-engineers-inc.github.io/rustest/from-pytest/coverage/))
- 🔍 **No plugin dependencies** — Common features built-in, less to maintain
- 📝 **Built-in markdown testing** — Test code blocks in docs (like pytest-codeblocks)
- 🐛 **Crystal-clear errors** — Vitest-style output makes debugging effortless

### For Everyone

- 🎯 Simple API—if you know pytest, you already know rustest
- 🧮 Built-in `approx()`, `raises()`, `skip()`, `xfail()`, `fail()` helpers
- 🛠️ **Rich built-in fixtures**: `tmp_path`, `tmpdir`, `monkeypatch`, `mocker`, `capsys`, `capfd`, `caplog`, `cache`, `request`
- 📦 Easy installation: `pip install rustest` or `uv add rustest`
- ⚡ Low-overhead keeps small suites feeling instant

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

### Rustest's own test suite (~500 tests)

Running rustest's comprehensive test suite demonstrates both the performance gains and compatibility:

| Test Runner | Test Count | Wall Clock | Speedup | Notes |
|-------------|------------|------------|---------|-------|
| pytest      | 457 tests  | 1.95–2.04s | 1.0x (baseline) | With pytest-asyncio plugin |
| rustest     | 497 tests  | 0.54–0.58s | **~3.6x faster** | **Built-in async & fixture parametrization** |

**Key Points:**
- **Shared test suite compatibility** - 457 tests use `from rustest import fixture, mark` but run seamlessly with both pytest and rustest thanks to conftest.py automatic import compatibility
- **Rustest is ~3.6× faster** on the same test workload without requiring external plugins
- **pytest requires pytest-asyncio plugin** for async support; rustest has it built-in
- **Both support fixture parametrization** - rustest natively, pytest through standard `@pytest.fixture(params=[...])`
- rustest includes 40 additional tests for its pytest compatibility layer that only run with rustest

This demonstrates rustest's design philosophy: provide pytest-compatible APIs with significantly better performance and built-in features.

### Large parametrized stress test

With **10,000 parametrized invocations**:

| Test Runner | Avg. Wall Clock | Speedup | Command |
|-------------|-----------------|---------|---------|
| pytest      | 9.72s           | 1.0x    | `pytest benchmarks/test_large_parametrize.py -q` |
| rustest     | 0.41s           | **~24x faster** | `python -m rustest benchmarks/test_large_parametrize.py` |

**[📊 View Detailed Performance Analysis →](https://apex-engineers-inc.github.io/rustest/advanced/performance/)**

## Debugging: Crystal-Clear Error Messages

Rustest transforms confusing assertion failures into instantly readable error messages. Every test failure shows you exactly what went wrong and what was expected, without any guesswork.

### Enhanced Error Output

Rustest makes failed assertions obvious. Here's a simple example:

**Your test code:**

```python
def test_numeric_comparison():
    actual = 42
    expected = 100
    assert actual == expected
```

**What Rustest shows when it fails:**

```text
Code:
    def test_numeric_comparison():
        actual = 42
        expected = 100
      → assert actual == expected

E   AssertionError: assert 42 == 100
E   Expected: 100
E   Received: 42

─ /path/to/test_math.py:5
```

**What you get:**

- 📍 **Code Context** — Three lines of surrounding code with the failing line highlighted.
- ✨ **Vitest-style Output** — Clear "Expected" vs "Received" values with color coding.
- 🔍 **Value Substitution** — Real runtime values are inserted into the assertion (e.g., `assert 42 == 100`).
- 🎯 **Frame Introspection** — Even minimal assertions like `assert result == expected` show both runtime values.
- 🔗 **Clickable Locations** — File paths appear as clickable links for fast navigation in supported editors.

### Real-World Example

**Your test code:**

```python
class User:
    def __init__(self, email: str):
        self.email = email

def create_user(name: str, age: int):
    """Return a User with a properly formatted email."""
    return User(f"{name.lower()}@company.com")

def test_user_creation():
    user = create_user("Alice", 25)
    # Intentional mistake for demonstration:
    user.email = "alice.wrong@example.com"
    assert user.email == "alice@company.com"
```

**What Rustest shows when it fails:**

```text
Code:
    def test_user_creation():
        user = create_user("Alice", 25)
        user.email = "alice.wrong@example.com"
      → assert user.email == "alice@company.com"

E   AssertionError: assert 'alice.wrong@example.com' == 'alice@company.com'
E   Expected: alice@company.com
E   Received: alice.wrong@example.com

─ /path/to/test_users.py:10
```

**No more debugging confusion!** You immediately see what value was received, what was expected, and where it failed — all in a format inspired by pytest and vitest.

## Installation

Rustest supports Python **3.10 through 3.14**.

### Try First (No Installation)

Test rustest on your existing pytest tests without installing anything:

<!--pytest.mark.skip-->
```bash
# Try it instantly with uvx (recommended)
uvx rustest --pytest-compat tests/

# Or with pipx
pipx run rustest --pytest-compat tests/
```

### Install Permanently

Once you're convinced, install rustest:

<!--pytest.mark.skip-->
```bash
# Using pip
pip install rustest

# Using uv (recommended for new projects)
uv add rustest
```

**[📖 Installation Guide →](https://apex-engineers-inc.github.io/rustest/getting-started/installation/)**

## Quick Start

> **💡 Already have pytest tests?** Skip to step 2 and use `rustest --pytest-compat tests/` to run them immediately without changes!

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

# Run existing pytest tests without code changes
rustest --pytest-compat tests/

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

### Choose Your Learning Path

**New to Testing:**
- [Why Automated Testing?](https://apex-engineers-inc.github.io/rustest/new-to-testing/why-test/) — Learn the fundamentals
- [Your First Test](https://apex-engineers-inc.github.io/rustest/new-to-testing/first-test/) — Get started in 5 minutes
- [Testing Basics](https://apex-engineers-inc.github.io/rustest/new-to-testing/testing-basics/) — Core concepts explained
- [Complete Beginner Guide](https://apex-engineers-inc.github.io/rustest/new-to-testing/fixtures/) — Progress through all topics

**Coming from pytest:**
- [Feature Comparison Table](https://apex-engineers-inc.github.io/rustest/from-pytest/comparison/) — What works, what doesn't
- [5-Minute Migration](https://apex-engineers-inc.github.io/rustest/from-pytest/migration/) — Get running in minutes
- [Plugin Replacement Guide](https://apex-engineers-inc.github.io/rustest/from-pytest/plugins/) — Built-in alternatives
- [Coverage Integration](https://apex-engineers-inc.github.io/rustest/from-pytest/coverage/) — Simple coverage.py integration
- [Known Limitations](https://apex-engineers-inc.github.io/rustest/from-pytest/limitations/) — What's not supported (yet)

### Core Reference (Everyone)
- [Writing Tests](https://apex-engineers-inc.github.io/rustest/guide/writing-tests/) — Test functions, classes, and structure
- [Fixtures](https://apex-engineers-inc.github.io/rustest/guide/fixtures/) — Complete fixtures reference
- [Parametrization](https://apex-engineers-inc.github.io/rustest/guide/parametrization/) — Advanced parametrization
- [Marks & Filtering](https://apex-engineers-inc.github.io/rustest/guide/marks/) — Organizing tests
- [Assertions](https://apex-engineers-inc.github.io/rustest/guide/assertions/) — Assertion helpers
- [CLI Usage](https://apex-engineers-inc.github.io/rustest/guide/cli/) — Command-line options
- [API Reference](https://apex-engineers-inc.github.io/rustest/api/overview/) — Complete API docs

## Feature Comparison with pytest

Rustest implements the 20% of pytest features that cover 80% of use cases, with a focus on raw speed and simplicity.

**[📋 View Full Feature Comparison →](https://apex-engineers-inc.github.io/rustest/advanced/comparison/)**

✅ **Supported:**
- Core features: Fixtures, **fixture parametrization**, test parametrization, marks, test classes, conftest.py, markdown testing
- Built-in fixtures: `tmp_path`, `tmpdir`, `monkeypatch`, `capsys`, `capfd`, `caplog`, `cache`, `request`
- Test utilities: `pytest.raises()`, `pytest.fail()`, `pytest.approx()`, `pytest.warns()`
- Async testing: `@mark.asyncio` (pytest-asyncio compatible)
- **Pytest compatibility mode**: Run existing pytest tests with `--pytest-compat` (no code changes!)

🚧 **Planned:** Parallel execution, JUnit XML output, more built-in fixtures

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
