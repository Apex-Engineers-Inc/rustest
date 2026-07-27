# Conformance & Benchmarks

The fitness function for the rustest v2 rewrite
(see `docs/superpowers/specs/2026-07-25-rustest-v2-architecture-design.md`).

- `python -m conformance` — run every corpus case through real pytest and real
  rustest, diff collected IDs + outcome counts + exit codes. Exit 1 on any
  unwaived divergence. `--only PREFIX` filters cases.
- `python -m conformance --v2-collect` — the **Phase 1b.1 gate**: diff pytest's
  collected node IDs and collection exit code against
  `rustest --v2-collect-only`. Nothing is executed, so nothing but IDs and the
  exit code is graded. Uses `waivers-v2-collect.toml`.
- `python -m conformance --v2-run` — the **Phase 1b.2 gate**: diff a real pytest
  run against `rustest --v2 --report-json`. Graded on the **ordered** node IDs
  the schema-v2 report carries, the **six-value** outcome tally
  (`passed/failed/skipped/xfailed/xpassed/error`) and the exit code. Uses
  `waivers-v2-run.toml`. `--v2-collect` and `--v2-run` are mutually exclusive.
- `conformance/corpus/<area>/<case>/` — one directory per case: `test_*.py`
  files, optional `conftest.py`, optional `case.toml` (`[case] args = [...]`).
- `conformance/waivers.toml` — every known divergence with a mandatory reason.
  Phase gates are defined as this file shrinking. `NEW-BUG:` prefix marks
  divergences discovered by the corpus that the v1 audit didn't predict.
- `conformance/waivers-v2-collect.toml` and `conformance/waivers-v2-run.toml` —
  the same discipline for the two v2 gates, kept separate because an entry in one
  ledger says nothing about the others (`collection/class-collection` is waived
  for v1 and matches under both v2 gates; `marks/xfail-strict` likewise).
  **Both v2 ledgers are empty**, which is the Phase 1b.2 result: every collection
  and execution divergence the corpus found in v1 is fixed in v2.
- `python -m conformance.bench.bench [--quick]` — pytest collect / pytest run /
  rustest v1 run / rustest v2 run / rustest v2 collect-only. As of Phase 1c
  Task 3 all five columns are live; Phase 2 will split the collect column into
  cold/warm rows once the manifest cache lands.

pytest is a dev-dependency only. It never ships with rustest.

## Case statuses

Each corpus case is graded into exactly one of five statuses, printed as
`[flag] area/case`. The run's exit code is 1 if *any* case is `DIVERGE`,
`STALE-WAIVER` or `HARNESS-ERROR`; matches and waivers alone exit 0.

