# CLAUDE.md

This file provides guidance for Claude Code when working with the rustest codebase.

## Project Overview

**rustest** is a Rust-powered pytest-compatible test runner focused on raw performance, with familiar pytest ergonomics.

**The measured figures**, for anything that needs to state one. Do not invent a headline
multiplier; these are what the tree can defend:

- **1.1x–5.7x** wall-clock across seventeen real open-source pytest suites (13 MATCH /
  4 EXPLAINED / 0 DIVERGE). Aggregate over all seventeen **1.23x**; over the fifteen that
  are not body-bound, **2.74x**.
- **~37x** on warm collection and **~8x** on marginal per-test overhead (500 files /
  5,000 tests: 8.39s → 227.6ms, and 933.6µs → 117.9µs per test).
- Any speedup is bounded by a suite's *framework share* — the fraction of wall clock that
  is not the user's own test bodies. `user_guide/performance.md` carries the per-suite
  table and every caveat, including that the marginal-overhead metric is noisy enough on a
  loaded machine that a single reading of it must not be quoted as a gate result.

The old "8.5x average, up to 19x" line was a **v1** measurement; v1 was deleted in Phase 4
Task 2 and the number describes a runner that no longer exists. It is gone from every page.

- **Languages**: Rust (core engine) + Python (user API/CLI)
- **Build System**: Maturin (PyO3 bridge for Rust-Python integration)
- **Python Support**: 3.12 - 3.14
- **License**: MIT

## Project Structure

```
src/                          # Rust core (rustest-core crate)
├── lib.rs                    # PyO3 boundary -- four functions, nothing else
└── v2/                       # The engine
    ├── config.rs             # rootdir + ini resolution (pytest's rules)
    ├── collect.rs            # The file walk and the worker pool
    ├── static_collect.rs     # Tier S: AST collection without importing
    ├── manifest.rs           # Collection output as data
    ├── manifest_cache.rs     # Tier S's content-addressed cache
    ├── execute.rs            # Run orchestration, the schema-v2 report
    ├── selection.rs          # -k / -m expression engine
    ├── nodeid.rs             # pytest-byte-identical node ids
    ├── protocol.rs           # The orchestrator <-> worker wire
    ├── cache.rs              # --lf / --ff store
    └── py.rs                 # The PyO3 functions themselves

python/rustest/               # Python package (user API)
├── __init__.py               # Public API exports (lazy)
├── __main__.py               # CLI entry point
├── decorators.py             # @fixture, @parametrize, @mark, outcome exceptions
├── _v2_worker.py             # The worker: collection + execution inside Python
├── _v2_builtins.py           # The engine's builtin fixtures (pytest ports)
├── builtin_fixtures.py       # Public fixture TYPES + MonkeyPatch
├── cli.py                    # Command-line interface
├── core.py                   # Wrapper around the Rust layer; `run` lives here
├── approx.py                 # Numeric comparison helper
├── _pytest_stub/             # `_pytest.*` import surface (aliases, not forks)
└── compat/pytest.py          # pytest compatibility layer

python/tests/                 # Python unit tests
tests/                        # Integration test suite
examples/tests/               # Example test suite
user_guide/                   # The documentation site's content (flat, .md)
great-docs.yml                # The documentation site's config
docs/superpowers/             # Internal SDD artifacts -- NOT site content
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

# Run with rustest itself -- there is one engine
uv run python -m rustest tests/ examples/tests/ -v

# Rust tests. TWO flags, both required.
#
# `--no-default-features` turns OFF `extension-module`. That feature is correct for the
# wheel -- it leaves the CPython symbols undefined for the host interpreter to supply -- but
# a test binary is an ordinary executable with no host, so on Linux it fails to LINK, with a
# wall of `undefined symbol: PyObject_GetAttr`-style errors. Windows links `pythonXY.lib`
# either way, so omitting the flag appears to work locally and breaks CI.
#
# `--test-threads=1` is required, not optional: these drive real Python worker pools, and
# running them concurrently produces spurious subprocess timeouts.
cargo test --no-default-features -- --test-threads=1

# ON WINDOWS, the test binary needs the Python DLL directory on PATH or it exits
# 0xc0000135 (STATUS_DLL_NOT_FOUND) before running anything -- it links pythonXY.dll and
# does not find it on a bare PATH. This is not a build failure and not a regression; it
# looks like a crash and costs ten minutes if you do not know. Prepend the uv-managed
# interpreter's directory, e.g.:
#   $env:PATH = "$HOME\AppData\Roaming\uv\python\cpython-3.14.2-windows-x86_64-none;$env:PATH"
# Adjust the version to whatever `uv python list` says the project resolves.

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
poe tests     # Run integration and example tests
poe docs      # Preview the docs site locally (great-docs preview)
poe docs-build # Build the docs site into great-docs/_site
```

### Pre-commit repair (uv-managed CPython)

If `pre-commit` cannot bootstrap any hook environment — the symptom is a `virtualenv`
failure rooted in `import ssl`, because the uv-managed interpreter is missing
`libcrypto-3-x64.dll` — the repair is to reinstall that interpreter:

```bash
uv python install --reinstall cpython-3.14.2
uv run pre-commit run --all-files
```

This is machine-global and rebuilds the project venv, so it is not something to do in the
middle of another task. Do **not** paper over it by copying a DLL from a sibling
interpreter: the freethreaded and standard builds ship different OpenSSL builds.

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
1. **Rust Core** (`src/v2/`) - High-performance engine for:
   - Config resolution and the file walk
   - Static (AST) collection and its manifest cache
   - Orchestrating the spawn-based worker pool and reporting

