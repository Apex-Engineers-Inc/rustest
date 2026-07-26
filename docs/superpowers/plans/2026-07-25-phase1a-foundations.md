# Phase 1a: v2 Foundations — Floor, Config Subsystem, Manifest, Nodeid Contract

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the v2 spine's ground layer — Python 3.12 floor, the pytest-semantics config subsystem, the serializable manifest model, and the nodeid contract — plus the Phase 1 harness backlog, all unit- and oracle-verified.

**Architecture:** v2 lives as a new Rust module tree `src/v2/` inside the existing crate (no workspace split until Cleanup phase; v1 code untouched). Config resolution and manifest types are pure-Rust with serde JSON contracts, exposed to Python through narrow PyO3 debug functions so the conformance harness can diff v2's config decisions against real pytest (the oracle). Phase 1b builds workers + Tier D collection on top of these types.

**Tech Stack:** Rust (serde, serde_json; existing PyO3/maturin build), Python 3.12+, pytest-as-oracle differential tests.

## Global Constraints

- **pytest is the oracle, not memory.** Any claim about pytest's default config semantics MUST be extracted from the installed pytest source (`.venv/Lib/site-packages/_pytest/main.py`, `_pytest/python.py`, `_pytest/config/__init__.py`) with the source location cited in a code comment, then locked by a differential test against a real `pytest` subprocess. Phase 0 falsified two desk-audit claims; do not add a third.
- Windows dev machine: pathlib in Python; forward-slash posix forms in every manifest/nodeid string; `sys.executable` for subprocesses.
- All Phase 0 gates stay green after every task: `uv run python -m conformance` (18+ cases, exit 0), `uv run pytest conformance/tests`, `uv run pytest python/tests`, `uv run poe lint && uv run poe typecheck`, `cargo fmt --check && cargo clippy --lib -- -D warnings`, `cargo test`.
- Rust code: rustfmt, clippy -D warnings, unit tests colocated (`#[cfg(test)]`), `tempfile` crate for dir fixtures (add as dev-dependency if absent).
- Commit per task on branch `v2/phase0-conformance`; end commit messages with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- v1 runtime behavior must not change in this sub-phase (v2 code is dormant except debug surfaces).

---

### Task 1: Python 3.12 floor

**Files:**
- Modify: `pyproject.toml` (`requires-python = ">=3.12"`; `[tool.basedpyright] pythonVersion = "3.12"`; drop any 3.10/3.11 classifiers)
- Modify: `.github/workflows/ci.yml` (test matrix: remove 3.10 and 3.11, keep 3.12/3.13/3.14)
- Modify: `CLAUDE.md` ("Python Support: 3.10 - 3.14" → "3.12 - 3.14"), plus any README/docs lines stating 3.10 (search `3.10` across README.md, docs/, python/rustest/ and update user-facing floors; leave historical changelog entries alone)
- Modify: `conformance/harness/grade.py` — the tomllib ImportError guard message becomes "conformance harness requires Python >= 3.12" (floor now project-wide); `conformance/README.md` already says 3.12.

**Interfaces:** none produced; this is configuration. User decision 2026-07-25 recorded in the spec (line 45-47) is the authority.

- [ ] **Step 1:** Apply all edits above. Search first: `grep -rn "3\.10" pyproject.toml CLAUDE.md README.md .github/ docs/ python/rustest/ conformance/` and judge each hit (floors change; history does not).
- [ ] **Step 2:** Verify: `uv sync --all-extras` succeeds; full gate suite from Global Constraints all green.
- [ ] **Step 3:** Commit: `chore: bump Python floor to 3.12 (v2 decision; enables sys.monitoring coverage)`

---

### Task 2: v2 module skeleton + manifest model

**Files:**
- Create: `src/v2/mod.rs`, `src/v2/manifest.rs`
- Modify: `src/lib.rs` (add `pub mod v2;` — no PyO3 exports yet)
- Modify: `Cargo.toml` (add `serde = { version = "1", features = ["derive"] }`, `serde_json = "1"` if absent; `tempfile` as dev-dependency)

**Interfaces:**
- Produces (frozen contract consumed by 1b/1c and by the v2 JSON report):

