# Phase 4b: The Speed Offensive — Real-Suite Wall-Clock, Head-On

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Runs AFTER Task 1c's green baseline, BEFORE any docs work (user directive 2026-07-28: speed work may change the project; docs would be invalidated). No releases. Sequential measurement discipline throughout — a timing phase run on a noisy machine is worthless.

**Goal:** Close the gap between component wins (37x collection, 8x per-test overhead) and real-suite wall-clock (2-3x). Target: demonstrable, honest multi-x improvements on the framework-owned share of every suite in the seventeen-table, with an explicit per-suite decomposition showing what is framework and what is user-code (Amdahl ceiling stated, not hidden).

## The evidence that frames this phase

- Warm collect 227ms @5k vs pytest 8.4s — delivered but a small share of full-run wall-clock.
- Overhead 118µs/test vs pytest 934µs — delivered; on 7,815-test jsonschema that is ~6.3s saved and the suite showed 2.2x. Consistent.
- Pool defaults to cpu_count workers (core.py::_pool_size) yet several suites ran pinned or effectively serial. The multiplier is implemented but possibly not DELIVERING.
- v1's async gather-batching was dropped for pytest-asyncio fidelity — a deliberate wall-clock sacrifice on async suites.

## Standing hypotheses (profiling confirms or kills each BEFORE any optimization)

- H1 **Per-worker import amplification:** pytest imports the target stack once; rustest imports it in EVERY worker (numpy/scipy ~1-2s × N workers = burned CPU + latency; Windows spawn has no COW). Predicts: heavy-import suites gain little from more workers; may even lose.
- H2 **Pin reasons:** jsonschema and Member Designer ran `-n 1` — why, exactly, per suite? (Shared external state like MD's mongo is user-inherent; anything harness- or engine-caused is ours to fix.)
- H3 **File-granularity load imbalance:** stem-hash routing + file-grouped dispatch means one huge file = one hot worker while others idle (jsonschema's generated classes may concentrate).
- H4 **Async serialization:** oracle-faithful sequential async leaves event-loop concurrency on the table for io-bound suites (MD). pytest-asyncio is ALSO sequential, so parity holds — but v1 was faster here and users will compare.
- H5 **Capture/protocol per-test costs at scale** (JSON-lines round trips, io redirection) — measured at 118µs/test synthetic; real suites with large outputs may differ.
- H6 **Body-bound Amdahl:** Pynite/MD bodies dominate; ceiling = framework share. Must be QUANTIFIED per suite so expectations are honest.

---

### Task 1: The profile — per-suite wall-clock decomposition (controller-reviewed evidence)

- Instrument a profiling mode (env-gated, e.g. RUSTEST_PROFILE=json): per-run phase timings (spawn, per-worker import/init, collect, dispatch/protocol, body time aggregate, capture, teardown, report) — worker- and file-attributed. No always-on cost.
- Run the decomposition on SIX representative suites: jsonschema (framework-heavy, huge N), more-itertools (mid), Pynite (body-heavy numeric), Member Designer test_lib or startup tree (async+db), click (mid, deselection-heavy), the 5k synthetic (control) — pytest side too (py-spy or -X importtime + duration accounting) for the comparison baseline.
- Deliverable: a table per suite — total, framework share, body share, per-worker import cost, worker utilization/imbalance — and a verdict per hypothesis H1-H6. NO optimization in this task.

### Task 2: The fixes the profile justifies (FULL REVIEW on any semantics-adjacent change)

Menu — implement what H-verdicts justify, skip what they kill (each with before/after numbers on the affected suites):
- H1 remedies: worker-count heuristic (cap by import-cost estimate or measured first-worker init), optional shared pre-import... (no fork on Windows — honest options: smaller default pools for heavy-import suites via first-worker timing feedback, or a persistent warm-pool daemon mode as an OPT-IN experiment clearly out of default path).
- H3: size-aware routing (split giant files' tests across workers where module state permits — CAREFUL: module fixtures; likely restrict to files with no module/class-scoped state, detector-style conservatism) or at minimum within-worker file ordering by size.
- H4: opt-in concurrent async within a loop scope (a rustest extension mark/config, DEFAULT OFF, documented as divergence — revisit of v1's batching under v2 semantics) IF profiling shows MD-class suites are io-bound waiting.
- H5: batch/protocol tuning as measured (larger batches, buffered writes).
- Re-run the SEVENTEEN-suite wall-clock table (the same one, same discipline) — the phase's headline deliverable, with per-suite framework-share speedup AND total speedup, honestly split.

### Task 3: Benchmark + gate refresh (controller-verified)

- bench.py gains the decomposition columns; baselines regenerated; conformance gates re-verified green (speed work must not move a single verdict — any verdict change is a Critical regression).
- The final numbers document (report-only; feeds the deferred README) with the Amdahl framing: "on framework-bound suites X-Yx; on body-bound suites the framework share shrinks Nx but totals are body-limited."

## Definition of done

1. Every H1-H6 has a measured verdict; every applied fix has before/after on named suites; no conformance verdict moved.
2. The seventeen-suite table regenerated under the improved engine; framework-share and total speedups reported per suite.
3. User reviews the numbers BEFORE the docs phase proceeds (explicit checkpoint — the user asked to see the speed story resolved first).