| Flag | Status          | Meaning                                                                                                                                                     |
| ---- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ok` | `MATCH`         | pytest and rustest agree on collected IDs, outcome counts, and exit code.                                                                                    |
| `~~` | `WAIVED`        | They diverge, but the divergence is recorded in `waivers.toml` with a reason. Exits 0.                                                                       |
| `XX` | `DIVERGE`       | They diverge and there is no waiver for this case. Fails the run (exit 1).                                                                                    |
| `!!` | `STALE-WAIVER`  | The case now matches, but `waivers.toml` still carries a waiver for it. Fails the run (exit 1) — remove the waiver. Shrinking `waivers.toml` is the phase-gate metric, so a waiver that has quietly gone inert must not go unnoticed. |
| `EE` | `HARNESS-ERROR` | **The harness could not ask the question** — a malformed `case.toml`, a subprocess timeout, a runner that wrote no report. Fails the run (exit 1). **A waiver does not apply**: a waiver is a judgement about a known *divergence*, and no comparison happened for it to be about. |

`DIVERGE` and `HARNESS-ERROR` are counted separately on the summary line for
the same reason they have different flags: "the runners disagree about three
cases" and "the instrument fell over on three cases" are different problems
with different fixes, and they printed as the same `[XX]` until Phase 1c
Task 2. The cost of that ambiguity is on the record — a `1 diverged` reading
that turned out to be a subprocess timeout under concurrent load (P1b.2 Task 5
report §10.7), which is where this status was recommended.

Under `--v2-collect` the same five statuses apply, graded on collected IDs and
the collection exit code alone, against `waivers-v2-collect.toml`. Under
`--v2-run` they apply to ordered IDs, the seven-value tally and the exit code,
against `waivers-v2-run.toml`.

`--only PREFIX` that matches no case **exits 1** with a message naming the
prefix and listing the corpus. It used to print `0 cases: …` and exit 0, which
answered "all clear" to a question that was never asked.

## The `--v2-collect` comparison protocol

Both runners see the **same** configuration environment, because otherwise they
would not be answering the same question. `run_pytest` (v1 mode) pins pytest's
rootdir with `-c <empty ini>` and `--rootdir=<case dir>`; the
`--v2-collect-only` surface has neither flag in Phase 1b.1 and resolves config by
walking *up* from its working directory. Run in place, the two disagree about
rootdir for every in-repo case — this repo's `pyproject.toml` carries
`[tool.pytest.ini_options]`, so v2's IDs would read `conformance/corpus/…` while
pytest's read `test_x.py`, and every case would "diverge" on a prefix neither
runner is getting wrong.

So each case is **copied into a temporary tree** (minus `__pycache__`) with a bare
`pytest.ini` at its root, and both runners are invoked there with no config flags
at all. An empty `pytest.ini` is authoritative for pytest
(`_pytest/config/findpaths.py`) and for v2 (`src/v2/config.rs`), so both resolve
the same rootdir by their own unmodified rules and both emit case-relative IDs. A
case that ships its own config file keeps it.

A shipped config file is honored only when it would *actually* anchor the search,
decided on **content** the way both runners decide it (`pyproject.toml` needs
`[tool.pytest.ini_options]`; `tox.ini` needs `[pytest]`; `setup.cfg` needs
`[tool:pytest]`; `pytest.ini` qualifies by name even when empty, a section-less
`.pytest.ini` does not). Qualifying on mere existence would skip the bare ini for
a case shipping, say, a `[project]`-only `pyproject.toml` — both runners would then
walk up out of the copy, agree on the wrong rootdir, and record a **vacuous MATCH**.

Two further rules the grader follows:

- **Node IDs are compared verbatim and in order**, with no normalization, sorting
  or de-duplication on either side. v2's contract is byte-parity with pytest's node
  IDs *in pytest's collection order* — the name-sorted interleaved walk descends a
  directory at the position its own name sorts to, which a set comparison cannot
  see — and a duplicated ID collapses into a set silently. On a mismatch the grader
  reports the set difference (readable) *and* the first divergent index (complete).
- **stderr is never read.** v2 deliberately puts its summary and
  `ERROR collecting <path>` prose on stderr where pytest puts them on stdout, and
  the wording differs by design. Grading anything but stdout IDs and the exit code
  manufactures divergences out of diagnostics.

## The `--v2-run` comparison protocol

The same isolation protocol — copy the case out of the repo, drop a bare
`pytest.ini` unless the case ships config that would really anchor the search,
invoke both runners with no config flags. What differs is where the two halves of
the graded contract are read from.

**pytest** is invoked twice in the one isolated tree: `--collect-only -q` supplies
the **ordered IDs of the selected tests**, and `-q --tb=no` supplies the summary
line and the exit code. The IDs deliberately do *not* come from a `-v` run: `-v`
prints one line per *report*, and a body that passes with a teardown that raises
prints its ID twice (`PASSED` then `ERROR`), while the schema-v2 report carries one
reduced status per test. Grading reports against tests would manufacture an ID
divergence out of a difference that is real only in the counts.

**`rustest --v2`** is read entirely from `--report-json`: IDs verbatim and in order
from `tests[]`, the six buckets from `summary`, the exit code from the process.
Neither stream is parsed — worker stderr legitimately carries class/module teardown
output on a completely green run.

Two mappings the harness applies so the two sides answer the same question:

- **A collection error means nothing ran.** pytest's `pytest_runtestloop` raises
  `Interrupted` before the first item, so exit 2 leaves the executed-ID list empty
  however many IDs the collect pass listed; `src/v2/execute.rs::stage` encodes the
  identical rule. Without this the gate would pit pytest's *collected* set against
  v2's *executed* one.
- **Collection errors count in the `error` bucket**, because pytest reports a failed
  import as `1 error`. The JSON report keeps `collection_errors` separate from
  `summary.error` on purpose; the harness folds them exactly as v2's own terminal
  summary line does (`python/rustest/core.py::_run_summary`).

## Baselines (Phase 1c, v1 + v2, Windows)

Recorded with `python -m conformance.bench.bench --out conformance/baselines.json`
on Windows 11 / Python 3.14 against the generated all-passing suites, all three
sizes run sequentially with no other conformance suite concurrent. The raw
numbers live in the tracked `conformance/baselines.json`; `conformance/bench_results.json`
stays gitignored scratch for ad-hoc runs.

Phase 0 recorded pytest vs the v1 runner only, back when a bare `rustest` command
*was* the v1 runner. Phase 1c Task 3 regenerated the table below after the flip: the
v1 column now runs with an explicit `--v1`, and two columns are new —
`rustest v2 run` (the bare, flag-less command: the default path since the flip) and
`rustest v2 collect` (`--v2-collect-only`, reserved since Phase 0 and filled in now).

| files | tests | pytest collect | pytest run | rustest v1 run | rustest v2 run | rustest v2 collect |
| ----- | ----- | --------------- | ---------- | --------------- | --------------- | ------------------- |
| 10    | 100   | 0.99s           | 0.87s      | 0.51s           | 5.29s           | 5.82s               |
| 100   | 1000  | 2.52s           | 1.77s      | 0.99s           | 8.81s           | 8.52s               |
| 500   | 5000  | 7.86s           | 6.33s      | 3.27s           | 11.29s          | 8.51s               |

Derived marginal cost per test (`derived` in the JSON):

- pytest: **1140.7 us/test**
- rustest v1: **569.1 us/test**
- rustest v2: **618.1 us/test**

The derivation subtracts the second-largest size from the largest, cancelling the
fixed startup cost (interpreter boot, plugin loading, extension import) both runs
pay identically:

```
overhead_us = (run_s_big - run_s_small) / (tests_big - tests_small) * 1e6
```

**Read the v2 columns as two separate numbers, not one.** The *marginal* per-test
cost (618.1 us/test) is already in v1's neighborhood. The *wall-clock* numbers in
the table are nonetheless far worse than v1's across every size, because v2 pays a
large **fixed** cost on every single invocation: it spawns a fresh worker pool (up to
one process per CPU core — 16 on the recording machine, clamped to the file count for
the 10-file suite) with no static collection tier and no manifest cache to skip any of
that work on a warm re-run. v1 has none of that fixed cost (no worker pool to spawn at
all for these suite sizes). This is not a bug to chase down inside
Phase 1c — it is exactly the gap Phase 2
(`docs/superpowers/plans/2026-07-26-phase2-speed.md`) exists to close: a static Rust
collector, a manifest cache, and dispatch tuning, gated on warm collection
**≤ 50ms** at 5k tests and per-test overhead **< 200µs**, measured against this exact
table.

**Caveat — these are indicative, not rigorous.** Each cell is a *single sample*
taken in a *fixed command order* (pytest collect, pytest run, rustest v1 run,
rustest v2 run, rustest v2 collect) inside one freshly generated suite, so later
commands read a warm filesystem cache and warm `.pyc` files that the first command
paid to populate. Phase 0 review measured that ordering bias at roughly **27-39%**
on the original three-column table — larger than several of the gaps above, so no
per-row ratio here should be quoted as a speedup (and the v1/v2 gap is large enough
that the bias doesn't change its direction, only its exact size). Proper warmup
iterations, repetition with a median/min-of-N statistic, and order randomization
are still a **Phase 2 work item**; until then these numbers exist to catch
order-of-magnitude regressions and to anchor the Phase 2 targets above.

## Requirements

The conformance harness requires **Python >= 3.12** (rustest v2's floor, per
the 2026-07-25 architecture decision recorded in the spec above). The
`Conformance` CI workflow (`.github/workflows/conformance.yml`) pins its job
to Python 3.12 accordingly.