```rust
// src/v2/manifest.rs
use serde::{Deserialize, Serialize};

pub const MANIFEST_SCHEMA_VERSION: u32 = 2;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MarkSpec {
    pub name: String,
    /// Positional args as JSON values (skipif conditions arrive pre-evaluated as bools in 1b).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub args: Vec<serde_json::Value>,
    #[serde(default, skip_serializing_if = "serde_json::Map::is_empty")]
    pub kwargs: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectedTest {
    /// Full pytest-byte-compatible nodeid, rootdir-relative, posix separators.
    pub id: String,
    /// Rootdir-relative posix file path (the nodeid's first segment).
    pub path: String,
    /// Dotted qualname within the module, e.g. "TestBox.test_method" or "test_top".
    pub qualname: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub class_name: Option<String>,
    /// Bracket content for parametrized cases, without brackets (e.g. "x-1").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub param_id: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub marks: Vec<MarkSpec>,
    /// Direct fixture parameter names in signature order.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fixtures: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectionErrorEntry {
    pub path: String,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectionManifest {
    pub schema_version: u32,
    /// Absolute rootdir, posix separators.
    pub rootdir: String,
    pub tests: Vec<CollectedTest>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub errors: Vec<CollectionErrorEntry>,
}
```

- [ ] **Step 1: Write failing Rust tests** in `src/v2/manifest.rs` `#[cfg(test)]`: (a) round-trip: build a manifest with one plain test, one class+param test carrying a mark with args/kwargs, one error; serialize → deserialize → `assert_eq!`; (b) **golden JSON contract test**: serialize a fixed manifest and `assert_eq!` against an inline golden string literal (pretty=false) — field names and omission rules are the frozen wire contract; (c) empty-collection manifest serializes without `tests`-adjacent noise (`errors` omitted when empty).
- [ ] **Step 2:** `cargo test v2::manifest` → fails to compile (module absent). Create the module tree + types; `cargo test` green.
- [ ] **Step 3:** Full gate suite green (maturin rebuild not required — no PyO3 surface yet — but `cargo clippy`/`fmt` are).
- [ ] **Step 4:** Commit: `feat(v2): manifest data model with frozen JSON contract`

---

### Task 3: Config subsystem — rootdir resolution + ini semantics

**Files:**
- Create: `src/v2/config.rs`
- Modify: `src/v2/mod.rs`

**Interfaces:**
- Produces (consumed by 1b collection and 1c CLI):

```rust
// src/v2/config.rs
pub struct ResolvedConfig {
    pub rootdir: std::path::PathBuf,
    pub config_file: Option<std::path::PathBuf>,
    pub testpaths: Vec<String>,
    pub python_files: Vec<String>,
    pub python_classes: Vec<String>,
    pub python_functions: Vec<String>,
    pub norecursedirs: Vec<String>,
    pub addopts: Vec<String>,
    pub markers: Vec<String>,
}

/// Resolve rootdir + ini exactly as pytest does, from CLI path args.
pub fn resolve_config(invocation_dir: &std::path::Path, args: &[std::path::PathBuf])
    -> Result<ResolvedConfig, ConfigError>;

/// pytest's name-matching rule for python_classes/python_functions:
/// plain patterns match by str::starts_with; patterns containing glob chars
/// ("*?[") match by fnmatch semantics. (Cite _pytest/python.py source.)
pub fn matches_name_pattern(name: &str, patterns: &[String]) -> bool;

/// File-pattern matching for python_files (always fnmatch on basename).
pub fn matches_file_pattern(basename: &str, patterns: &[String]) -> bool;
```

