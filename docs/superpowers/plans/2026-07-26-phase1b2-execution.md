# Phase 1b.2: Execution Engine — Full-Run Parity and the Un-Waiving Sweep

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rustest --v2` runs the whole corpus with pytest-parity outcomes and exit codes, fixing at the root every v1 execution bug the corpus found (#129 unittest silent-pass, #131 skipif ignored, xfail unsupported, zero-collected exit 5), un-waiving every fixable entry in BOTH conformance ledgers, and closing the three v2-collect deferrals (selection args, fixture-closure param ids) plus the duplicate-path-args parity gap.

**Architecture:** Protocol v2 adds Execute ops. Workers gain a fixture engine (conftest-chain closures, function/module scopes, yield teardown in reverse order) and an execution core classifying outcomes by exception TYPE. The orchestrator schedules manifest tests to the workers that collected them (imports already warm), applies `-k`/`-m` selection pre-dispatch in Rust, aggregates outcome events into a v2 JSON report, and maps pytest's exit-code contract. The conformance harness gains a full-run v2 gate (ids + outcome counts + exit codes).

**Tech Stack:** Existing v2 stack. pytest-as-oracle discipline throughout; per-test mutation verification for every new instrument test (ledger method note — an inert test hid behind a suite-level score for a full cycle in 1b.1).

## Global Constraints

- **pytest is the oracle**; cite installed source for every semantic (fixture resolution: `_pytest/fixtures.py`; outcomes: `_pytest/outcomes.py`; skipping: `_pytest/skipping.py`; unittest: `_pytest/unittest.py`; `-k`/`-m`: `_pytest/mark/__init__.py` + `expression.py`).
- Outcome classification is by exception TYPE identity, never message strings (spec decision; v1's `execution.rs:649` string-matching is the anti-pattern).
- All 1b.1 gates stay green after every task; the v1 conformance gate stays untouched at 14/7/0/0 until the final un-waiving task, which is the ONLY task allowed to edit waiver ledgers.
- Worker protocol changes are frozen-contract changes: golden tests, deny_unknown_fields, PROTOCOL_VERSION bump to 2 (Init carries new config fields; old workers must fail loudly).
- No pytest import in the worker. Windows-first. Data-only protocol. Commit per task on `v2/phase0-conformance`; commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Protocol v2 — Execute ops + outcome wire contract

**Files:** Modify `src/v2/protocol.rs`, `src/v2/manifest.rs` (no struct changes; re-exports only if needed)

**Interfaces (frozen on landing):**

```rust
pub const PROTOCOL_VERSION: u32 = 2;

// New WorkerRequest variants:
    /// Execute one collected test by manifest id. The worker must have collected
    /// the file already in this session (imports warm); executing an unknown id
    /// is a protocol error response, not a silent skip.
    ExecuteTest { id: String },
// New WorkerResponse variants:
    /// Outcome of one ExecuteTest.
    TestResult {
        id: String,
        /// "passed" | "failed" | "skipped" | "xfailed" | "xpassed" | "error"
        status: String,
        duration_s: f64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        message: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        stdout: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        stderr: Option<String>,
    },