2. **Python Layer** (`python/rustest/`) - User-friendly API for:
   - Decorators (`@fixture`, `@parametrize`, `@mark`)
   - Built-in fixtures
   - CLI interface
   - pytest compatibility

3. **PyO3/Maturin Bridge** - Compiled Rust exposed as `rustest.rust` module

### Key Entry Points
- CLI: `python -m rustest` → `__main__.py` → `cli.py:main()`
- Python API: `from rustest import fixture, mark, parametrize`
- Test Discovery: `src/v2/collect.rs:collect()`
- Test Execution: `src/v2/execute.rs:run()`
- Worker (Python side): `python/rustest/_v2_worker.py`

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
- `cargo test --no-default-features -- --test-threads=1` - Rust unit tests (both flags required; see above)
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
2. **rustest** - The project's own test runner. `rustest <paths>` runs the engine with the
   pytest compatibility shim always installed. `--pytest-compat` and `--v1` were both
   removed -- passing either exits 4 with a message naming the change.
3. **Documentation tests** - Python code blocks in `README.md` and `user_guide/*.md`

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
2. Run `cargo test --no-default-features -- --test-threads=1` for Rust tests
3. Run `uv run maturin develop` to rebuild
4. Run Python tests to verify integration

## CI/CD Pipeline

The CI workflow (`ci.yml`) runs all checks across Python 3.12-3.14. **ALL must pass**:

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

The site is built by **great-docs** (Quarto-based), configured in `great-docs.yml`. It
replaced zensical/MkDocs; there is no `zensical.toml` and no `mkdocs.yml`.

- Content: `user_guide/` — a **flat** directory of `.md` files. Flat is great-docs' design
  (it globs one level and copies by basename), which is why the six beginner pages carry
  an `intro-` prefix instead of living in a subdirectory.
- Config + nav + API-reference discovery: `great-docs.yml`
- Landing page: `README.md` (great-docs generates the site index from it)
- API reference: **auto-generated** from `python/rustest` docstrings by the `reference:`
  key. The five hand-written `docs/api/*.md` pages are gone — do not re-add them.
- Build: `poe docs-build` · Preview: `poe docs` — both go through `scripts/docs.sh`
- Prerequisites: the **Quarto CLI** on PATH (`winget install --id Posit.Quarto -e` on
  Windows), plus a `.venv-docs` the script bootstraps
- `docs/superpowers/` is internal SDD material, not site content, and is not built

**The docs toolchain is deliberately not in `.venv`.** great-docs pulls jupyter, which
pulls anyio, which registers a **pytest plugin** — and the conformance gates run real
pytest out of `.venv` and must keep loading exactly what an unpolluted pytest loads. That
is why it is a `[dependency-groups] docs` group rather than an extra, and why
`scripts/docs.sh` uses a separate environment.

**Why the pages are `.md` and not `.qmd`.** great-docs and Quarto render both. rustest's
own doc-code-block collector keys on `.md` (`src/v2/collect.rs::is_markdown`), so
authoring in `.md` is what keeps every example on the site executing as a test in CI —
verified directly: a `.qmd` passed to rustest reports "found no collectors". The two
systems do not collide, because Quarto's *executable* fences are spelled ```` ```{python} ````
and this collector matches only ```` ```python ````.

### Documentation Code Blocks

**CRITICAL**: Every Python code block in documentation is executed as a test in CI unless
explicitly skip-marked, and a `def test_*` inside one really runs, as its own node — not
defined and silently discarded.
The mechanism is the same collector a `.py` test file goes through: a block's source execs
into a fresh module at collect time and that module is enumerated exactly as a file would
be, so fixtures, `@parametrize`, `Test*` classes and xunit-style `setup_function` all work
inside a block with no special-casing.

**This tier is off by default.** Nothing named `.md` collects unless something turns it on:
`--codeblocks` on the command line, or `codeblocks = true` in `[tool.rustest]` (or the
pytest ini section) in `pyproject.toml`. **This repository turns it on** — see
`pyproject.toml`'s `[tool.rustest]` table — which is exactly why every command below in this
section works with no flag: run it from a clone that has not set the key and the same
command is a usage error, exit 4, `found no collectors for README.md`. See
`user_guide/markdown-testing.md` for the full config precedence and the CLI flag pair.

#### Testing Documentation
CI runs `rustest README.md user_guide/*.md` — that is, **every page on the site**, not a
subset. Each block executes in its own fresh namespace, so every block must import
everything it uses.

#### Writing Testable Code Blocks

**Default Behavior**: every Python code block is collected and executed as a test unless
marked otherwise. A block with no `def test_*` in it is one node, passing if its top-level
statements — including a bare `assert` — run cleanly. A block that defines one or more
`def test_*` functions produces **one node per function**, and those functions are called
for real:

```python
# This will be executed and must work
from rustest import fixture

@fixture
def sample():
    return "test"

def test_example(sample):
    assert sample == "test"
```

One consequence worth knowing before it surprises you: a block's own top-level code (outside
any `def`) runs at **collect** time, the same moment a `.py` file's module-level code runs.
`-m`/`-k` deselection and `--lf` decide which *tests* execute, not whether a block's
top-level statements already ran — and a conftest's autouse fixture reaches the `def test_*`
functions inside a block but never the block's own top-level statements, consistent with
`.py` semantics where module-level code has never had autouse applied either.

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
# One page, verbosely
uv run python -m rustest user_guide/fixtures.md -v

# The exact CI line -- markdown must be NAMED, not walked: a directory argument collects
# no `.md` (pytest walking the same tree collects none either).
uv run python -m rustest README.md user_guide/*.md
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