- **Semantics to implement — extract each from the installed pytest source and cite the file in a comment (Global Constraint: pytest is the oracle):**
  1. Config file precedence and section rules: `pytest.ini` (authoritative even empty) → `pyproject.toml` `[tool.pytest.ini_options]` (only when the table exists) → `tox.ini` `[pytest]` → `setup.cfg` `[tool:pytest]`. Extract the exact search/precedence from `_pytest/config/findpaths.py`.
  2. Rootdir resolution algorithm (same file): ancestors of the common ancestor of args, first dir holding a qualifying config; setup.py fallback; final fallbacks. Implement the documented algorithm faithfully, including args-empty → invocation dir.
  3. Defaults: read the registered ini defaults from `_pytest/main.py` / `_pytest/python.py` (`python_files`, `python_classes`, `python_functions`, `norecursedirs`) — DO NOT trust memory; Phase 0 proved `python_functions` matches by prefix `test` (corpus case collection/naming-testfoo). Encode the extracted defaults as constants with source citations.
  4. Name matching: prefix-first, fnmatch only for glob-bearing patterns (this is the Phase 0 lesson, now load-bearing).
  5. Values may be strings or lists in TOML/ini; normalize per pytest's `_strtobool`/linelist handling for the fields above (extract the per-field types from source).

- [ ] **Step 1: Write failing Rust tests** (`#[cfg(test)]`, `tempfile::TempDir` layouts): precedence quartet (each config-file kind wins in its slot; empty `pytest.ini` beats populated `pyproject.toml`); rootdir anchoring for nested arg dirs; defaults when nothing found; `matches_name_pattern("testfoo", defaults)` is **true** while `matches_name_pattern("checkfoo", defaults)` is false; glob pattern `"test_*"` rejects `testfoo`; `matches_file_pattern` accepts extracted default file patterns and rejects others; norecursedirs defaults include the extracted set.
- [ ] **Step 2:** `cargo test v2::config` red → implement → green.
- [ ] **Step 3:** Full gate suite green.
- [ ] **Step 4:** Commit: `feat(v2): config subsystem — rootdir + ini semantics with pytest-source citations`

---

### Task 4: Nodeid construction

**Files:**
- Create: `src/v2/nodeid.rs`
- Modify: `src/v2/mod.rs`

**Interfaces:**
- Produces:

```rust
/// Build a pytest-byte-compatible nodeid.
/// path: rootdir-relative posix path. parts: class chain then function name.
/// param_id: bracket content without brackets.
pub fn build_nodeid(path: &str, parts: &[&str], param_id: Option<&str>) -> String;
/// Inverse split, for tooling: (path, parts, param_id).
pub fn split_nodeid(nodeid: &str) -> (String, Vec<String>, Option<String>);
```