```

`Init` gains `invocation_dir: String` (workers need it for fixture `tmp_path` factories rooted consistently). Status strings are the six-value closed set — document that `error` means setup/teardown/internal failure (pytest's E column) vs `failed` (test body assert), citing `_pytest/reports.py` semantics.

- [ ] Golden lines for both new variants (populated + omission shapes); round-trips; version bump pinned in both `init`/`ready` goldens; unknown-status is NOT validated at decode (String field — the orchestrator validates; document why: forward-compat within a session is impossible, so validation lives one layer up with better diagnostics). Per-test mutation verification for each new golden. Commit: `feat(v2): protocol v2 — execute ops and outcome wire contract`

---

### Task 2: Worker fixture engine + closure param ids

**Files:** Modify `python/rustest/_v2_worker.py`; tests in `python/tests/test_v2_worker_fixtures.py` (new file — keep the collection tests file focused)

**Scope (cited to `_pytest/fixtures.py` throughout):**
1. Fixture REGISTRY built during collection: `@rustest.fixture`/compat `@pytest.fixture` functions from the module + its conftest chain (walk up from the file's dir to rootdir, importing each `conftest.py` under its real identity — reuse `resolve_module_identity`). Nearest-definition shadowing (module > closest conftest > … > rootdir conftest).
2. Fixture CLOSURE per test: direct params → transitive fixture deps (param names of fixture functions), autouse fixtures injected (module + conftest chain scope rules). Unknown fixture name → the test errors at setup with pytest's `fixture '<name>' not found` message shape (status `error`).
3. Scopes: `function` (fresh per test) and `module` (cached per module, torn down when the worker moves past the module — for 1b.2, teardown at Shutdown is acceptable IF pytest-observable ordering within the corpus matches; the yield-teardown corpus case is the oracle — probe it and implement what makes it MATCH). `session` scope: treat as module-scoped-per-worker for now with a documented limitation (no corpus case exercises cross-file session fixtures yet; record as a 1c corpus addition).
4. Yield fixtures: setup to the yield, teardown after the test in REVERSE setup order (cite `_pytest/fixtures.py` finalizer stack). Teardown exceptions → status `error` with the teardown message (pytest reports these as errors even when the body passed).
5. **Closure-driven parametrized-fixture ids:** `@pytest.fixture(params=[...])` fixtures in the closure multiply the test into per-param entries with pytest's id formatting — THIS is what un-waives `fixtures/parametrized-fixture` in the v2-collect gate. Collection-time change: `collect_file` must resolve closures for param expansion (registry must therefore be built during collection — it already imports the module; conftest chain import moves here). Ids byte-match pytest (the corpus case is the oracle).
6. Builtin fixtures for 1b.2: `tmp_path`, `monkeypatch`, `capsys` (reuse v1's implementations from `builtin_fixtures.py` where import-safe — cite what you reuse; wrap rather than fork). Others error clearly as not-yet-supported.

- [ ] TDD with the corpus fixture cases as the test table (scope-function, scope-module, yield-teardown ordering, autouse, override-nearest, parametrized-fixture ids). Per-test mutation verification on the closure resolver and teardown-order tests. Commit: `feat(v2): worker fixture engine — closures, scopes, teardown order, param ids`

---

### Task 3: Worker execution core — outcomes by type

**Files:** Modify `python/rustest/_v2_worker.py`; tests in `python/tests/test_v2_worker_execute.py` (new)

**Scope:**
1. `execute_test(id) -> dict` (the TestResult response): resolve the CollectedTest (worker-local index from collection), build fixture closure, run setup → call → teardown with per-phase exception capture. Capture stdout/stderr per test (io redirection; fd-level is 1c).
2. Outcome classification BY TYPE: rustest's `Skipped`/compat skip exception → `skipped`; `_pytest_stub`/compat `Failed`/AssertionError from body → `failed`; setup/teardown exceptions → `error`; everything else from body → `failed` with traceback message. Cite `_pytest/outcomes.py` class semantics. NO string matching anywhere.
3. Marks at execution: `skip`/`skipif` (evaluate stored condition args now — closing #131 at the root; reuse the applymarker evaluate-and-skip logic noted in Phase 0), `xfail` (body fails → `xfailed`; body passes → `xpassed`; strict=True xpass → `failed`, citing `_pytest/skipping.py::xfailed_key` handling). Class-mark MRO port (`get_unpacked_marks` reversed-`__mro__` `__dict__` walk) so inherited marks apply like pytest — closing the #135 read-path properly.
4. **unittest execution — the #129 fix at the root:** run via `TestCase(name)(result)` with an explicit `unittest.TestResult`, then TRANSLATE: failures → `failed`, errors → `error`, skipped → `skipped`, expectedFailure → `xfailed`, unexpectedSuccess → `xpassed` (cite `_pytest/unittest.py`'s TestCaseFunction mapping). The corpus `unittest-basic` case (1 pass 1 fail) is the acceptance oracle.
5. Duration per test (perf_counter around the phases).

- [ ] TDD: corpus marks/skip-and-skipif and marks/xfail shapes as tables; unittest translation matrix (all five buckets); per-test mutation verification on the classification switch (each branch individually killable). Commit: `feat(v2): worker execution — type-identity outcomes, unittest translation, mark semantics`

---

### Task 4: Orchestrator execute + selection + CLI `--v2`

**Files:** Modify `src/v2/collect.rs` (or new `src/v2/execute.rs` — implementer's structural call, one clear owner), `src/v2/py.rs`, `src/lib.rs`, `python/rustest/rust.pyi`, `python/rustest/cli.py`, `python/rustest/core.py`; new `src/v2/selection.rs` for `-k`/`-m`

**Scope:**
1. `-k` full expression language and `-m` expressions in Rust: port pytest's `expression.py` grammar (ident/and/or/not/parens; `-k` also matches substrings against id parts) — cite it; apply to the manifest BEFORE dispatch (deselected count tracked). This un-waives `marks/mark-filter` + `marks/deselect-all` in the v2-collect gate (v2-collect-only accepts `-k`/`-m` now too) and gives the full-run gate selection parity.
2. Execute scheduling: each test dispatched to the worker that collected its file (warm imports — stem-hash routing already guarantees the mapping); manifest order preserved in the report via index reassembly (same pattern as collection).
3. Duplicate path args parity: pytest collects duplicates twice — match it in the walk/dispatch layer (cite the 1b.1 probe; add the differential test).
4. Exit codes for the full run: 0 all passed (xfailed/skipped count as ok), 1 any failed/xpassed-strict... probe pytest: plain xpassed is NOT a failure by default; error → 1 (pytest: errors make exit 1)... verify: pytest exits 1 on test failures AND on errors? Errors during collection → 2; test-phase errors → 1. PROBE ALL, cite, encode. 5 zero collected post-selection (with `no tests ran` parity check on counts only). 2 collection errors. 3 internal. 4 usage.
5. CLI `--v2 [paths...]` full run: line-per-test progress is NOT required (quiet summary counts to stderr fine — output UX is 1c); JSON report v2 via `--report-json` in v2 mode (schema v2: statuses six-valued, plus summary with xfailed/xpassed buckets); the conformance harness consumes this.
6. `Init` passes invocation_dir; protocol v2 handshake (old worker ↔ new orchestrator must fail loudly — test it).

- [ ] TDD incl. a mini full-run differential (mixed pass/fail/skip/xfail tree vs pytest: outcome counts + exit code); selection differentials (`-k "a and not b"`, `-m` expr) against pytest on isolated trees; per-test mutation verification on the exit-code mapper and the selection evaluator. Commit: `feat(v2): execute orchestration, selection expressions, --v2 CLI`

---

### Task 5: Full-run conformance gate + the un-waiving sweep

**Files:** Modify `conformance/harness/runners.py`, `grade.py`, `__main__.py`; BOTH waiver ledgers; `conformance/README.md`; new corpus case `marks/xfail-strict` (strict xpass shape, per Phase 0 Task 6's concern)

**Scope:**
1. `--v2-run` gate mode: run each corpus case through real pytest (execute, as the v1 gate does) and `rustest --v2 --report-json`; grade ordered ids (from the report), outcome counts per six-value mapping (pytest xfailed/xpassed parsed from its summary — extend `parse_pytest_summary` for those tokens), exit codes. Same isolation protocol as v2-collect. Stale-waiver machinery applies.
2. New ledger `conformance/waivers-v2-run.toml` — expected to be EMPTY at gate-green for the current corpus (every v1 execution bug is fixed in v2; adjudicate any residual divergence with full discipline — a non-empty ledger here needs mechanism-cited entries and is a finding to surface loudly in your report).
3. **The un-waiving sweep:** with v2 features landed, re-run the v2-COLLECT gate — `marks/mark-filter`, `marks/deselect-all`, `fixtures/parametrized-fixture` should now MATCH; stale-waiver detection forces removing them (empty v2-collect ledger). The V1 ledger stays as-is (v1 bugs remain in v1 — its gate documents them until Cleanup deletes v1) — UPDATE the v1 waiver texts to note "fixed in v2" with the fixing commit, keeping reasons accurate. Add `marks/xfail-strict` corpus case (corpus → 22; count assertions updated; adjudicate in all three gates: v1 waived predictably, v2 gates MATCH).
4. CI: conformance.yml runs all three gates.
- [ ] Per-test mutation verification for every new grading test; both scoreboards + the new one recorded in your report and the ledger. Commit: `feat(conformance): full-run v2 gate; un-waive v2 ledgers`

---

## Definition of done (Phase 1b.2)

1. All prior gates green; corpus at 22 cases; three gates green: v1 (waivers documenting v1 bugs, texts updated), v2-collect (ledger EMPTY), v2-run (ledger empty or mechanism-adjudicated).
2. #129/#131/xfail/exit-5 demonstrably fixed in v2 (the corpus cases MATCH under --v2-run).
3. Exit-code contract probed and encoded for the full run; selection expressions differential-tested.
4. Ledger updated; Phase 1c plan (compat-by-default, output UX, remaining builtin fixtures, session scope, capture fidelity) authored at this gate.
