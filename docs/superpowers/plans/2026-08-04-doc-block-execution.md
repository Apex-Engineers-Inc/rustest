# Documentation Code Block Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make documentation code blocks execute for real, so a `def test_*` written inside a block actually runs, and put that behaviour behind a setting that is off by default.

**Architecture:** A block stops being compiled into `def run_codeblock():` and is instead executed at module level into a fresh module object, which is then handed to `collect_module`, the same enumerator a `.py` test file goes through. The block's identity travels in a new `block_segment` field that reaches the node id and the qualname but deliberately never reaches `class_name`. Collection of `.md` files flips to off by default, enabled by `--codeblocks` or a `codeblocks` key readable from either `[tool.rustest]` or pytest's ini section.

**Tech Stack:** Rust (`src/v2/`, rustfmt + clippy), Python 3.12-3.14 (`python/rustest/`, ruff + basedpyright), PyO3/maturin bridge.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-04-doc-block-execution-design.md`. Read it before Task 1. Every decision below traces to it.
- **Rust tests need BOTH flags:** `cargo test --no-default-features -- --test-threads=1`. Omitting `--no-default-features` fails to link on Linux; omitting `--test-threads=1` produces spurious subprocess timeouts.
- **On Windows the Rust test binary needs the Python DLL directory on PATH** or it exits `0xc0000135` before running anything. Prepend the uv-managed interpreter directory, e.g. `$env:PATH = "$HOME\AppData\Roaming\uv\python\cpython-3.14.2-windows-x86_64-none;$env:PATH"`.
- **Rebuild after Rust changes:** `uv run maturin develop`.
- **Before every commit:** `cargo fmt` and `cargo clippy --lib -- -D warnings` for Rust changes; `uv run ruff format python && uv run ruff check python && uv run basedpyright python` for Python changes.
- **Never change code inside ` ```python ` fences in `README.md` or `user_guide/*.md`** except where a task says so explicitly. CI executes them.
- **The docs gate must stay green throughout.** Baseline as of this plan: `uv run python -m rustest README.md user_guide/*.md` reports `303 passed, 83 skipped`. Task 2 both flips the default and enables the setting for this repo, so the gate never goes vacuous mid-plan.
- **The only defensible performance figures** are the ones in `CLAUDE.md`. Do not invent any.

---

## Execution: two independent chains

These tasks form two chains that share no file, so they can run concurrently:

- **Chain R (Rust + CLI):** Task 1 -> Task 2 -> Task 3. Touches `src/v2/config.rs`,
  `src/v2/collect.rs`, `pyproject.toml`, `python/rustest/cli.py`, `python/rustest/core.py`,
  `python/tests/test_codeblock_switch.py`.
- **Chain P (Python worker):** Task 4 -> Task 5 -> Task 6. Touches
  `python/rustest/_v2_worker.py` and `python/tests/test_codeblock_execution.py`. Deliberately
  no Rust, so it never contends with Chain R's cargo builds.
- **Task 7 converges** and requires both chains complete.

Within a chain the order is strict. Chain P's tests pass both before and after Chain R's
default flip, because they write `[tool.rustest] codeblocks = true` into their own
`tmp_path` project and the pre-flip default is on anyway.

Rules for concurrent execution:

1. **Commit what your change forces to compile, but never the other chain's files.** Chain R
   must not touch `python/rustest/_v2_worker.py` or `python/tests/test_codeblock_execution.py`;
   Chain P must not touch any Rust file. Everything else a change legitimately requires is
   fair game. Name the files; never `git add -A`.
2. If `git commit` fails on `.git/index.lock`, wait a moment and retry rather than clearing
   the lock.
3. **Integration-level verification belongs at wave boundaries, run by the controller, not
   inside a concurrently-running task.** The chains share no file but they do share the
   *built artifact*: while Chain R is mid-edit on `src/v2/`, `maturin develop` cannot
   succeed, so any full-suite or docs-gate result Chain P collects is measuring Chain R's
   half-finished state. The reverse is equally true, since Chain R's docs gate imports Chain
   P's worker. Inside a task, run only the targeted tests that exercise that task's change.
   The controller runs `uv run pytest python/tests -q`, `uv run pytest tests/ examples/tests/ -q`
   and the docs gate once both chains are between tasks and the tree is consistent.

   This applies to Task 2 Step 5 and Task 5 Step 5 in particular: both call for a full
   `maturin develop` plus gate run. Under concurrent execution, do the build, confirm the
   task's own targeted tests, and leave the gate to the wave boundary.

## File Structure

**Rust**

| File | Responsibility | Change |
| --- | --- | --- |
| `src/v2/config.rs` | rootdir + ini resolution | Add a boolean ini getter and the out-of-band `[tool.rustest]` lookup; add `codeblocks` to `ResolvedConfig` |
| `src/v2/collect.rs` | file walk, `CollectOptions`, `discover` | Flip `CollectOptions::new()` to `codeblocks: false`; make the flag tri-state so config can decide; resolve config before the markdown-target decision |

**Python**

| File | Responsibility | Change |
| --- | --- | --- |
| `python/rustest/_v2_worker.py` | collection + execution | The whole execution-model change: `block_segment` plumbing, module-level exec, four-step registration, the failure model |
| `python/rustest/cli.py` | CLI surface | `--codeblocks` / `--no-codeblocks` as a tri-state pair |
| `python/rustest/core.py` | `run()` wrapper | `codeblocks: bool \| None = None` |
| `python/tests/test_codeblock_switch.py` | new | Switch tests (Chain R) |
| `python/tests/test_codeblock_execution.py` | new | Execution + failure-model tests (Chain P) |
| `tests/test_codeblocks_integration.py` | new | End-to-end over a fixture `.md` |

**Docs**

| File | Change |
| --- | --- |
| `pyproject.toml` | `[tool.rustest] codeblocks = true` (Task 2, keeps the gate real) |
| `user_guide/markdown-testing.md` | Rewrite: the mechanism, the flag, the config key |
| `user_guide/cli.md` | The flag pair and the default flip |
| `CLAUDE.md` | L329/L338/L412 become accurate rather than a walk-back |
| `CHANGELOG.md` | Six breaking-change entries (synced to `user_guide/changelog.md` by pre-commit) |

