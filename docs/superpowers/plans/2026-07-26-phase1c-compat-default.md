# Phase 1c: Compat by Default — the Product Flip

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox steps. **Speed regime (user directive): no routine re-reviews — the controller verifies fixes directly; full adversarial review only for Task 1 (product flip) ; mutation testing only where a frozen contract or the gate instrument changes; never run conformance gates concurrently.**

**Goal:** `rustest <paths>` — no flags — runs the v2 engine with pytest-compat always on, at parity with pytest on the corpus, with usable terminal output. The `--pytest-compat` flag dies; v1 remains reachable only via `--v1` (removed at Cleanup).

**Architecture:** The CLI default path routes to v2 (collect + execute + report). The compat shim installs unconditionally. Output gains a per-test progress line mode and keeps the byte-parity summary. Harness gains the `[EE]` harness-error marker (distinct from `[XX]` divergence). Corpus grows the shapes 1b.2 reviews accumulated.

**Tech Stack:** existing v2 stack; changes are mostly Python CLI/UX + corpus.

## Global Constraints

- pytest is the oracle; cite sources; differential tests for anything semantic. All three conformance gates green after every task (23+ cases), run sequentially only.
- v1 suites (`tests/`, `examples/tests/` via both runners) stay byte-identical to baseline except where a task's report documents an intended flip-related change.
- basedpyright strict; ruff clean; commit per task on `v2/phase0-conformance`; commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: The flip — v2 is the default path (FULL REVIEW)

- `rustest [paths...]` with no mode flag → v2 collect+execute+report (the Task 4 `--v2` path). `--v2` becomes a no-op alias (deprecation note); `--v1` opts into the legacy engine (banner: "legacy engine, removed in a future release"); `--pytest-compat` flag deleted (compat shim always installed in the v2 worker — already true) with a clear error pointing at the changelog.
- CLI surface parity for the flipped default: `-k/-m` (already), `-x/--exitfirst` (fail-fast: orchestrator stops dispatching after first failure — implement; probe pytest's `-x` exit semantics), `--lf/--ff` (wire to the existing cache keyed on v2 report ids — verify id-format compatibility with v1's cache or version the cache file), `-v` (per-test result lines in pytest's PASSED/FAILED wording), `-q`, `--no-capture`/`-s` (worker skips io redirection), `--report-json` (schema v2, already), `-n/--workers`.
- Fold-in ride-alongs: waiver grep-claim scoping (both entries, "outside src/v2/"); `TYPE_CHECKING` protocol block for mark typing (pytest's `_SkipifMarkDecorator` pattern); `decorators.py:566→568` line refs; bare-`usefixtures` warning divergence noted in §9 of the Task 6 report.
- The rustest self-suites (`tests/`, `examples/tests/`, README/docs codeblocks) must pass under the flipped default — this is the first real-world-ish suite v2 meets. Divergences: adjudicate loudly (fix v2 or document); this is expected to surface missing builtins (capfd, caplog, cache, mocker, request attributes) — implement the small ones, waive/`--v1`-gate the big ones with a written list for Phase 3.
- Exit-code contract, summary parity, and all three gates green; docs updated (README quickstart, CLAUDE.md dev commands unchanged since they name explicit runners).

### Task 2: Output UX + harness polish (spot-review)

- Default output: pytest-shaped per-file progress (dots/percent optional — match pytest's `-q`-style density by default with `-v` expanding), failure section with tracebacks from TestResult.message, summary line (already byte-parity), duration tail.
- Harness: `[EE]` marker + distinct status for harness errors (currently prints `[XX]`, the exact ambiguity that burned a verification run); `--only` no-match exits 1 with a message (currently silent 0); waived-case harness errors surface as `[EE]` not `[~~]`.
- Corpus additions from the 1b.2 backlog (adjudicate all three gates each): `fixtures/session-scope` (cross-file session fixture — documents the per-worker divergence loudly or matches at workers=1), `marks/pytest-exit` (pytest.exit() shape — the I1 keying case), `collection/dupe-args` (duplicate path args — pinned divergence today, candidates for fixing), `fixtures/module-param-reorder` (the reorder_items setup-count divergence — expected DIVERGE with mechanism waiver pointing at Phase 2/3 decision).

### Task 3: CI + benchmarks refresh (controller-verified only)

- conformance.yml: three gates sequential; add the v2 default-path smoke (`rustest conformance/corpus/collection/multi-file-ordering` exits 0).
- Benchmarks: extend `conformance/bench/bench.py` to also time the v2 default path (`rustest_v2_run_s` + fill the reserved `rustest_collect_s` via `--v2-collect-only`); regenerate `conformance/baselines.json` (full three sizes, sequential, note ordering-bias caveat still applies); record the first v1-vs-v2-vs-pytest comparison table in the README section — expectation management: v2 is NOT yet fast (spawn workers + no static tier); the numbers establish the Phase 2 baseline, and the report says so.

## Definition of done

1. `rustest <paths>` runs v2 by default; `--pytest-compat` deleted; three gates green (corpus 27+); self-suites pass under the default or divergences documented.
2. v2 baseline numbers committed; Phase 2 plan authored at this gate (static tier + cache + parallel tuning against these baselines).
