# Development Guide

This guide is for developers who want to contribute to rustest. Don't worry if you're new to Rust—you don't need to be a Rust expert to get started!

## Prerequisites

### 1. Rust

Install Rust using rustup (the official Rust installer):

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Add to PATH
source $HOME/.cargo/env

# Verify installation
rustc --version  # Should show "rustc 1.75.0" or similar
cargo --version  # Should show "cargo 1.75.0" or similar
```

!!! info "What is Rust?"
    Rust is a systems programming language that provides memory safety and high performance. In rustest, Rust handles the fast parts (test discovery and execution) while Python provides the friendly API.

### 2. uv

Install uv (fast Python package manager):

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify installation
uv --version
```

!!! info "What is uv?"
    Think of it as a faster, more modern alternative to pip and virtualenv. It manages Python dependencies and virtual environments.

### 3. Python 3.10-3.14

```bash
# Check your Python version
python3 --version  # Should be 3.10 through 3.14
```

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/Apex-Engineers-Inc/rustest.git
cd rustest
```

### 2. Install Python Dependencies

```bash
# Creates virtual environment and installs dependencies
uv sync --all-extras
```

This installs all Python packages needed for development (testing, linting, type checking, etc.)

### 3. Build the Rust Extension

```bash
# Compiles Rust code and installs as Python module
uv run maturin develop
```

This:
- Compiles the Rust code in `src/` into a native Python extension
- Installs it in your virtual environment
- Takes a minute or two the first time (subsequent builds are faster)

!!! warning "Common Issue"
    If you see errors about missing Rust toolchain, make sure you completed step 1 above and ran `source $HOME/.cargo/env`.

### 4. Verify Everything Works

```bash
# Run example tests
uv run rustest examples/tests/

# Run Python unit tests
uv run poe pytests

# Run Rust tests
cargo test
```

If all three commands succeed, you're ready to develop! 🎉

## Project Structure

Rustest is a **hybrid Python/Rust project**:

```
rustest/
├── src/                          # 🦀 Rust code (the fast core)
│   ├── lib.rs                    # Main entry point
│   ├── model.rs                  # Data structures
│   ├── discovery/                # Fast test file discovery
│   ├── execution/                # Test execution engine
│   └── python_support/           # Rust↔Python bridge
│
├── python/rustest/               # 🐍 Python code (the friendly API)
│   ├── __init__.py               # Public API
│   ├── decorators.py             # @fixture, @parametrize, @skip
│   ├── cli.py                    # Command-line interface
│   ├── reporting.py              # Test results and reports
│   └── core.py                   # Wrapper around Rust layer
│
├── python/tests/                 # 🧪 Python unit tests
│   ├── test_decorators.py
│   ├── test_core.py
│   └── ...
│
├── tests/                        # 🧪 Integration tests
│   ├── test_basic.py
│   ├── test_fixtures.py
│   └── ...
│
├── Cargo.toml                    # Rust dependencies
└── pyproject.toml                # Python dependencies
```

**Key Concepts:**
- **Rust side:** Fast test discovery and execution (the engine)
- **Python side:** Friendly decorators and API (the steering wheel)
- **PyO3/Maturin:** The bridge connecting Rust and Python

## Development Tasks

We use `poe` (poethepoet) as a task runner:

| Command | What it does | When to use it |
|---------|-------------|----------------|
| `poe dev` | Rebuild Rust extension | After changing `.rs` files |
| `poe pytests` | Run Python tests | After changing Python code |
| `poe lint` | Check Python code style | Before committing |
| `poe typecheck` | Check Python types | Before committing |
| `poe fmt` | Format Rust code | Before committing Rust changes |
| `poe unit` | Run example test suite | Verify end-to-end functionality |
| `cargo test` | Run Rust tests | After changing Rust code |
| `cargo check` | Fast-check Rust compiles | While developing Rust code |

## Pre-commit Hooks

We use pre-commit hooks to automatically check code quality:

### Setup (One-time)

```bash
# Install pre-commit hooks
uv run pre-commit install
```

### What it does

Every time you run `git commit`, pre-commit automatically:

- ✅ Formats Python code with ruff
- ✅ Lints Python code with ruff
- ✅ Type-checks Python code with basedpyright
- ✅ Formats Rust code with cargo fmt
- ✅ Lints Rust code with cargo clippy
- ✅ Checks YAML and TOML files
- ✅ Trims trailing whitespace
- ✅ Fixes end-of-file issues

If any check fails, the commit is blocked until you fix the issues.

### Manual Usage

```bash
# Run all hooks on all files
uv run pre-commit run --all-files

# Run hooks only on staged files
uv run pre-commit run

