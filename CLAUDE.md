# CLAUDE.md

This file provides guidance for Claude Code when working with the rustest codebase.

## Project Overview

**rustest** is a Rust-powered pytest-compatible test runner focused on raw performance. It delivers massive speedups (8.5x average, up to 19x faster) while maintaining familiar pytest ergonomics.

- **Languages**: Rust (core engine) + Python (user API/CLI)
- **Build System**: Maturin (PyO3 bridge for Rust-Python integration)
- **Python Support**: 3.10 - 3.14
- **License**: MIT

## Project Structure

```
src/                          # Rust core (rustest-core crate)
├── lib.rs                    # Main entry point, PyO3 module
├── discovery.rs              # Fast test file discovery
├── execution.rs              # Test execution engine
├── model.rs                  # Data structures (TestCase, Fixture, etc.)
├── python_support.rs         # Rust-Python bridge
├── mark_expr.rs              # Mark expression parsing
├── cache.rs                  # Caching logic
└── output/                   # Output formatting

python/rustest/               # Python package (user API)
├── __init__.py               # Public API exports
├── __main__.py               # CLI entry point
├── decorators.py             # @fixture, @parametrize, @mark, etc.
├── builtin_fixtures.py       # tmp_path, tmpdir, monkeypatch, capsys, capfd, request
├── cli.py                    # Command-line interface
├── core.py                   # Wrapper around Rust layer
├── approx.py                 # Numeric comparison helper
└── compat/pytest.py          # pytest compatibility layer

python/tests/                 # Python unit tests
tests/                        # Integration test suite
examples/tests/               # Example test suite
docs/                         # MkDocs documentation
```

## Development Commands

### Initial Setup
```bash
uv sync --all-extras          # Install dependencies
uv run maturin develop        # Build Rust extension
```

### Building
```bash
uv run maturin develop        # Rebuild Rust extension after changes
poe dev                       # Alias for above
poe build                     # Build package for distribution
```

### Testing
```bash
# Python unit tests
uv run poe pytests
uv run pytest python/tests -v

# Integration tests
uv run pytest tests/ examples/tests/ -v

# Run with rustest itself
uv run python -m rustest tests/ examples/tests/ -v

# Rust tests
cargo test

# Example tests
uv run rustest examples/tests/
```

### Formatting (REQUIRED before commits)
```bash
# Rust - ALWAYS run for Rust changes
cargo fmt
cargo fmt --check             # Verify formatting

# Python - ALWAYS run for Python changes
uv run ruff format python
uv run ruff format --check python
```

### Linting
```bash
# Rust
cargo clippy --lib -- -D warnings

# Python
uv run ruff check python
uv run basedpyright python    # Type checking
```

### Pre-commit (runs all checks)
```bash
uv run pre-commit install     # One-time setup
uv run pre-commit run --all-files
```

### Task Runner Shortcuts
```bash
poe dev       # Rebuild Rust extension
poe pytests   # Run Python tests
poe lint      # Check Python style
poe typecheck # Type check Python
poe fmt       # Format Rust
poe unit      # Run example tests
```

## Code Style and Conventions

### Rust
- Follow standard Rust conventions (rustfmt)
- Use `cargo clippy` with `-D warnings` (treat warnings as errors)
- Document public APIs with doc comments
- Use `rayon` for parallelization where appropriate

### Python
- Follow Ruff formatting and linting rules
- Use type hints everywhere (checked by basedpyright)
- Public API is exported from `python/rustest/__init__.py`
- Decorators go in `decorators.py`, fixtures in `builtin_fixtures.py`

## Architecture Notes

### Hybrid Design
1. **Rust Core** (`src/`) - High-performance engine for:
   - Test discovery (globset, regex)
   - Test execution (rayon for parallelization)
   - Result formatting

2. **Python Layer** (`python/rustest/`) - User-friendly API for:
   - Decorators (`@fixture`, `@parametrize`, `@mark`)
   - Built-in fixtures
   - CLI interface
   - pytest compatibility

3. **PyO3/Maturin Bridge** - Compiled Rust exposed as `rustest.rust` module

### Key Entry Points
- CLI: `python -m rustest` → `__main__.py` → `cli.py:main()`
- Python API: `from rustest import fixture, mark, parametrize`
- Test Discovery: `src/discovery.rs:discover_tests()`
- Test Execution: `src/execution.rs:run_collected_tests()`

## Pre-commit Requirements

**CRITICAL**: Before any commit, ALL of the following must pass. CI will fail otherwise:

### Rust Changes
- `cargo fmt` - Format code
- `cargo fmt --check` - Verify formatting passes
- `cargo clippy --lib -- -D warnings` - No lint warnings
- `cargo build` - Must compile without errors

### Python Changes
- `uv run ruff format python` - Format code
- `uv run ruff check python` - No lint errors
- `uv run basedpyright python` - **ALL type checks must pass**

