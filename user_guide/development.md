# Development Guide

This guide is for people contributing to rustest. You do not need to know Rust to be
useful here: most of the user-facing surface is Python, and the Rust core is a small,
heavily commented crate.

## Prerequisites

### 1. Rust

Install Rust using rustup, the official installer:

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Add to PATH
source $HOME/.cargo/env

# Verify installation
rustc --version
cargo --version
```

!!! info "What is Rust?"
    Rust is a systems programming language with memory safety and no garbage collector. In
    rustest it handles config resolution, the file walk, static collection and the worker
    pool; Python provides the API you write tests against.

### 2. uv

Install uv, the Python package manager this project uses:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify installation
uv --version
```

!!! info "What is uv?"
    A faster replacement for pip and virtualenv. It manages Python dependencies, the
    virtual environment, and the interpreters themselves.

### 3. Python 3.12-3.14

```bash
# uv manages the interpreter, so ask uv which ones it has
uv python list
```

Any of 3.12, 3.13 or 3.14 works. `uv sync` picks one and `uv run python --version` tells
you which it settled on.

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

That covers testing, linting and type checking. It deliberately leaves out the
documentation toolchain, which lives in its own environment for a reason described under
[Building Documentation Locally](#building-documentation-locally).

### 3. Build the Rust Extension

```bash
# Compiles Rust code and installs as Python module
uv run maturin develop
```

This compiles `src/` into a native extension and installs it into the virtual environment
as `rustest.rust`. The first build takes a minute or two; later ones are incremental.

!!! warning "Common Issue"
    Errors about a missing Rust toolchain mean step 1 did not finish. Run
    `source $HOME/.cargo/env` and try again.

### 4. Verify Everything Works

```bash
# Run example tests
uv run rustest examples/tests/

# Run Python unit tests
uv run poe pytests

# Run Rust tests (both flags are required, see below)
cargo test --no-default-features -- --test-threads=1
```

On Windows the last one needs the Python DLL directory on `PATH` first, or it exits before
running anything. See [Running the Rust tests](#running-the-rust-tests).

## Project Structure

Rustest is a hybrid Python/Rust project:

```
rustest/
├── src/                          # Rust core (the rustest-core crate)
│   ├── lib.rs                    # The PyO3 boundary: four functions, nothing else
│   └── v2/                       # The engine
│       ├── config.rs             # rootdir + ini resolution, following pytest's rules
│       ├── collect.rs            # The file walk and the worker pool
│       ├── static_collect.rs     # Tier S: AST collection without importing
│       ├── manifest.rs           # Collection output as data
│       ├── manifest_cache.rs     # Tier S's content-addressed cache
│       ├── execute.rs            # Run orchestration and the schema-v2 report
│       ├── selection.rs          # The -k / -m expression engine
│       ├── nodeid.rs             # pytest-byte-identical node ids
│       ├── protocol.rs           # The orchestrator/worker wire
│       ├── cache.rs              # The --lf / --ff store
│       └── py.rs                 # The PyO3 functions themselves
│
├── python/rustest/               # Python package (the user-facing API)
│   ├── __init__.py               # Public API, exported lazily
│   ├── __main__.py               # CLI entry point
│   ├── decorators.py             # @fixture, @parametrize, @mark, outcome exceptions
│   ├── _v2_worker.py             # The worker: collection and execution inside Python
│   ├── _v2_builtins.py           # The engine's builtin fixtures, ported from pytest
│   ├── builtin_fixtures.py       # Public fixture types and MonkeyPatch
│   ├── cli.py                    # Command-line interface
│   ├── core.py                   # Wrapper around the Rust layer; run() lives here
│   ├── approx.py                 # Numeric comparison helper
│   ├── _pytest_stub/             # The _pytest.* import surface
│   └── compat/pytest.py          # The pytest compatibility layer
│
├── python/tests/                 # Python unit tests
├── tests/                        # Integration tests
├── examples/tests/               # Example test suite
├── conformance/                  # The pytest conformance corpus and its harness
│
├── Cargo.toml                    # Rust dependencies
└── pyproject.toml                # Python dependencies and the poe tasks
```

The Rust side does discovery, config and orchestration. The Python side provides the
decorators, the builtin fixtures and the worker that actually imports and runs your tests.
PyO3 and maturin connect the two.

## Development Tasks

Tasks run through `poe` (poethepoet), and are defined under `[tool.poe.tasks]` in
`pyproject.toml`. `uv sync` installs poethepoet into `.venv` rather than onto your `PATH`,
so prefix these with `uv run` (`uv run poe dev`) unless you have activated the virtual
environment or installed poethepoet globally:

| Command | What it does | When to use it |
|---------|-------------|----------------|
| `poe dev` | Rebuild the Rust extension (`maturin develop`) | After changing `.rs` files |
| `poe pytests` | Run `python/tests` under pytest | After changing Python code |
| `poe lint` | `ruff check` over `python` and `conformance` | Before committing |
| `poe typecheck` | `basedpyright` over `python` and `conformance` | Before committing |
| `poe fmt` | `cargo fmt` | Before committing Rust changes |
| `poe tests` | Run `tests/` and `examples/tests/` under rustest | Verify end-to-end behaviour |
| `poe build` | `uv build` | Building a distribution |
| `poe docs` | Preview the documentation site | Editing `user_guide/` |
| `poe docs-build` | Build the documentation site | Checking the site compiles |
| `cargo check` | Fast-check that Rust compiles | While developing Rust code |

### Running the Rust tests

`cargo test` on its own does not work. Two flags are required, and neither is optional:

```bash
cargo test --no-default-features -- --test-threads=1
```

`--no-default-features` turns off the `extension-module` feature. That feature is correct
for the wheel, where it leaves the CPython symbols undefined for the host interpreter to
supply, but a test binary is an ordinary executable with no host. On Linux it therefore
fails to link, with a wall of `undefined symbol: PyObject_GetAttr` errors. Windows links
`pythonXY.lib` either way, which is why omitting the flag appears to work locally and then
breaks CI.

`--test-threads=1` is required because these tests drive real Python worker pools. Running
them concurrently puts several pools on one machine at once and produces spurious
subprocess timeouts.

!!! warning "Windows: the test binary needs the Python DLL directory on PATH"
    Without it the binary exits `0xc0000135` (`STATUS_DLL_NOT_FOUND`) before running a
    single test. It links `pythonXY.dll` and does not find it on a bare PATH. This looks
    like a crash and is not one. Prepend the uv-managed interpreter's directory:

    ```powershell
    $env:PATH = "$HOME\AppData\Roaming\uv\python\cpython-3.14.2-windows-x86_64-none;$env:PATH"
    ```

    Adjust the version to whatever `uv python list` says the project resolves.

## Pre-commit Hooks

### Setup (One-time)

```bash
# Install pre-commit hooks
uv run pre-commit install
```

### What it does

On every `git commit`, pre-commit runs:

- `ruff format` and `ruff check --fix` over `python/` and `conformance/`
- `basedpyright` over the same two directories
- `cargo fmt` and `cargo clippy --lib -- -D warnings` when Rust files changed
- A copy of the root `CHANGELOG.md` into `user_guide/changelog.md` when the changelog changed
- YAML and TOML syntax checks, trailing-whitespace trimming, end-of-file fixing,
  merge-conflict markers, and case-conflicting filenames

A failing check blocks the commit.

The ruff and basedpyright versions in `.pre-commit-config.yaml` are pinned to the versions
`uv.lock` resolves. Bumping one means bumping both, or the hook and the CI step disagree
about formatting and each undoes the other.

### Manual Usage

```bash
# Run all hooks on all files
uv run pre-commit run --all-files

# Run hooks only on staged files
uv run pre-commit run

# Skip hooks for a single commit (not recommended)
git commit --no-verify
```

### If pre-commit cannot bootstrap a hook environment

The symptom is a `virtualenv` failure rooted in `import ssl`, because the uv-managed
interpreter is missing `libcrypto-3-x64.dll`. Reinstall that interpreter:

```bash
uv python install --reinstall cpython-3.14.2
uv run pre-commit run --all-files
```

This is machine-global and rebuilds the project venv, so do not do it in the middle of
another task. Do not work around it by copying a DLL from a sibling interpreter either:
the freethreaded and standard builds ship different OpenSSL builds.

## Typical Workflow

```bash
# 1. Make your changes to Python or Rust files

# 2. If you changed Rust code, rebuild:
poe dev

# 3. Run tests:
poe pytests                                          # Python tests
cargo test --no-default-features -- --test-threads=1 # Rust tests

# 4. Commit your changes (pre-commit runs automatically):
git add .
git commit -m "Your message"
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
vim src/v2/collect.rs

# 2. Rebuild the extension
poe dev

# 3. Run Rust tests
cargo test --no-default-features -- --test-threads=1

# 4. Format code
poe fmt
```

## Testing Your Changes

### Python Tests

```bash
# Run Python unit tests
poe pytests

# Run integration and example tests
poe tests

# Run a specific test file
uv run rustest python/tests/test_decorators.py -v

# Run a specific test
uv run rustest python/tests/test_decorators.py -v -k test_fixture_marks_callable
```

The integration suites are run twice in CI, once under real pytest and once under rustest,
because agreement between the two runners is the property being tested:

```bash
uv run pytest tests/ examples/tests/ -v
uv run python -m rustest tests/ examples/tests/ -v
```

CI also runs every page of the documentation site. Markdown files have to be named rather
than walked:

```bash
uv run python -m rustest README.md user_guide/*.md
```

**Know what this gate does and does not catch.** Each `python` block runs as one test, and
that test executes the block **at module level**. Imports run, class and function bodies
are compiled, top-level statements execute. A `def test_*` inside the block is *defined and
never called*, so nothing inside it is checked:

<!--rustest.mark.skip-->
```python
# This block PASSES. The body never runs.
def test_never_runs():
    assert False
```

So the gate catches import errors, syntax errors, bad signatures and anything asserted at
top level. It does not catch a wrong assertion inside a `def test_*`, which is where most
examples put theirs. When an example's behaviour matters, verify it by copying the block
into a real test file and running that file, rather than trusting a green docs run.

### Rust Tests

The Rust tests all live in `#[cfg(test)]` modules inside `src/`, so there is one binary and
`--lib` selects the same set as the bare invocation.

```bash
# Run all Rust tests
cargo test --no-default-features -- --test-threads=1

# Run tests with output visible
cargo test --no-default-features -- --test-threads=1 --nocapture

# Run a specific test
cargo test --no-default-features walk_is_name_sorted_and_depth_first -- --test-threads=1
```

## Understanding the Rust↔Python Bridge

Three pieces:

1. **PyO3** is the Rust library that lets Rust code hold and call Python objects.
2. **Maturin** compiles the crate into a Python extension module, installed as
   `rustest.rust`.
3. `src/lib.rs` is the whole boundary. It registers four `#[pyfunction]`s and does nothing
   else; `python/rustest/core.py` is what calls them.

The four are `v2_resolve_config`, `v2_collect`, `v2_run` and `v2_static_stdlib_allowlist`.
The last two of those are debug surfaces, exposed so their answers can be checked against
a real interpreter rather than trusted.

**Example:**

```rust
// In src/v2/py.rs - this Rust function...
#[pyfunction]
pub fn v2_resolve_config(invocation_dir: &str, args: Vec<String>) -> PyResult<String> {
    // Fast Rust code here
}
```

```python
# ...can be called from Python:
from pathlib import Path

from rustest.rust import v2_resolve_config

# invocation_dir must be absolute; a relative path raises ValueError.
config_json = v2_resolve_config(str(Path.cwd()), ["tests"])
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

### "cargo test" fails with undefined CPython symbols

**Problem:** `--no-default-features` was omitted, so `extension-module` is on and the test
binary has no host interpreter to supply the symbols.

**Solution:** Run `cargo test --no-default-features -- --test-threads=1`. On Linux you may
also need Python development headers: `sudo apt-get install python3-dev`.

### "cargo test" exits 0xc0000135 on Windows without running anything

**Problem:** The test binary cannot find `pythonXY.dll`.

**Solution:** Prepend the uv-managed interpreter's directory to `PATH`, as shown under
[Running the Rust tests](#running-the-rust-tests).

### Tests pass locally but fail in CI

**Problem:** The Rust extension is stale relative to `src/`.

**Solution:** Run `poe dev` to rebuild it.

## Getting Help

- **Rust documentation:** https://doc.rust-lang.org/book/
- **PyO3 guide:** https://pyo3.rs/
- **rustest issues:** https://github.com/Apex-Engineers-Inc/rustest/issues

!!! tip "For Python developers new to Rust"
    Start with Python-side changes: decorators, the CLI, the builtin fixtures. Those are
    where most of the user-visible behaviour lives. When you do open a Rust file, read the
    module doc comment first; each one names the pytest source it ports and why.

## Documentation

### Updating CLI Documentation

If you change CLI arguments, update the documentation:

```bash
# Automatically update CLI help in docs
uv run python scripts/update_cli_docs.py
```

This script captures the output of `rustest --help` and updates the "Quick Reference" block
in `user_guide/cli.md`. The flags **table** further down that page is hand-maintained, so
adding or removing a flag means editing both.

### Building Documentation Locally

The site is built by [great-docs](https://posit-dev.github.io/great-docs/), which shells
out to Quarto. Two prerequisites:

**1. The Quarto CLI**, which is not a Python package, so uv cannot install it:

<!--rustest.mark.skip-->
```bash
winget install --id Posit.Quarto -e     # Windows
brew install --cask quarto              # macOS
# Linux: https://quarto.org/docs/get-started/
```

**2. Nothing else.** `scripts/docs.sh` bootstraps a `.venv-docs` on first run:

<!--rustest.mark.skip-->
```bash
uv run poe docs         # starts a local preview server
uv run poe docs-build   # writes great-docs/_site
```

Both forward to `great-docs` inside `.venv-docs`, which `scripts/docs.sh` creates and
installs into on first run.

The docs toolchain lives in `.venv-docs` rather than `.venv` on purpose. great-docs depends
on jupyter, which depends on anyio, which registers a pytest plugin, and the conformance
gates run real pytest out of `.venv` and must keep loading exactly what an unpolluted
pytest loads. That is also why it is declared as a `[dependency-groups] docs` group rather
than an extra, so `uv sync --all-extras` leaves it out.

### Where documentation lives

| Path | What |
|---|---|
| `user_guide/*.md` | Every page on the site. Flat, because great-docs globs one level and copies by basename, which is why the beginner pages are `intro-*.md` |
| `great-docs.yml` | Config, nav ordering, and the auto-discovered API reference |
| `README.md` | The site's landing page |
| `docs/superpowers/` | Internal engineering artifacts. Not site content, not built |

The API reference is generated from `python/rustest` docstrings, so there are no
hand-written API pages to update. Improve a docstring and the page follows.

Pages are authored in `.md`, not `.qmd`, and that is load-bearing. great-docs and Quarto
render both, but rustest's own code-block collector only claims `.md`
(`src/v2/collect.rs::is_markdown`), so keeping the extension is what keeps every example on
the site reaching the CI gate at all (with the limits on that gate described under
[Python Tests](#python-tests)). Quarto's executable fences are spelled ```` ```{python} ````,
which the collector deliberately does not match.

## Quick Reference

```bash
# Setup (first time only)
uv sync --all-extras
uv run maturin develop
uv run pre-commit install

# Daily development
poe dev          # Rebuild Rust after changes
poe pytests      # Run Python tests
cargo test --no-default-features -- --test-threads=1

# Update docs after CLI changes
uv run python scripts/update_cli_docs.py

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

Bug fixes, features, documentation, performance work and test improvements are all welcome.
Behaviour changes that touch pytest compatibility should come with a conformance corpus
case, so the new behaviour cannot regress unnoticed.

## See Also

- [Performance](performance.md) - Understanding rustest's speed
- [Comparison with pytest](comparison.md) - Feature compatibility
- [API Reference](../reference/index.html) - Code you might modify