---

### Task 1: Boolean ini getter and the out-of-band `[tool.rustest]` lookup

**Files:**
- Modify: `src/v2/config.rs` (add `getini_bool`, `read_tool_rustest_codeblocks`, and a `codeblocks` field on `ResolvedConfig`)
- Test: `src/v2/config.rs` (inline `#[cfg(test)]` module, matching the file's existing convention)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ResolvedConfig.codeblocks: Option<bool>`. `None` means "no config opinion, CLI or the built-in default decides". `getini_bool(cfg: &ConfigDict, name: &str) -> Option<bool>`.

`Option<bool>` rather than `bool` is load-bearing: the CLI must be able to override config, and a bare `bool` cannot distinguish "config said false" from "config said nothing".

- [ ] **Step 1: Write the failing tests**

Add to the `#[cfg(test)]` module in `src/v2/config.rs`:

```rust
#[test]
fn getini_bool_accepts_pytest_ini_spellings() {
    let mut cfg = ConfigDict::new();
    cfg.insert("a".to_string(), "true".to_string());
    cfg.insert("b".to_string(), "False".to_string());
    cfg.insert("c".to_string(), "1".to_string());
    cfg.insert("d".to_string(), "no".to_string());
    cfg.insert("e".to_string(), "banana".to_string());
    assert_eq!(getini_bool(&cfg, "a"), Some(true));
    assert_eq!(getini_bool(&cfg, "b"), Some(false));
    assert_eq!(getini_bool(&cfg, "c"), Some(true));
    assert_eq!(getini_bool(&cfg, "d"), Some(false));
    assert_eq!(getini_bool(&cfg, "e"), None);
    assert_eq!(getini_bool(&cfg, "missing"), None);
}

/// `[tool.rustest]` is read from EXACTLY `<rootdir>/pyproject.toml`, with no walk in
/// either direction, so a table in a subdirectory is invisible.  Spec: "Exactly at
/// rootdir is literal".
#[test]
fn tool_rustest_is_read_only_at_the_rootdir() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(
        root.join("pyproject.toml"),
        "[tool.rustest]\ncodeblocks = true\n",
    )
    .unwrap();
    assert_eq!(read_tool_rustest_codeblocks(root), Some(true));

    let sub = root.join("sub");
    std::fs::create_dir(&sub).unwrap();
    std::fs::write(
        sub.join("pyproject.toml"),
        "[tool.rustest]\ncodeblocks = true\n",
    )
    .unwrap();
    // rootdir is still `root`; the subdirectory table must not be consulted.
    assert_eq!(read_tool_rustest_codeblocks(root), Some(true));
    // And a rootdir with no table at all answers None rather than a default.
    let empty = tempfile::tempdir().unwrap();
    assert_eq!(read_tool_rustest_codeblocks(empty.path()), None);
}

/// The case that was silently broken before the spec was amended: a project whose
/// config file is `pytest.ini` must still have its `[tool.rustest]` table honoured,
/// even though `pyproject.toml` never becomes the config file.
#[test]
fn tool_rustest_is_honoured_when_pytest_ini_is_the_config_file() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(root.join("pytest.ini"), "[pytest]\n").unwrap();
    std::fs::write(
        root.join("pyproject.toml"),
        "[tool.rustest]\ncodeblocks = true\n",
    )
    .unwrap();
    let resolved = resolve_config(root, &[]).unwrap();
    assert_eq!(resolved.config_file, Some(root.join("pytest.ini")));
    assert_eq!(resolved.codeblocks, Some(true));
}

/// Adding `[tool.rustest]` must not move rootdir.  If it did, every node id in the
/// suite could change from adding one key.
#[test]
fn tool_rustest_does_not_move_rootdir() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(root.join("pytest.ini"), "[pytest]\n").unwrap();
    let before = resolve_config(root, &[]).unwrap().rootdir;
    std::fs::write(
        root.join("pyproject.toml"),
        "[tool.rustest]\ncodeblocks = true\n",
    )
    .unwrap();
    let after = resolve_config(root, &[]).unwrap().rootdir;
    assert_eq!(before, after);
}
```

If `resolve_config` is not the exact name of the crate-internal resolver, use whatever the existing tests in this module call; do not invent a new entry point.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cargo test --no-default-features -- --test-threads=1 config::
```

Expected: FAIL, `cannot find function getini_bool` and `cannot find function read_tool_rustest_codeblocks`.

- [ ] **Step 3: Implement the getter and the lookup**

Add near the other `getini_*` helpers in `src/v2/config.rs`:

```rust
/// pytest's ini boolean parsing, for the one rustest-only key that uses it.
///
/// Source: `_pytest/config/__init__.py::Config._getini`, `type="bool"` branch, which
/// defers to `_strtobool`.  An unrecognised value answers `None` rather than raising:
/// this key is rustest-only, so a project that also runs real pytest may legitimately
/// have a value pytest itself would reject, and refusing the whole run over it would be
/// harsher than the feature warrants.
fn getini_bool(cfg: &ConfigDict, name: &str) -> Option<bool> {
    let raw = cfg.get(name)?.trim().to_ascii_lowercase();
    match raw.as_str() {
        "1" | "true" | "yes" | "on" => Some(true),
        "0" | "false" | "no" | "off" => Some(false),
        _ => None,
    }
}

/// Read `[tool.rustest] codeblocks` from **exactly** `<rootdir>/pyproject.toml`.
///
/// Deliberately outside config-file discovery.  `load_config_dict_from_file` answers
/// `None` for a `pyproject.toml` with no `[tool.pytest.ini_options]` table, and
/// `pytest.ini` outranks `pyproject.toml` in the same directory, so a project keeping its
/// settings in `pytest.ini` would never have this table read if it went through
/// `locate_config`.  No walk in either direction: an upward walk would read an unrelated
/// `pyproject.toml` above the repository.
fn read_tool_rustest_codeblocks(rootdir: &Path) -> Option<bool> {
    let text = std::fs::read_to_string(rootdir.join("pyproject.toml")).ok()?;
    let value: toml::Value = text.parse().ok()?;
    value
        .get("tool")?
        .get("rustest")?
        .get("codeblocks")?
        .as_bool()
}
```

Add the field to `ResolvedConfig`:

```rust
    /// `[tool.rustest] codeblocks`, or the pytest-ini-section spelling, or `None` when
    /// neither is set.  `None` is not `false`: it means "no config opinion", so the CLI
    /// flag and then the built-in default decide.
    pub codeblocks: Option<bool>,
```

Populate it where the other fields are built, preferring the dedicated table:

```rust
        codeblocks: read_tool_rustest_codeblocks(&rootdir)
            .or_else(|| getini_bool(&cfg, "codeblocks")),
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cargo fmt && cargo clippy --lib -- -D warnings && cargo test --no-default-features -- --test-threads=1 config::
```

Expected: PASS, clippy clean.

- [ ] **Step 5: Commit**

```bash
git add src/v2/config.rs
git commit -m "feat(config): read a codeblocks boolean from [tool.rustest] or the pytest ini section"
```

---

### Task 2: Flip the default, and keep this repo's gate real

**Files:**
- Modify: `src/v2/collect.rs` (`CollectOptions::new`, `plan_with_options`, `discover`)
- Modify: `pyproject.toml` (add `[tool.rustest] codeblocks = true`)
- Test: `src/v2/collect.rs` inline tests

**Interfaces:**
- Consumes: `ResolvedConfig.codeblocks: Option<bool>` from Task 1.
- Produces: `CollectOptions.codeblocks: Option<bool>`, tri-state: `None` meaning "not passed on the CLI, let config decide".

**Why `pyproject.toml` changes in this task and not the last one:** the moment the default flips, `rustest README.md user_guide/*.md` collects nothing and reports success. A vacuously green gate for the middle of this plan is exactly the failure mode the whole feature exists to remove.

- [ ] **Step 1: Write the failing tests**

Add to the `#[cfg(test)]` module in `src/v2/collect.rs`, alongside `a_markdown_argument_is_a_target_when_codeblocks_are_on`:

```rust
/// The default inverts: a markdown argument is a usage error unless codeblocks are
/// explicitly enabled.  This is pytest's answer, which is why it is now the default.
#[test]
fn a_markdown_argument_is_a_usage_error_by_default() {
    let tmp = tempfile::tempdir().unwrap();
    let md = tmp.path().join("notes.md");
    std::fs::write(&md, "```python\nassert True\n```\n").unwrap();
    let options = CollectOptions::new();
    let err = plan_with_options(tmp.path(), &[md], 1, &options).unwrap_err();
    assert!(
        format!("{err:?}").contains("found no collectors"),
        "unexpected error: {err:?}"
    );
}

/// `[tool.rustest] codeblocks = true` enables it with no CLI flag.
#[test]
fn config_alone_enables_codeblocks() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::write(
        tmp.path().join("pyproject.toml"),
        "[tool.rustest]\ncodeblocks = true\n",
    )
    .unwrap();
    let md = tmp.path().join("notes.md");
    std::fs::write(&md, "```python\nassert True\n```\n").unwrap();
    let options = CollectOptions::new(); // codeblocks: None
    let dispatch = plan_with_options(tmp.path(), &[md], 1, &options).unwrap();
    assert!(!dispatch.is_empty(), "config should have enabled markdown");
}

/// `--no-codeblocks` beats a config that turns them on.  This is the override the flag
/// exists for now that the default is off.
#[test]
fn cli_off_beats_config_on() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::write(
        tmp.path().join("pyproject.toml"),
        "[tool.rustest]\ncodeblocks = true\n",
    )
    .unwrap();
    let md = tmp.path().join("notes.md");
    std::fs::write(&md, "```python\nassert True\n```\n").unwrap();
    let options = CollectOptions {
        codeblocks: Some(false),
        ..CollectOptions::new()
    };
    let err = plan_with_options(tmp.path(), &[md], 1, &options).unwrap_err();
    assert!(format!("{err:?}").contains("found no collectors"));
}

/// A directory walk still finds no markdown, whatever the setting says.  Unchanged
/// behaviour, pinned so the flip does not accidentally widen the walk.
#[test]
fn a_directory_walk_finds_no_markdown_even_when_enabled() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::write(
        tmp.path().join("pyproject.toml"),
        "[tool.rustest]\ncodeblocks = true\n",
    )
    .unwrap();
    std::fs::write(tmp.path().join("notes.md"), "```python\nassert True\n```\n").unwrap();
    let options = CollectOptions::new();
    let dispatch = plan_with_options(tmp.path(), &[tmp.path().to_path_buf()], 1, &options);
    // No `.py` and no walked `.md` means nothing to collect.
    assert!(dispatch.is_err() || dispatch.unwrap().is_empty());
}
```

`dispatch.is_empty()` stands for whatever the existing tests use to assert an empty manifest; match the neighbouring tests rather than inventing an accessor.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cargo test --no-default-features -- --test-threads=1 collect::
```

Expected: FAIL. `a_markdown_argument_is_a_usage_error_by_default` fails because the default is still `true`; the config tests fail to compile because `codeblocks` is still `bool`.

- [ ] **Step 3: Make the flag tri-state and flip the default**

In `src/v2/collect.rs`, change the field:

```rust
    /// Collect python fences out of a `.md` file **named as an argument**.
    ///
    /// `None` means the CLI said nothing, so `[tool.rustest] codeblocks` or the pytest
    /// ini spelling decides, and the built-in default is **off**.  Off is pytest's
    /// answer: pytest collects no markdown at all, so a project that has not asked for
    /// this feature sees pytest's collection exactly.
    ///
    /// It has never applied to a *directory walk*: pytest collects no markdown, so
    /// walking one in meant `rustest tests/` found tests `pytest tests/` never sees.
    pub codeblocks: Option<bool>,
```

and the constructor:

```rust
    /// The production default: codeblocks **off** unless config or the CLI asks, both
    /// tiers, cache on, no selection.
    pub fn new() -> Self {
        Self {
            codeblocks: None,
            ..Self::default()
        }
    }
```

In `plan_with_options`, resolve config before deciding targets:

```rust
    let config = resolve_config(invocation_dir, args)?;
    let codeblocks = options
        .codeblocks
        .or(config.codeblocks)
        .unwrap_or(false);
    let targets = discover_targets(&config, invocation_dir, args, codeblocks)?;
```

`discover` currently resolves config internally and takes the flag as an argument, so split it: keep the config resolution where it is and pass the already-resolved `&ResolvedConfig` into the target half. This is the restructuring the spec's ordering note names. Do not duplicate config resolution; there must remain exactly one call.

- [ ] **Step 4: Enable the setting for this repository**

Add to `pyproject.toml`, at top level near the other `[tool.*]` tables:

```toml
# rustest's own settings.  `codeblocks` is off by default; this repository's CI gate is
# `rustest README.md user_guide/*.md`, so it must ask for the feature explicitly or the
# gate passes vacuously.
[tool.rustest]
codeblocks = true
```

- [ ] **Step 5: Run the tests and the gate**

```bash
cargo fmt && cargo clippy --lib -- -D warnings
cargo test --no-default-features -- --test-threads=1
uv run maturin develop
uv run python -m rustest README.md user_guide/*.md
```

Expected: Rust tests PASS. The docs gate still reports `303 passed, 83 skipped`. If it reports `no tests ran` or exits 4, the `pyproject.toml` key is not being read; fix that before continuing, because every later task depends on this gate being real.

- [ ] **Step 6: Commit**

```bash
git add src/v2/collect.rs pyproject.toml
git commit -m "feat(collect): codeblocks off by default, enabled by config or flag"
```

---

### Task 3: CLI and Python API tri-state

**Files:**
- Modify: `src/v2/py.rs:249` and `:362` (the two pyo3 signatures that hard-default `codeblocks=true`)
- Modify: `python/rustest/cli.py:355-361` (the `--no-codeblocks` argument)
- Modify: `python/rustest/core.py:322,736` (the `run()` signature and its forwarding)
- Test: `python/tests/test_codeblock_switch.py` (create)

**The pyo3 boundary is the load-bearing part of this task, and it is easy to miss.**

Task 2 flipped the Rust-side default, but the flip is **not reachable from Python** until this
task changes `src/v2/py.rs`. Both `v2_collect` and the `plan` entry point declare
`codeblocks=true` in their `#[pyo3(signature = ...)]` and type the parameter `codeblocks: bool`,
so the boundary coerces to a concrete bool and the `Option<bool>` tri-state never survives the
crossing. Verified empirically after Task 2 landed: in a scratch project with **no config at
all**, `rustest page.md` still collected and passed.

Change both to `Option<bool>` with a `None` default, and forward `None` through rather than
coercing it. Until that happens the feature is unreachable and, worse, this repository's own
docs gate is green because of the hard default rather than because `pyproject.toml` is read,
which is the exact vacuously-green condition this plan's ordering was written to prevent.

`v2_run` never builds a `CollectOptions` and does not need the change.

**Interfaces:**
- Consumes: the tri-state `CollectOptions.codeblocks` from Task 2.
- Produces: `run(..., codeblocks: bool | None = None)`. CLI parses to `None` when neither flag is passed.

- [ ] **Step 1: Write the failing tests**

Create `python/tests/test_codeblock_switch.py`:

```python
"""The codeblocks switch: CLI flag, config key, and their precedence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _md(tmp_path: Path, body: str, *, enable: bool = True) -> Path:
    """Write a one-page project whose config enables codeblocks unless told otherwise."""
    if enable:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.rustest]\ncodeblocks = true\n", encoding="utf-8"
        )
    page = tmp_path / "page.md"
    page.write_text(body, encoding="utf-8")
    return page


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # rustest writes its human summary to stderr so stdout stays clean for --llm JSONL;
    # callers assert against the combined streams.
    return subprocess.run(
        [sys.executable, "-m", "rustest", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_cli_flag_is_tristate(tmp_path: Path) -> None:
    """No flag means config decides; --no-codeblocks overrides config to off."""
    page = _md(tmp_path, "```python\nassert True\n```\n")

    enabled = _run(str(page), "-q", cwd=tmp_path)
    assert enabled.returncode == 0, enabled.stdout + enabled.stderr
    assert "1 passed" in enabled.stdout + enabled.stderr

    overridden = _run(str(page), "--no-codeblocks", "-q", cwd=tmp_path)
    assert overridden.returncode == 4, overridden.stdout + overridden.stderr


def test_cli_flag_enables_without_config(tmp_path: Path) -> None:
    """--codeblocks works with no config file at all."""
    page = _md(tmp_path, "```python\nassert True\n```\n", enable=False)

    off = _run(str(page), "-q", cwd=tmp_path)
    assert off.returncode == 4, off.stdout + off.stderr

    on = _run(str(page), "--codeblocks", "-q", cwd=tmp_path)
    assert on.returncode == 0, on.stdout + on.stderr
    assert "1 passed" in on.stdout + on.stderr
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest python/tests/test_codeblock_switch.py -v
```

Expected: FAIL. `--codeblocks` is an unrecognised argument, and the no-flag case collects the page because the default has not reached the Python layer.

- [ ] **Step 3: Implement the flag pair**

Replace the `--no-codeblocks` block in `python/rustest/cli.py`:

```python
    _ = parser.add_argument(
        "--codeblocks",
        dest="enable_codeblocks",
        action="store_true",
        default=None,
        help="Collect and run python code blocks in markdown files named as arguments.",
    )
    _ = parser.add_argument(
        "--no-codeblocks",
        dest="enable_codeblocks",
        action="store_false",
        help="Do not collect code blocks, overriding any config setting.",
    )
```

`default=None` on the first argument is what makes the pair tri-state: `store_false` inherits the same dest, so "neither passed" stays `None` and config decides.

In `python/rustest/core.py`, change the `run()` parameter:

```python
    codeblocks: bool | None = None,
```

and its docstring line to state that `None` means the config decides. Forward `None` through to the Rust layer unchanged rather than coercing it to a bool.

- [ ] **Step 4: Run to verify they pass**

```bash
uv run ruff format python && uv run ruff check python && uv run basedpyright python
uv run pytest python/tests/test_codeblock_switch.py -v
```

Expected: PASS, and basedpyright clean.

- [ ] **Step 5: Commit**

```bash
git add python/rustest/cli.py python/rustest/core.py python/tests/test_codeblock_switch.py
git commit -m "feat(cli): --codeblocks/--no-codeblocks as a tri-state pair"
```

---

### Task 4: `block_segment` reaches the node id, never `class_name`

**Files:**
- Modify: `python/rustest/_v2_worker.py`: `_build_entry`, `ExecutionPlan`, `_CollectContext`, `collect_module`, `_make_items`, `_collect_function`
- Modify: `python/rustest/_v2_worker.py:3978-3980` (the `_build_entry` docstring the change falsifies). **`src/v2/manifest.rs:68` is deliberately NOT touched here** so this chain stays pure Python and never contends with the Rust chain's cargo builds; Task 7 updates it.
- Test: `python/tests/test_codeblock_execution.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `collect_module(..., block_segment: str | None = None)`. When set, every entry's `id` and `qualname` carry the segment as their first component; `class_name` does not.

**This is the task that prevents a silent wrong value.** `_build_entry` derives `class_name = ".".join(parts[:-1])` and `ExecutionPlan.class_name` is the same rule over `self.parts`. If the block segment were prefixed onto `parts`, every module-level test in a block would acquire a phantom class named `codeblock_3_line_88`. `FixtureRunner.note_test_boundary` uses `class_name` as the class-scope teardown boundary, so two module-level tests in one block would look like two tests in the same class and a class-scoped fixture would be built once and reused instead of torn down per test.

The full consumer set of the `parts[:-1]` rule, all of which must see the block-less value:

1. `_build_entry:3991`, the wire `class_name`
2. `ExecutionPlan.class_name:2341`
3. `note_test_boundary`, called from **two** sites, `:2719` and `:5851`
4. `_ItemNode.keywords:2416-2418`, which splits `class_name` into the keywords chain-map

- [ ] **Step 1: Write the failing tests**

Create `python/tests/test_codeblock_execution.py`. It owns its helpers so this chain shares no file with the switch tests:

```python
"""Doc block execution: node shapes, fixtures, and the failure model."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _md(tmp_path: Path, body: str, *, enable: bool = True) -> Path:
    if enable:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.rustest]
codeblocks = true
", encoding="utf-8"
        )
    page = tmp_path / "page.md"
    page.write_text(body, encoding="utf-8")
    return page


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # rustest writes its summary to stderr so stdout stays clean for --llm JSONL.
    return subprocess.run(
        [sys.executable, "-m", "rustest", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
```

Then add the pinning test:

```python
def test_block_segment_is_in_the_id_but_not_the_class_name(tmp_path: Path) -> None:
    """The wire shape, pinned directly.

    A module-level test inside a block must carry NO class_name. If it acquires one,
    class-scope teardown breaks silently; see test_class_scope_is_torn_down_per_test.
    """
    from rustest._v2_worker import collect_module
    import types

    module = types.ModuleType("block_probe")
    module.__file__ = str(tmp_path / "page.md")
    exec(
        "def test_alpha():\n    assert True\n"
        "class TestBox:\n    def test_beta(self):\n        assert True\n",
        module.__dict__,
    )

    entries, _plans = collect_module(
        module,
        tmp_path / "page.md",
        tmp_path,
        naming=_default_naming(),
        block_segment="codeblock_0_line_3",
    )

    by_name = {e["qualname"]: e for e in entries}
    alpha = by_name["codeblock_0_line_3.test_alpha"]
    assert alpha["id"].endswith("page.md::codeblock_0_line_3::test_alpha")
    assert "class_name" not in alpha, (
        "a module-level block test must have no class_name; a phantom class breaks "
        "class-scope teardown"
    )

    beta = by_name["codeblock_0_line_3.TestBox.test_beta"]
    assert beta["class_name"] == "TestBox", (
        "a real class keeps its own name, with no block segment mixed in"
    )
```

Add a `_default_naming()` helper to this same file, building whatever `Naming` value the existing worker tests use; do not invent a new constructor.

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest python/tests/test_codeblock_execution.py::test_block_segment_is_in_the_id_but_not_the_class_name -v
```

Expected: FAIL, `collect_module() got an unexpected keyword argument 'block_segment'`.

- [ ] **Step 3: Thread the segment through**

Add the parameter to `collect_module` and store it on `_CollectContext`:

```python
    block_segment: str | None = None,
```

Add the same field to `_CollectContext`, then in `_build_entry` accept it and use it for the id and qualname only:

```python
def _build_entry(
    rel_path: str,
    parts: tuple[str, ...],
    param_id: str | None,
    marks: list[MarkSpec],
    fixtures: list[str],
    block_segment: str | None = None,
) -> CollectedTestDict:
    id_parts = (block_segment, *parts) if block_segment else parts
    entry: CollectedTestDict = {
        "id": build_nodeid(rel_path, id_parts, param_id),
        "path": rel_path,
        "qualname": ".".join(id_parts),
    }
    # `class_name` is derived from `parts` ALONE, never from `id_parts`. A block segment
    # here would give every module-level test in a block a phantom class, and
    # `FixtureRunner.note_test_boundary` uses `class_name` as the class-scope teardown
    # boundary -- so a class-scoped fixture would be reused across tests that must each
    # get their own. This asymmetry is deliberate; see the doc-block execution spec.
    if len(parts) > 1:
        entry["class_name"] = ".".join(parts[:-1])
```

Add `block_segment` to `ExecutionPlan` as a plain field defaulting to `None`, and leave its `class_name` property reading `self.parts` untouched. Pass `context.block_segment` from `_collect_function` into both `_build_entry` and the `ExecutionPlan` it constructs.

Update `_build_entry`'s own docstring, replacing the claim that `class_name` is `qualname` minus its last segment with:

```
For a documentation code block the qualname carries a leading block segment that
`class_name` deliberately does not, so the two are not derivable from each other.
```

- [ ] **Step 4: Run to verify it passes**

```bash
uv run ruff format python && uv run ruff check python && uv run basedpyright python
uv run pytest python/tests/test_codeblock_execution.py -v
uv run pytest python/tests -q
```

Expected: the new test PASSES and the existing suite is unchanged, because `block_segment` defaults to `None` for every `.py` caller.

- [ ] **Step 5: Commit**

```bash
git add python/rustest/_v2_worker.py python/tests/test_codeblock_execution.py
git commit -m "feat(worker): carry a block segment in node ids without touching class_name"
```

---

### Task 5: Execute blocks at module level and enumerate them

**Files:**
- Modify: `python/rustest/_v2_worker.py`: `collect_markdown` (`:4669-4740`), and delete `_codeblock_callable` (`:4652-4666`)
- Test: `python/tests/test_codeblock_execution.py`

**Interfaces:**
- Consumes: `collect_module(..., block_segment=...)` from Task 4.
- Produces: `collect_markdown` returning per-inner-test entries for blocks that define tests, and a single `codeblock_N_line_M` entry for blocks that do not.

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_inner_tests_become_their_own_nodes(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "```python\n"
        "def test_one():\n    assert True\n"
        "def test_two():\n    assert False\n"
        "```\n",
    )
    proc = _run(str(page), "-v", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "1 failed" in combined and "1 passed" in combined, combined
    assert "::test_one" in combined and "::test_two" in combined


def test_a_block_with_no_test_functions_keeps_one_node(tmp_path: Path) -> None:
    page = _md(tmp_path, "```python\nx = 1\nassert x == 1\n```\n")
    proc = _run(str(page), "-v", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "1 passed" in combined, combined
    assert "codeblock_0_line_1" in combined


def test_an_inner_test_resolves_a_conftest_fixture(tmp_path: Path) -> None:
    """The 'a code block requests no fixtures' limitation is gone."""
    (tmp_path / "conftest.py").write_text(
        "from rustest import fixture\n\n@fixture\ndef supplied():\n    return 7\n",
        encoding="utf-8",
    )
    page = _md(
        tmp_path,
        "```python\ndef test_uses(supplied):\n    assert supplied == 7\n```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_class_scope_is_torn_down_per_test(tmp_path: Path) -> None:
    """The pinning test for the phantom-class hazard.

    Two module-level tests in one block must each get their own class-scoped fixture
    value. If the block segment leaked into class_name they would share one.
    """
    (tmp_path / "conftest.py").write_text(
        "from rustest import fixture\n"
        "_n = [0]\n\n"
        "@fixture(scope='class')\n"
        "def counter():\n"
        "    _n[0] += 1\n"
        "    return _n[0]\n",
        encoding="utf-8",
    )
    page = _md(
        tmp_path,
        "```python\n"
        "def test_first(counter):\n    assert counter == 1\n"
        "def test_second(counter):\n    assert counter == 2\n"
        "```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    assert proc.returncode == 0, (
        "class scope was not torn down per test; the block segment probably reached "
        "class_name\n" + proc.stdout + proc.stderr
    )


def test_parametrize_and_classes_work_inside_a_block(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "```python\n"
        "from rustest import parametrize\n\n"
        "@parametrize('n', [1, 2, 3])\n"
        "def test_p(n):\n    assert n > 0\n\n"
        "class TestBox:\n    def test_m(self):\n        assert True\n"
        "```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "4 passed" in combined, combined


def test_two_blocks_defining_the_same_name_get_distinct_ids(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "```python\ndef test_dup():\n    assert True\n```\n\n"
        "```python\ndef test_dup():\n    assert True\n```\n",
    )
    proc = _run(str(page), "-v", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "2 passed" in combined, combined
    assert "codeblock_0_" in combined and "codeblock_1_" in combined


def test_skip_marked_blocks_are_not_executed(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "<!--rustest.mark.skip-->\n"
        "```python\nraise RuntimeError('must not run')\n```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped" in proc.stdout + proc.stderr
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest python/tests/test_codeblock_execution.py -v -k "inner or conftest or class_scope or parametrize or distinct"
```

Expected: FAIL. Inner tests are still invisible, so each block reports as one passing node.

- [ ] **Step 3: Rewrite `collect_markdown`**

Replace the per-block body of the loop in `collect_markdown`. For each non-skipped block:

```python
        module = types.ModuleType(f"rustest_codeblock_{index}_{abs(hash(rel_path))}")
        # Not registered in `sys.modules`, matching the previous synthetic module. A class
        # defined inside a block therefore will not pickle; a doc example needing that is
        # out of scope.
        module.__file__ = str(path)
        segment = f"codeblock_{index}_line_{line_number}"

        # `build_registry` minus the import, in its order. The xunit hooks must be
        # registered BEFORE the block's own fixtures: autouse order is registration order,
        # so the reverse makes `setup_function` run after a user's autouse fixture.
        registry = conftest_registry(path, rootdir)
        for kind in ("module", "function"):
            for fixturedef in _xunit_fixturedefs(module, rel_path, kind=kind):
                registry.register(fixturedef)
        registry.parse_factories(module, rel_path)
        _register_declared_plugins(module, registry)

        block_entries, block_plans = collect_module(
            module,
            path,
            rootdir,
            naming=naming,
            registry=registry,
            asyncio_config=asyncio_config,
            block_segment=segment,
        )
```

Compile and execute the block at module level before that registration, using the existing filename convention so a traceback points at the markdown source:

```python
        exec(  # noqa: S102
            compile(code, f"{path}:L{line_number}", "exec"),
            module.__dict__,
        )
```

When `block_entries` is empty, fall back to the current single-node shape: build one `_build_entry(rel_path, (segment,), ...)` plus an `ExecutionPlan` whose `func` replays the already-completed execution rather than re-running the body. A skipped block keeps its current treatment exactly, including not being compiled.

`collect_markdown` needs `naming` and `asyncio_config` parameters, both already in hand at its call site in `collect_file` as `state.naming` and `state.asyncio`.

Delete `_codeblock_callable` and its now-stale docstring.

- [ ] **Step 4: Run to verify they pass**

```bash
uv run ruff format python && uv run ruff check python && uv run basedpyright python
uv run pytest python/tests/test_codeblock_execution.py -v
uv run pytest python/tests tests/ examples/tests/ -q
```

Expected: the new tests PASS and nothing else regresses.

- [ ] **Step 5: Check the real gate and expect failures**

```bash
uv run python -m rustest README.md user_guide/*.md
```

Expected: this is the moment the feature starts working, so the count changes. Record the new numbers. Any failure here is a real broken example, not a plan defect. Do not fix documentation in this task; note them and continue.

- [ ] **Step 6: Commit**

```bash
git add python/rustest/_v2_worker.py python/tests/test_codeblock_execution.py
git commit -m "feat(worker): execute doc blocks at module level and enumerate their tests"
```

---

### Task 6: The failure model

**Files:**
- Modify: `python/rustest/_v2_worker.py`: `collect_markdown`'s per-block exception handling
- Test: `python/tests/test_codeblock_execution.py`

**Interfaces:**
- Consumes: the per-block loop from Task 5.
- Produces: a block that raises yields failing tests rather than a file-level collection error.

A per-block collection error is not expressible: `collect_file` carries exactly one of `tests` / `error` per **file**, so one broken block would erase every sibling block's tests on the page.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_broken_block_does_not_erase_its_siblings(tmp_path: Path) -> None:
    """The pinning test for the per-file error shape."""
    page = _md(
        tmp_path,
        "```python\nimport no_such_module_at_all\n```\n\n"
        "```python\ndef test_sibling():\n    assert True\n```\n",
    )
    proc = _run(str(page), "-v", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "test_sibling" in combined, (
        "the healthy block was erased by its broken sibling\n" + combined
    )
    assert "1 failed" in combined and "1 passed" in combined, combined


def test_a_broken_page_exits_one_not_two(tmp_path: Path) -> None:
    """Failing tests, not a collection error, so the exit code is 1."""
    page = _md(tmp_path, "```python\nraise RuntimeError('boom')\n```\n")
    proc = _run(str(page), "-q", cwd=tmp_path)
    assert proc.returncode == 1, (
        f"expected 1 (failed tests), got {proc.returncode}\n" + proc.stdout + proc.stderr
    )


def test_the_traceback_names_the_markdown_source(tmp_path: Path) -> None:
    page = _md(tmp_path, "```python\nraise RuntimeError('boom')\n```\n")
    proc = _run(str(page), "-q", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "page.md:L1" in combined, (
        "traceback should point at the markdown source, not <string>\n" + combined
    )


def test_partial_failure_keeps_reached_tests_and_adds_a_block_node(
    tmp_path: Path,
) -> None:
    """A block that raises after defining tests produces both shapes.

    This is the deliberate exception to 'tests means no block node'.
    """
    page = _md(
        tmp_path,
        "```python\n"
        "def test_reached():\n    assert True\n"
        "raise RuntimeError('boom')\n"
        "def test_never_defined():\n    assert True\n"
        "```\n",
    )
    proc = _run(str(page), "-v", cwd=tmp_path)
    combined = proc.stdout + proc.stderr
    assert "test_reached" in combined, combined
    assert "codeblock_0_line_1" in combined, (
        "the exec failure needs a node of its own\n" + combined
    )
    assert "test_never_defined" not in combined, (
        "a test whose definition was never reached must not exist\n" + combined
    )


def test_a_block_body_runs_once_not_twice(tmp_path: Path) -> None:
    """Outcome transport is replay, not re-execution."""
    page = _md(
        tmp_path,
        "```python\n"
        "from pathlib import Path\n"
        "p = Path('side_effect.txt')\n"
        "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"
        "```\n",
    )
    proc = _run(str(page), "-q", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "side_effect.txt").read_text() == "x", (
        "the body ran more than once; the block node must replay its recorded outcome"
    )
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest python/tests/test_codeblock_execution.py -v -k "broken or exits_one or traceback or partial or once"
```

Expected: FAIL. The broken block currently errors the whole file.

- [ ] **Step 3: Implement the per-block boundary**

Wrap each block's exec and enumeration in its own `try`/`except Exception`. On failure, record the traceback and emit a failing `codeblock_N_line_M` node **in addition to** any entries already produced for tests defined before the raise. Do not let the exception escape to `collect_file`, which would error the whole file.

`SystemExit` and `KeyboardInterrupt` are deliberately not caught here: `collect_file` lets `SystemExit` through, matching a `.py` module with a module-level `sys.exit()`.

The block node's `ExecutionPlan.func` replays the recorded outcome, raising the stored exception rather than re-executing the body. This is what keeps side effects single.

- [ ] **Step 4: Run to verify they pass**

```bash
uv run ruff format python && uv run ruff check python && uv run basedpyright python
uv run pytest python/tests/test_codeblock_execution.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/rustest/_v2_worker.py python/tests/test_codeblock_execution.py
git commit -m "feat(worker): a failing doc block is a failing test, not a file-level error"
```

---

### Task 7: Accepted consequences, integration, and the documentation

**Files:**
- Create: `tests/test_codeblocks_integration.py`
- Modify: `user_guide/markdown-testing.md`, `user_guide/cli.md`, `CLAUDE.md`, `CHANGELOG.md`
- Modify: any `README.md` / `user_guide/*.md` example that Task 5 revealed as broken

**Interfaces:**
- Consumes: everything above.
- Produces: the shipped, documented feature.

- [ ] **Step 1: Write the accepted-consequence tests**

Create `tests/test_codeblocks_integration.py` using the same `tmp_path` + subprocess pattern as `tests/test_pytest_plugins_fixtures.py`, covering:

```python
def test_deselection_does_not_gate_body_execution(tmp_path: Path) -> None:
    """Accepted consequence, asserted rather than discovered.

    Deselecting by -m no longer prevents a block body from running, because bodies run
    at collect. Identical to .py semantics.
    """
    page = _md(
        tmp_path,
        "```python\n"
        "from pathlib import Path\n"
        "Path('ran.txt').write_text('yes')\n"
        "```\n",
    )
    proc = _run(str(page), "-m", "not codeblock", "-q", cwd=tmp_path)
    assert (tmp_path / "ran.txt").exists(), (
        "the body should still have executed at collect despite deselection"
    )


def test_collect_only_executes_bodies(tmp_path: Path) -> None:
    """--v2-collect-only runs module-level code, exactly as it does for a .py file."""
    page = _md(
        tmp_path,
        "```python\n"
        "from pathlib import Path\n"
        "Path('collected.txt').write_text('yes')\n"
        "```\n",
    )
    proc = _run(str(page), "--v2-collect-only", cwd=tmp_path)
    assert (tmp_path / "collected.txt").exists(), proc.stdout + proc.stderr


def test_k_selects_by_block_and_by_inner_test(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "```python\ndef test_alpha():\n    assert True\n```\n\n"
        "```python\ndef test_beta():\n    assert True\n```\n",
    )
    by_block = _run(str(page), "-k", "codeblock_0", "-v", cwd=tmp_path)
    assert "test_alpha" in by_block.stdout + by_block.stderr
    by_test = _run(str(page), "-k", "test_beta", "-v", cwd=tmp_path)
    combined = by_test.stdout + by_test.stderr
    assert "1 passed" in combined and "1 deselected" in combined, combined
```

- [ ] **Step 2: Run them**

```bash
uv run pytest tests/test_codeblocks_integration.py -v
uv run python -m rustest tests/test_codeblocks_integration.py -v
```

Expected: PASS under both runners.

- [ ] **Step 3: Fix any documentation examples Task 5 revealed**

Run the gate and repair real breakage:

```bash
uv run python -m rustest README.md user_guide/*.md
```

Fix broken examples by defining the helpers they reference so a reader can paste and run them. Reserve `<!--rustest.mark.skip-->` for snippets that genuinely cannot run. Do not silence a failure with a skip marker just to make the gate green: a marker means nothing will ever tell you when the block stops being correct.

- [ ] **Step 4: Rewrite the documentation**

`user_guide/markdown-testing.md` is the page that documents this mechanism and is currently the page most wrong about it. It must now say: blocks are off by default; `--codeblocks` or the config key enables them; a `def test_*` inside a block **does** run and reports as its own node; bodies execute at collect, so deselection does not prevent them running; a skip-marked block is not executed and nothing will report when it stops being correct.

`user_guide/cli.md`: the `--codeblocks` / `--no-codeblocks` pair, the default flip, both config spellings.

`CLAUDE.md` L329, L338 and L412: these become accurate rather than a walk-back. L329 and L338 may now say code blocks are executed as tests. **L331-333, the fresh-namespace rule, is already correct and stays.**

`CHANGELOG.md` gets all **seven** breaking changes: the default flip, the node-id shape change, previously-hidden failures surfacing, the `run()` API default, the exit code moving 2 to 1 for a broken page, stale `--lf` entries, and **autouse fixtures no longer reaching a block's top-level statements** (they still reach the tests inside a block). The seventh was found during Task 5's review and confirmed empirically: a conftest autouse fixture setting an environment variable, asserted at block top level, now fails, while the same assertion inside a `def test_*` in the same block passes. It is consistent with `.py` semantics, where module-level code never had autouse either, but it is user-visible. See the spec's breaking-changes section, item 7. Pre-commit syncs it to `user_guide/changelog.md`; verify with `git diff --no-index CHANGELOG.md user_guide/changelog.md` returning empty.

- [ ] **Step 5: Full verification**

```bash
cargo fmt --check && cargo clippy --lib -- -D warnings
cargo test --no-default-features -- --test-threads=1
uv run ruff format --check python && uv run ruff check python && uv run basedpyright python
uv run pytest python/tests -q
uv run pytest tests/ examples/tests/ -q
uv run python -m rustest tests/ examples/tests/ -v
uv run python -m rustest README.md user_guide/*.md
uv run pre-commit run --all-files
```

Expected: all green. The docs gate now reports real results, and a `def test_*` inside a documentation block genuinely runs.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: documentation code blocks execute for real, off by default"
```

---

## Self-Review

**Spec coverage.** The switch and both config homes are Tasks 1-3. The execution model, the four-step registration and module-level exec are Task 5. `block_segment` and the `class_name` exclusion are Task 4. The failure model and the partial-failure case are Task 6. The accepted deselection consequence, `--v2-collect-only`, `-k`, docs and changelog are Task 7. Every breaking change in the spec has a changelog line in Task 7 Step 4.

**Known gap, deliberately deferred.** The spec's "Mechanical notes" list assertion rewriting as absent for blocks and `-n` distributing by file. Neither needs code; both are documented in Task 7 Step 4 rather than implemented.

**Type consistency.** `block_segment: str | None` is the name in Tasks 4, 5 and 6. `codeblocks: Option<bool>` is the Rust type in Tasks 1 and 2, and `codeblocks: bool | None` the Python one in Task 3. `read_tool_rustest_codeblocks(rootdir: &Path) -> Option<bool>` and `getini_bool(cfg: &ConfigDict, name: &str) -> Option<bool>` are used only in Task 1.

**Ordering.** Task 2 changes `pyproject.toml` in the same commit as the default flip, so this repository's docs gate never passes vacuously mid-plan. Task 4 precedes Task 5 because Task 5 calls `collect_module` with the parameter Task 4 adds.
