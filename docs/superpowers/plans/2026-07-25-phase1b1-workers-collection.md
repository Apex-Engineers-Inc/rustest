# Phase 1b.1: Worker Spine + Tier D Collection

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v2 collects real test suites through spawn workers into the manifest, byte-matching pytest's collected nodeids on the entire conformance corpus — including the cases v1 gets wrong (`collection/class-collection`, module identity underlying `fixtures/autouse`).

**Architecture:** The Rust orchestrator walks files (v2 config rules), dispatches them over a JSON-lines stdin/stdout protocol to spawned Python worker processes (`python -m rustest._v2_worker`), and aggregates `CollectedTest` entries into a `CollectionManifest`. Workers import modules under their REAL package identity (the #130 fix), enumerate per pytest's rules (config-driven naming, `__init__`-refusal for classes, inherited methods, unittest discovery), and return data only — no live objects cross the boundary. Execution is 1b.2; this sub-phase ends at collection parity.

**Tech Stack:** Rust (std::process, serde JSON-lines), Python 3.12+ worker, existing v2 modules (config, manifest, nodeid), conformance harness as the gate.

## Global Constraints

- **pytest is the oracle.** Collection semantics claims cite installed pytest source (`_pytest/python.py` collection rules, `_pytest/pathlib.py` import modes) and are locked by differential tests. The corpus is the acceptance gate: `--v2-collect-only` nodeids must equal `pytest --collect-only -q` nodeids on every corpus case (modulo documented, waived divergences ONLY where pytest semantics are deliberately out of scope).
- Windows dev machine; spawn is the only process primitive; `sys.executable` always; posix-form paths in all manifest/nodeid strings (producer normalizes — `CollectedTest` doc contract).
- Worker protocol is data-only JSON lines; schema changes are contract changes (golden tests).
- All existing gates stay green after every task (cargo v2 tests, python/tests, conformance 20 cases exit 0, poe lint/typecheck, fmt/clippy). `cargo test` env note: needs the uv CPython dir on PATH (see ledger).
- v1 untouched. New Python surface type-checked (basedpyright strict) and ruff-clean.
- Commit per task on `v2/phase0-conformance`; commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Worker protocol contract (Rust types + golden tests)

**Files:**
- Create: `src/v2/protocol.rs`; Modify: `src/v2/mod.rs`

**Interfaces (frozen contract; consumed by Tasks 2-4):**

```rust
use serde::{Deserialize, Serialize};
use crate::v2::manifest::{CollectedTest, CollectionErrorEntry};

pub const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkerRequest {
    /// Sent once as the first line of a worker's stdin.
    Init {
        protocol_version: u32,
        /// Absolute posix rootdir (nodeids are relative to it).
        rootdir: String,
        /// Naming rules from ResolvedConfig, passed through verbatim.
        python_files: Vec<String>,
        python_classes: Vec<String>,
        python_functions: Vec<String>,
    },
    /// Collect one file. path: absolute posix.
    CollectFile { path: String },
    /// Graceful shutdown; worker replies Bye then exits 0.
    Shutdown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkerResponse {
    Ready { protocol_version: u32 },
    /// Per-file result. Either tests or an error entry (import/syntax failure).
    Collected {
        path: String,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        tests: Vec<CollectedTest>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<CollectionErrorEntry>,
    },
    Bye,
}
```

- [ ] **Step 1 (RED):** `#[cfg(test)]` tests: golden JSON line for each variant (e.g. `{"op":"init","protocol_version":1,...}`, `{"op":"collect_file","path":"/a/t.py"}`, a `collected` with one test and no error key, a `collected` with error and no tests key); round-trips; unknown-op deserialization fails (not silently ignored).
- [ ] **Step 2:** Implement; `cargo test v2::protocol` green. **Step 3:** Gates. **Step 4:** Commit: `feat(v2): worker protocol v1 with golden wire contract`

---

### Task 2: Python worker — module identity + enumeration core

**Files:**
- Create: `python/rustest/_v2_worker.py`, `python/tests/test_v2_worker.py`
- Modify: `python/rustest/rust.pyi` only if needed (no Rust in this task)

**Interfaces:**
- Produces: `python -m rustest._v2_worker` speaking protocol v1 on stdin/stdout (one JSON object per line, flushed). Internal functions unit-testable without subprocess: `handle_init(msg) -> dict`, `collect_file(path: str) -> dict` (the `collected` response), and the two core helpers below. Task 3 spawns this module; Task 5's corpus gate exercises it end-to-end.

**The two correctness cores (each with cited pytest semantics):**

1. `resolve_module_identity(path: Path, rootdir: Path) -> tuple[str, str | None]` returning `(module_name, package_root)` — walk UP from the file collecting `__init__.py` dirs to build the real dotted name (`tests.unit.test_a`); a file in a non-package dir gets its bare stem (`conftest`, `test_a`) with the containing dir as the sys.path root. Import via `importlib` such that `sys.modules` holds the REAL name — `import conftest` from a test then hits the same module object (**the #130 fix; write the regression test exactly as the corpus case: fixture appends to a list in conftest, test imports conftest and reads it — same object**). Insert the sys.path root (if absent) the way pytest's default importmode does — cite `_pytest/pathlib.py::import_path`/`insert_missing_modules` for the semantics you port; name collisions between two same-stem files in different non-package dirs: first-wins with a collection error entry for the second (pytest errors here too — probe it, record the parity choice).
2. `enumerate_module(mod, path, rootdir, naming) -> list[CollectedTest-dicts]` implementing pytest's rules, each cited:
   - functions: name matches python_functions (prefix/glob per config semantics — the worker receives patterns and applies the same startswith/fnmatch rule; port the tiny matcher to Python or receive pre-compiled decisions? Port the matcher — 15 lines — and cite `_pytest/python.py::PyCollector._matches_prefix_or_glob_option`), defined-in-module check (`__module__` equality — pytest skips imported-in helpers; cite `_pytest/python.py::PyCollector.istestfunction` neighborhood), nested functions invisible (only module `__dict__` iteration).
   - classes: name matches python_classes; **refuse classes with `__init__`** (cite the pytest warning path `PytestCollectionWarning "cannot collect test class"`); collect inherited `test_*` methods (iterate `dir(cls)`-style with `__module__`-agnostic method lookup per pytest's unittest-style semantics — probe pytest on the Phase 0 `class TestB(TestA)` shape and match); methods per python_functions rules.
   - unittest.TestCase subclasses: enumerate via `unittest.TestLoader.getTestCaseNames` (cite source) — do NOT run anything.
   - parametrize: read `@pytest.mark.parametrize`/rustest marks from the compat/native decorators' attributes (`__rustest_marks__` etc. — read v1's decorators.py to consume the same metadata) and EXPAND into per-case entries with pytest-formatted ids (reuse/port v1's id formatting where it already byte-matches — Phase 0 proved parametrize ids match; cite which v1 code you reuse). `param_id` set; marks carried as MarkSpec dicts (skipif conditions NOT evaluated in 1b.1 — carried as args; evaluation is 1b.2).
   - qualname/class_name/id fields per the manifest contract; ids built path-first with the nodeid rules (port `build_nodeid` composition — path::Class::name[param]).
- [ ] **Step 1 (RED):** Python unit tests, no subprocess: module-identity round-trip (package vs non-package vs conftest — the #130 regression shape); enumeration table tests mirroring the corpus collection cases (naming-testfoo collects `testfoo`; underscore skipped; Helper class skipped; TestWithInit refused WITH an error/warning entry decision documented; nested function invisible; unittest methods listed; parametrize expansion ids `test_value[1]` etc.).
- [ ] **Step 2:** Implement; green. **Step 3:** Protocol loop (`main()`: read lines, dispatch, flush) + one subprocess smoke test writing Init/CollectFile/Shutdown lines and asserting Ready/Collected/Bye. **Step 4:** Gates (basedpyright strict on the new file!). **Step 5:** Commit: `feat(v2): python worker — real module identity and pytest-rule enumeration`

---

### Task 3: Rust orchestrator — file walk + worker pool + manifest assembly

**Files:**
- Create: `src/v2/collect.rs`; Modify: `src/v2/mod.rs`

**Interfaces:**
- Produces: `pub fn collect(invocation_dir: &Path, args: &[PathBuf], python_executable: &str, workers: usize) -> Result<CollectionManifest, CollectError>`
- File discovery: from `ResolvedConfig` — args (or testpaths, or rootdir) walked with `norecursedirs` pruning and `python_files` matching, deterministic sorted order (pytest sorts dir entries — cite `_pytest/main.py` traversal), respecting the config's rules ONLY (no .gitignore in v2 — v1's gitignore behavior is a documented v1-ism; pytest doesn't read .gitignore).
- Pool: spawn `workers` processes (`python_executable -m rustest._v2_worker`, piped stdio, spawn only); send Init, await Ready (protocol_version mismatch → error); round-robin CollectFile dispatch (work-stealing is 1b.2+ territory; keep simple), collect Collected responses, Shutdown+Bye+wait on completion; a worker crash (EOF mid-protocol) → CollectError naming the file in flight (loud, never silent).
- Manifest assembly: entries concatenated in dispatch order (deterministic given sorted walk + round-robin), rootdir posix, schema_version constant.
- [ ] **Step 1 (RED):** Rust tests with tempfile trees + the REAL worker (spawn `sys.executable`... in Rust tests use env var `RUSTEST_TEST_PYTHON` falling back to `python` — document; CI/dev both have it): walk honors norecursedirs + python_files; two-file tree yields sorted deterministic manifest; worker crash path (point at a file the worker will die on — e.g. protocol test double via `RUSTEST_V2_WORKER_ARGV` override env — keep simple: a test-only mode flag) errors loudly.
- [ ] **Step 2:** Implement; green. **Step 3:** Gates. **Step 4:** Commit: `feat(v2): orchestrator — config-driven walk, spawn pool, manifest assembly`

---

### Task 4: PyO3 + CLI surface — `--v2-collect-only`

**Files:**
- Create: none; Modify: `src/v2/py.rs` (add `v2_collect`), `src/lib.rs` (register), `python/rustest/rust.pyi`, `python/rustest/cli.py` (+ flag), `python/rustest/core.py` (route)
- Test: `python/tests/test_v2_collect_cli.py`

**Interfaces:**
- `rust.v2_collect(invocation_dir: str, args: list[str], workers: int) -> str` (CollectionManifest JSON; python_executable = `sys.executable` resolved on the Python side and passed through — add the param to the Rust fn).
- CLI: `rustest --v2-collect-only [paths...]` → prints nodeids one per line in manifest order (pytest `-q --collect-only` shape), then `N tests collected` summary line to stderr; exit 0 with tests, 5 with none (the v2 exit-code contract's first beachhead), 2 on collection errors (errors also printed to stderr with paths).
- [ ] **Step 1 (RED):** subprocess CLI tests: mini suite → nodeid lines byte-equal to `pytest --collect-only -q` for the same tree (a direct mini-differential right here); empty dir → exit 5; syntax-error file → exit 2 + error on stderr.
- [ ] **Step 2:** Implement + `uv run maturin develop`; green. **Step 3:** Gates. **Step 4:** Commit: `feat(v2): v2_collect PyO3 + --v2-collect-only CLI`

---

### Task 5: Conformance harness v2-collection mode — the 1b.1 gate

**Files:**
- Modify: `conformance/harness/runners.py` (add `run_rustest_v2_collect(case_dir, args) -> CollectResult(ids: list[str], exit_code: int)` using `--v2-collect-only` [AMENDED per Task 5 review: ordered list + exit code, never a bare set — ordering parity is graded]), `conformance/harness/grade.py` (collection-only grading path), `conformance/__main__.py` (`--v2-collect` flag: grade ONLY collected-ID sets + collection exit codes, pytest vs v2)
- Create: `conformance/waivers-v2-collect.toml` (separate ledger for the v2-collection gate)
- Test: extend `conformance/tests/`

**The gate:** `uv run python -m conformance --v2-collect` runs all 20 corpus cases comparing pytest's collected nodeid set against v2's. Expected outcome and the point of this whole sub-phase:
- `collection/class-collection` MATCHES (v2 refuses `__init__` classes like pytest — v1 couldn't).
- `collection/naming-testfoo`, `naming-underscore`, `nested-function`, `unittest-basic` (collection side), `conftest-visibility`, `empty-suite` (exit 5!), parametrize cases, `marks/mark-filter` under `-m` — hmm: `-m` filtering is post-collection; the v2-collect CLI doesn't take `-m` in 1b.1 → cases with `case.toml` args that v2-collect can't honor yet get waived in `waivers-v2-collect.toml` with reason "selection args land in 1b.2".
- Fixture cases match on collection (they collect identically even where execution differs).
- Every remaining divergence gets adjudicated with the Phase 0 discipline: mechanism, file:line, NEW-BUG prefix if unpredicted. **Target state: waivers-v2-collect.toml contains ONLY the selection-args deferrals plus `fixtures/parametrized-fixture`** (fixture-closure param ids require conftest fixture resolution — a 1b.2 protocol capability; Task 2 established a partial closure would emit wrong ids, worse than a waived gap).
- [AMENDED per Task 2 oracle probes: imported test functions ARE collected by pytest 8.4.2 (`collect_imported_tests` defaults True — no `__module__` filter); Task 3 must route files to workers by file-stem hash so same-stem collisions land on one worker and reproduce pytest's "import file mismatch" error.]
- [ ] **Step 1 (RED):** harness tests for the new runner fn + grading path (golden + one real). **Step 2:** Implement; run the gate; record verbatim output; adjudicate. **Step 3:** Full existing conformance (v1 mode) unchanged: 20 cases 13/7/0/0 exit 0. Gates. **Step 4:** Commit: `feat(conformance): v2-collection gate` **Step 5:** Update ledger with the v2-collect scoreboard.

---

## Definition of done (Phase 1b.1)

1. All prior gates green PLUS `python -m conformance --v2-collect` exit 0 with waivers limited to selection-args deferrals.
2. The #130 module-identity regression test passes in the worker (conftest shared-state shape).
3. `--v2-collect-only` byte-matches pytest nodeids on the corpus; exit codes 0/2/5 honored.
4. Ledger updated; Phase 1b.2 plan (execution engine, outcome classification, un-waiving) authored at this gate.