# Skip hooks for a single commit (not recommended!)
git commit --no-verify
```

## Typical Workflow

```bash
# 1. Make your changes to Python or Rust files

# 2. If you changed Rust code, rebuild:
poe dev

# 3. Run tests:
poe pytests        # Python tests
cargo test         # Rust tests

# 4. Commit your changes (pre-commit runs automatically):
git add .
git commit -m "Your message"
# → Pre-commit hooks run automatically
# → If they pass, commit succeeds
# → If they fail, fix issues and try again
```

## Making Your First Change

### Adding a Python Feature

```bash
# 1. Edit a Python file
vim python/rustest/decorators.py

# 2. Run tests
poe pytests

# 3. Check types and style
poe typecheck
poe lint
```

### Adding a Rust Feature

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

## Testing Your Changes

### Python Tests

```bash
# Run all Python tests
poe pytests

# Run a specific test file
python -m unittest python.tests.test_decorators

# Run a specific test
python -m unittest python.tests.test_decorators.FixtureDecoratorTests.test_fixture_marks_callable
```

### Rust Tests

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

## Understanding the Rust↔Python Bridge

Don't worry if Rust feels unfamiliar! Here's what you need to know:

1. **PyO3** is a Rust library that lets Rust code interact with Python
2. **Maturin** compiles Rust code into a Python module
3. The Rust code in `src/lib.rs` exports functions that Python can call
4. The `#[pyfunction]` macro marks Rust functions Python can use
5. Data flows: Python → Rust (fast processing) → Python (results)

**Example:**

```rust
// In src/lib.rs - This Rust function...
#[pyfunction]
fn run(paths: Vec<String>, ...) -> PyResult<PyRunReport> {
    // Fast Rust code here
}
```

<!--pytest.mark.skip-->
```python
# ...can be called from Python:
from rustest.rust import run
report = run(paths=["tests"])
```

## Troubleshooting

### "Cannot import name 'rust'"

**Problem:** The Rust extension isn't built.

**Solution:** Run `uv run maturin develop`

### "error: linker 'cc' not found"

**Problem:** Missing C compiler (needed to compile Rust).

**Solution:**
- Ubuntu/Debian: `sudo apt-get install build-essential`
- macOS: `xcode-select --install`
- Windows: Install Visual Studio C++ Build Tools

### "cargo test" fails with linking errors

**Problem:** Python development headers missing.

**Solution:**
- Ubuntu/Debian: `sudo apt-get install python3-dev`
- macOS: Should work out of the box
- Windows: Reinstall Python with "Include development headers"

### Tests pass locally but fail in CI

**Problem:** Need to rebuild after pulling changes.

**Solution:** Run `poe dev` to rebuild the Rust extension

## Getting Help

- **Rust documentation:** https://doc.rust-lang.org/book/
- **PyO3 guide:** https://pyo3.rs/
- **rustest issues:** https://github.com/Apex-Engineers-Inc/rustest/issues

!!! tip "For Python Developers New to Rust"
    - You don't need to be a Rust expert to contribute!
    - Start with Python-side changes (decorators, CLI, reporting)
    - The Rust code is well-commented and designed to be readable
    - Ask questions in issues or pull requests—we're here to help!

## Documentation

### Updating CLI Documentation

If you change CLI arguments, update the documentation:

```bash
# Automatically update CLI help in docs
python scripts/update_cli_docs.py
```

This script captures the output of `rustest --help` and updates `docs/guide/cli.md`.

### Building Documentation Locally

```bash
# Install docs dependencies
uv pip install -e ".[docs]"

# Serve docs locally
mkdocs serve

# Build docs
mkdocs build
```

Visit http://127.0.0.1:8000 to preview the documentation.

## Quick Reference

```bash
# Setup (first time only)
uv sync --all-extras
uv run maturin develop
uv run pre-commit install

# Daily development
poe dev          # Rebuild Rust after changes
poe pytests      # Run Python tests
cargo test       # Rust tests

# Update docs after CLI changes
python scripts/update_cli_docs.py

# Before committing
poe lint         # Check Python style
poe typecheck    # Check Python types
poe fmt          # Format Rust code

# Or let pre-commit handle it
git commit -m "message"  # Pre-commit runs all checks
```

## Contributing

1. **Fork the repository** on GitHub
2. **Create a feature branch**: `git checkout -b feature-name`
3. **Make your changes** following the workflow above
4. **Run tests** and ensure pre-commit checks pass
5. **Submit a pull request** with a clear description

We welcome contributions of all kinds:
- Bug fixes
- New features
- Documentation improvements
- Performance optimizations
- Test improvements

## See Also

- [Performance](performance.md) - Understanding rustest's speed
- [Comparison with pytest](comparison.md) - Feature compatibility
- [API Reference](../api/overview.md) - Code you might modify