- Contract: `build_nodeid("tests/test_a.py", &["TestBox", "test_m"], Some("x-1"))` == `"tests/test_a.py::TestBox::test_m[x-1]"`; no escaping is performed (param ids arrive pre-formed; pytest's own id sanitization happens at id-generation time in 1b, not here); `split_nodeid` treats the **first** `[` after the last `::` as the param boundary and tolerates `::` never appearing (bare file id).

- [ ] **Step 1: Failing tests:** the example above; nested classes (`&["TestA", "TestB", "test_x"]`); no-class; no-param; param containing `::`-free brackets like `"1-2[a]"`? — no: param containing nested `[` (`"data[0]"`) round-trips via first-`[`-after-last-`::` rule (`split(build(x)) == x` for a table of cases including that one); bare-file split.
- [ ] **Step 2:** Red → implement → green. **Step 3:** Gates. **Step 4:** Commit: `feat(v2): nodeid construction contract`

---

### Task 5: PyO3 debug surface + pytest-oracle differential tests

**Files:**
- Create: `src/v2/py.rs` (PyO3 fns), `python/tests/test_v2_config_oracle.py`
- Modify: `src/v2/mod.rs`, `src/lib.rs` (register `v2_resolve_config` in the existing `rust` module), `python/rustest/rust.pyi` (typing stub)

**Interfaces:**
- Produces: `rust.v2_resolve_config(invocation_dir: str, args: list[str]) -> str` returning `ResolvedConfig` as JSON: `{"rootdir": str, "config_file": str|null, "testpaths": [...], "python_files": [...], "python_classes": [...], "python_functions": [...], "norecursedirs": [...], "addopts": [...], "markers": [...]}` with rootdir/config_file as absolute posix strings.

- [ ] **Step 1: Failing Python oracle tests** (`python/tests/test_v2_config_oracle.py`): for each of four `tmp_path` layouts — (a) bare dir, (b) `pytest.ini` at root with nested tests dir, (c) `pyproject.toml` with `[tool.pytest.ini_options]` at a parent, (d) `tox.ini` `[pytest]` — run REAL pytest via `subprocess` (`sys.executable -m pytest --collect-only -q`, `cwd=layout`) and parse the `rootdir:` line from its header (`--co` header prints it; use `-rN`? keep: parse stdout line starting with `"rootdir:"`), then call `rust.v2_resolve_config` and assert the rootdirs are equal (`Path(...)` compare, case-normalized on Windows). Also assert extracted defaults: `python_functions` matching accepts `testfoo` per corpus (call a tiny `v2`-exposed matcher? — no second surface: instead assert the JSON's `python_functions` equals the pytest-source-extracted defaults committed in Task 3, citing the same source in the test's docstring).
- [ ] **Step 2:** Red (function absent) → implement `py.rs`, register, `uv run maturin develop` → green.
- [ ] **Step 3:** Full gates including `uv run pytest python/tests` (grows by these tests) and conformance (unchanged).
- [ ] **Step 4:** Commit: `feat(v2): config debug surface + pytest-oracle differential tests`

---

### Task 6: Harness — exit-5 passthrough + no-tests corpus cases

**Files:**
- Modify: `conformance/harness/runners.py` (`_check_pytest_exit`: only exits >= 3 AND != 5 are harness faults; 5 flows to the grader as a normal outcome)
- Create: `conformance/corpus/collection/empty-suite/test_nothing_collected.py` (a file whose only function is `def helper(): pass` — collected by neither runner) and `conformance/corpus/marks/deselect-all/` (`test_marks.py` with one `@pytest.mark.smoke` test + `case.toml` args `["-m", "nosuchmark"]`)
- Modify: `conformance/waivers.toml` (expected: both cases DIVERGE on exit codes — pytest 5 vs rustest 0; waive citing the v2 exit-code contract, spec "Contracts are pytest's: exit codes 0-5")
- Test: extend `conformance/tests/test_runners.py` (golden: exit-5 summary parse yields Outcomes with collection_error False and exit_code 5, no RuntimeError)

- [ ] **Step 1:** Failing test for the exit-5 passthrough (call `_check_pytest_exit` — or its successor shape — with rc=5 → no raise; rc=3, rc=4 → raise). **Step 2:** Implement; red→green. **Step 3:** Create the two corpus cases; run `uv run python -m conformance --only collection/empty-suite` then `--only marks/deselect-all`; record pre-waiver output; adjudicate + waive with exit-code-contract reasons (verify pytest genuinely exits 5 in both). **Step 4:** Full conformance green (now 20 cases); harness tests green. **Step 5:** Commit: `feat(conformance): exit-5 passthrough + no-collection corpus cases`

---

### Task 7: Harness backlog polish

**Files:**
- Modify: `conformance/__main__.py` (wrap the `load_waivers` call: malformed TOML → one-line `SystemExit` naming the file and TOML error, not a traceback)
- Modify: `conformance/harness/runners.py` (`parse_pytest_collect`: anchor to a nodeid regex `^[^\s:][^:\n]*(::[^:\s][^:\n]*)+(\[[^\n]*\])?$`-style — implementer refines against real `-q --collect-only` output — killing the phantom-ID risk)
- Test: `conformance/tests/` — malformed waivers.toml → SystemExit with filename in message; collect-parse golden extended with a traceback-looking line containing `::` that must NOT be parsed as an id.

- [ ] **Step 1:** Failing tests → **Step 2:** implement → green → **Step 3:** full gates + conformance (20 cases, exit 0) → **Step 4:** Commit: `fix(conformance): guard waivers load; anchor collect parsing`

---

## Definition of done (Phase 1a)

1. Full gate suite green including `cargo test` (new v2 unit tests), oracle tests, and 20-case conformance run, on Python 3.12+.
2. Every pytest-semantics constant in `src/v2/config.rs` carries a pytest-source citation comment and a locking test.
3. Manifest golden-JSON contract test exists (the wire format is frozen for 1b/1c).
4. Ledger updated; Phase 1b plan is written only after this gate, incorporating anything the oracle tests falsify.