### All Changes
- `uv run pre-commit run --all-files` - Run complete check suite

### Tests (ALL must pass)
- `cargo test` - Rust unit tests
- `uv run pytest python/tests -v` - Python unit tests (via pytest)
- `uv run pytest tests/ examples/tests/ -v` - Integration tests (via pytest)
- `uv run python -m rustest tests/ examples/tests/ -v` - Integration tests (via rustest)
- Documentation code blocks - README and docs Python examples are tested

**CI will fail if ANY of the following exist**:
- Type errors (basedpyright)
- Formatting issues (cargo fmt, ruff format)
- Linting errors (clippy, ruff check)
- Compile errors (cargo build)
- **Test failures** (pytest, rustest, or documentation code blocks)

## Testing Requirements

**CRITICAL**: ALL tests must pass for CI to succeed.

### Test Execution
Tests are run through multiple runners to ensure compatibility:
1. **pytest** - Standard Python test runner
2. **rustest** - The project's own test runner
3. **Documentation tests** - Python code blocks in README.md and docs/

### When Adding Features
- Add unit tests in `python/tests/`
- Add integration tests in `tests/` if needed
- Ensure tests pass with **both pytest and rustest**
- If adding code examples to documentation, ensure they are valid and testable
- Update documentation if adding user-facing features

## Common Patterns

### Adding a new decorator
1. Implement in `python/rustest/decorators.py`
2. Export from `python/rustest/__init__.py`
3. Add tests in `python/tests/test_decorators.py`
4. Update type hints in `python/rustest/rust.pyi` if Rust interaction needed

### Adding a new built-in fixture
1. Implement in `python/rustest/builtin_fixtures.py`
2. Register in the fixtures registry
3. Add tests in `python/tests/test_builtin_fixtures.py`

### Modifying Rust core
1. Make changes in `src/`
2. Run `cargo test` for Rust tests
3. Run `uv run maturin develop` to rebuild
4. Run Python tests to verify integration

## CI/CD Pipeline

The CI workflow (`ci.yml`) runs all checks across Python 3.10-3.14. **ALL must pass**:

### Tests
- Python unit tests via pytest
- Integration tests via **both pytest and rustest**
- README.md Python code block validation
- Documentation code block validation

### Code Quality
- Formatting checks (cargo fmt, ruff format)
- Linting (clippy, ruff check)
- Type checking (basedpyright)
- Rust compilation

## Documentation

- Main docs: `docs/` (MkDocs with Material theme, Zensical compatible)
- Build locally: `mkdocs serve`
- API reference auto-generated from docstrings

### Documentation Code Blocks

**CRITICAL**: All Python code blocks in documentation are tested as executable code in CI.

#### Testing Documentation
Python code blocks in the following files are automatically tested:
- `README.md`
- `docs/guide/writing-tests.md`
- `docs/guide/fixtures.md`
- `docs/guide/assertions.md`
- `docs/guide/test-classes.md`

#### Writing Testable Code Blocks

**Default Behavior**: All Python code blocks are executed as tests unless marked otherwise.

```python
# This will be executed and must work
from rustest import fixture

@fixture
def sample():
    return "test"

def test_example(sample):
    assert sample == "test"
```

#### Skipping Code Blocks

Use `<!--rustest.mark.skip-->` to skip code blocks that are examples only:

```markdown
<!--rustest.mark.skip-->
```python
# This is an example that won't be executed
assert value == expected  # These variables don't need to exist
```
```

!!! note "pytest compatibility"
    For compatibility with pytest-codeblocks, `<!--pytest.mark.skip-->` and `<!--pytest-codeblocks:skip-->` also work.

#### Guidelines for Documentation Code

1. **Make code executable**: All code blocks should be valid, runnable Python unless explicitly skipped
2. **Import everything needed**: Include all imports in the code block
3. **Use complete examples**: Provide full context so the code can execute standalone
4. **Skip conceptual examples**: Use `<!--rustest.mark.skip-->` for pseudo-code or incomplete snippets
5. **Test before committing**: Run `uv run python -m rustest README.md` locally

#### Testing Documentation Locally

```bash
# Test all documentation code blocks
uv run python -m rustest README.md
uv run python -m rustest docs/guide/fixtures.md -v

# Test entire directories
uv run python -m rustest docs/guide/
uv run python -m rustest README.md docs/guide/ docs/api/
```

#### Common Patterns

**Good - Executable example:**
```python
from rustest import fixture

@fixture
def database():
    return {"connected": True}

def test_connection(database):
    assert database["connected"] is True
```

**Needs skip marker - Pseudo-code:**
```markdown
<!--rustest.mark.skip-->
```python
# Conceptual example
result = expensive_operation()
if not result.is_valid():
    fail("Operation failed")
```
```

**Why This Matters**: Testing documentation ensures:
- Examples actually work and don't mislead users
- Breaking changes to the API are caught immediately
- Documentation stays in sync with the codebase
