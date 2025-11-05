# rustest

Rustest is a Rust-powered test runner that aims to provide the most common
pytest ergonomics with a focus on raw performance. The project ships with a
clean Python API, a friendly CLI, and a simple build pipeline powered by
[`uv`](https://github.com/astral-sh/uv), [`maturin`](https://github.com/PyO3/maturin),
and [`poethepoet`](https://github.com/nat-n/poethepoet).

## Features

- ✅ Familiar `@fixture`, `@parametrize`, `@skip`, and `@mark` helpers.
- ✅ Test discovery for files named `test_*.py` or `*_test.py`.
- ✅ Dependency-injected fixtures resolved by Rust for minimal overhead.
- ✅ Optional stdout/stderr capture and pretty CLI output.
- ✅ Fully typed Python API with `basedpyright` configuration ready to go.

## Feature Comparison

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

## Performance

Rustest is designed for speed. Our benchmarks show **2.5x faster** execution compared to pytest on a comprehensive test suite with 161 tests covering various scenarios:

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

Rustest supports Python **3.10 through 3.13**. The recommended workflow uses `uv` which acts
as both the dependency resolver and the virtual environment manager.

```bash
uv sync --all-extras
uv run maturin develop
```

The first command installs the Python dependencies declared in
`pyproject.toml`; the second compiles and installs the Rust extension in the
current environment.

## Usage

Running the CLI mirrors pytest's ergonomics:

```bash
uv run rustest tests/
```

or directly from Python:

```python
from rustest import run

report = run(paths=["tests"], pattern=None, workers=None, capture_output=True)
print(report)
```

## Getting Started for Development

This guide is designed for Python developers who want to contribute to rustest, even if you're new to Rust. Don't worry—you don't need to be a Rust expert to get started!

### Prerequisites

Before you begin, you'll need to install the following tools:

#### 1. **Rust** (the programming language that powers the core)
```bash
# Install Rust using rustup (the official Rust installer)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# After installation, make sure it's in your PATH
source $HOME/.cargo/env

# Verify installation
rustc --version  # Should show something like "rustc 1.75.0"
cargo --version  # Should show something like "cargo 1.75.0"
```

**What is Rust?** Rust is a systems programming language that provides memory safety and high performance. In rustest, Rust handles the fast parts (test discovery and execution) while Python provides the friendly API.

#### 2. **uv** (fast Python package manager)
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify installation
uv --version
```

**What is uv?** Think of it as a faster, more modern alternative to pip and virtualenv. It manages Python dependencies and virtual environments.

#### 3. **Python 3.10-3.13**
```bash
# Check your Python version
python3 --version  # Should be 3.10 through 3.13
```

### Step-by-Step Setup

#### 1. Clone the Repository
```bash
git clone https://github.com/Apex-Engineers-Inc/rustest.git
cd rustest
```

#### 2. Install Python Dependencies
```bash
# This creates a virtual environment and installs all dependencies
uv sync --all-extras
```

**What this does:** Installs all Python packages needed for development (testing, linting, type checking, etc.)

#### 3. Build the Rust Extension
```bash
# This compiles the Rust code and installs it as a Python module
uv run maturin develop
```

**What this does:**
- Compiles the Rust code in `src/` into a native Python extension
- Installs it in your virtual environment so Python can import it
- Takes a minute or two the first time (subsequent builds are faster)

**Common issue:** If you see errors about missing Rust toolchain, make sure you completed step 1 above.

#### 4. Verify Everything Works
```bash
# Run the example tests
uv run rustest examples/tests/

# Run the Python unit tests
uv run poe pytests

# Run the Rust tests
cargo test
```

If all three commands succeed, you're ready to develop! 🎉

### Understanding the Project Structure

rustest is a **hybrid Python/Rust project**. Here's what each part does:

```
rustest/
├── src/                          # 🦀 Rust code (the fast core - rustest-core crate)
│   ├── lib.rs                    # Main entry point
│   ├── model.rs                  # Data structures (TestCase, Fixture, etc.)
│   ├── discovery/                # Fast test file discovery
│   ├── execution/                # Test execution engine
│   └── python_support/           # Rust↔Python bridge utilities
│
├── python/rustest/               # 🐍 Python code (the friendly API)
│   ├── __init__.py               # Public API (what users import)
│   ├── _decorators.py            # @fixture, @parametrize, @skip
│   ├── _cli.py                   # Command-line interface
│   ├── _reporting.py             # Test results and reports
│   └── core.py                   # Wrapper around Rust layer
│
├── python/tests/                 # 🧪 Python unit tests
│   ├── test_decorators.py        # Test decorator functionality
│   ├── test_core.py              # Test core API
│   ├── test_cli.py               # Test CLI
│   └── ...
│
├── tests/                        # 🧪 Integration test suite
│   ├── test_basic.py             # Basic test functions
│   ├── test_fixtures.py          # Fixture dependency injection
│   └── ...
│
├── Cargo.toml                    # Rust dependencies (rustest-core crate)
├── pyproject.toml                # Python dependencies & project config
└── README.md                     # This file!
```

**Key Concepts:**
- **Rust side (rustest-core):** Fast test discovery and execution. Think of it as the engine.
- **Python side (rustest):** Friendly decorators and API. Think of it as the steering wheel.
- **PyO3/Maturin:** The bridge that connects Rust and Python together.

### Common Development Tasks

We use `poe` (poethepoet) as a task runner. Think of it like `make` or `npm scripts`:

| Command | What it does | When to use it |
|---------|-------------|----------------|
| `poe dev` | Rebuild the Rust extension | After changing any `.rs` files |
| `poe pytests` | Run Python tests | After changing Python code |
| `poe lint` | Check Python code style | Before committing |
| `poe typecheck` | Check Python types with basedpyright | Before committing |
| `poe fmt` | Format Rust code | Before committing Rust changes |
| `poe unit` | Run example test suite | To verify end-to-end functionality |
| `cargo test` | Run Rust tests | After changing Rust code |
| `cargo check` | Fast-check Rust code compiles | While developing Rust code |

**Typical workflow:**
```bash
# 1. Make your changes to Python or Rust files

# 2. If you changed Rust code, rebuild:
poe dev

# 3. Run tests:
poe pytests        # Python tests
cargo test         # Rust tests

# 4. Check code quality:
poe lint           # Python linting
poe typecheck      # Python type checking
poe fmt            # Rust formatting

# 5. Commit your changes!
```

### Making Your First Change

Let's walk through a simple example:

#### Adding a New Python Feature
```bash
# 1. Edit a Python file
vim python/rustest/_decorators.py

# 2. Run tests
poe pytests

# 3. Check types and style
poe typecheck
poe lint
```

#### Adding a New Rust Feature
```bash
# 1. Edit a Rust file
vim src/model.rs

# 2. Rebuild the extension
poe dev

# 3. Run Rust tests
cargo test

# 4. Format code
poe fmt
```

### Testing Your Changes

#### Python Tests
```bash
# Run all Python tests
poe pytests

# Run a specific test file
python -m unittest python.tests.test_decorators

# Run a specific test
python -m unittest python.tests.test_decorators.FixtureDecoratorTests.test_fixture_marks_callable
```

#### Rust Tests
```bash
# Run all Rust tests
cargo test

# Run tests with output visible
cargo test -- --nocapture

# Run a specific test
cargo test discovers_basic_test_functions

# Run only unit tests (faster)
cargo test --lib
```

### Understanding the Rust↔Python Bridge

Don't worry if Rust feels unfamiliar! Here's what you need to know:

1. **PyO3** is a Rust library that lets Rust code interact with Python
2. **Maturin** compiles Rust code into a Python module (like a `.so` or `.pyd` file)
3. The Rust code in `src/lib.rs` exports functions that Python can call
4. The `#[pyfunction]` macro marks Rust functions that Python can use
5. Data flows: Python → Rust (fast processing) → Python (results)

**Example:**
```rust
// In src/lib.rs - This Rust function...
#[pyfunction]
fn run(paths: Vec<String>, ...) -> PyResult<PyRunReport> {
    // Fast Rust code here
}

// ...can be called from Python:
from rustest._rust import run
report = run(paths=["tests"])
```

### Troubleshooting

#### "Cannot import name '_rust'"
**Problem:** The Rust extension isn't built.
**Solution:** Run `uv run maturin develop`

#### "error: linker 'cc' not found"
**Problem:** Missing C compiler (needed to compile Rust).
**Solution:**
- Ubuntu/Debian: `sudo apt-get install build-essential`
- macOS: `xcode-select --install`
- Windows: Install Visual Studio C++ Build Tools

#### "cargo test" fails with linking errors
**Problem:** Python development headers missing.
**Solution:**
- Ubuntu/Debian: `sudo apt-get install python3-dev`
- macOS: Should work out of the box
- Windows: Reinstall Python with "Include development headers" checked

#### Tests pass locally but fail in CI
**Problem:** Might need to rebuild after pulling changes.
**Solution:** Run `poe dev` to rebuild the Rust extension

### Getting Help

- **Rust documentation:** https://doc.rust-lang.org/book/
- **PyO3 guide:** https://pyo3.rs/
- **rustest issues:** https://github.com/Apex-Engineers-Inc/rustest/issues

**For Python developers new to Rust:**
- You don't need to be a Rust expert to contribute!
- Start with Python-side changes (decorators, CLI, reporting)
- The Rust code is well-commented and designed to be readable
- Ask questions in issues or pull requests—we're here to help!

### Quick Reference

```bash
# Setup (first time only)
uv sync --all-extras
uv run maturin develop

# Daily development
poe dev          # Rebuild Rust after changes
poe pytests      # Run Python tests
cargo test       # Run Rust tests

# Before committing
poe lint         # Check Python style
poe typecheck    # Check Python types
poe fmt          # Format Rust code

# Running the tool
uv run rustest examples/tests/
```

## Example

A minimal test suite can look like this:

```python
from rustest import fixture, parametrize, mark

@fixture
def numbers() -> list[int]:
    return [1, 2, 3]

@mark.unit
@parametrize("value", [(1,), (2,), (3,)])
def test_numbers(numbers: list[int], value: int) -> None:
    assert value in numbers

@mark.slow
@mark.integration
def test_api_endpoint() -> None:
    # Marks help categorize and filter tests
    assert True
```

Running `uv run rustest` executes the test with Rust-speed discovery and a clean
summary.

## License

rustest is distributed under the terms of the MIT license. See [LICENSE](LICENSE).
