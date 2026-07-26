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
- `conformance/corpus/<area>/<case>/` — one directory per case: `test_*.py`
  files, optional `conftest.py`, optional `case.toml` (`[case] args = [...]`).
- `conformance/waivers.toml` — every known divergence with a mandatory reason.
  Phase gates are defined as this file shrinking. `NEW-BUG:` prefix marks
  divergences discovered by the corpus that the v1 audit didn't predict.
- `conformance/waivers-v2-collect.toml` — the same discipline for the
  `--v2-collect` gate, kept separate because an entry in one ledger says nothing
  about the other (`collection/class-collection` is waived for v1 and matches
  under v2; `collection/empty-suite` likewise).
- `python -m conformance.bench.bench [--quick]` — the three canonical numbers
  (pytest collect / pytest run / rustest run; rustest collect arrives in
  Phase 2).

pytest is a dev-dependency only. It never ships with rustest.

## Case statuses

Each corpus case is graded into exactly one of four statuses, printed as
`[flag] area/case`. The run's exit code is 1 if *any* case is `DIVERGE` or
`STALE-WAIVER`; matches and waivers alone exit 0.

| Flag | Status         | Meaning                                                                                                                                                     |
| ---- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ok` | `MATCH`        | pytest and rustest agree on collected IDs, outcome counts, and exit code.                                                                                    |
| `~~` | `WAIVED`       | They diverge, but the divergence is recorded in `waivers.toml` with a reason. Exits 0.                                                                       |
| `XX` | `DIVERGE`      | They diverge and there is no waiver for this case. Fails the run (exit 1).                                                                                    |
| `!!` | `STALE-WAIVER` | The case now matches, but `waivers.toml` still carries a waiver for it. Fails the run (exit 1) — remove the waiver. Shrinking `waivers.toml` is the phase-gate metric, so a waiver that has quietly gone inert must not go unnoticed. |

Under `--v2-collect` the same four statuses apply, graded on collected IDs and
the collection exit code alone, against `waivers-v2-collect.toml`.

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

Two further rules the grader follows:

- **Node IDs are compared verbatim**, with no normalization on either side. v2's
  contract is byte-parity with pytest's node IDs; normalizing would hide exactly
  the defect this gate exists to catch.
- **stderr is never read.** v2 deliberately puts its summary and
  `ERROR collecting <path>` prose on stderr where pytest puts them on stdout, and
  the wording differs by design. Grading anything but stdout IDs and the exit code
  manufactures divergences out of diagnostics.

## Baselines (Phase 0, v1 runner, Windows)

Recorded with `python -m conformance.bench.bench --out conformance/baselines.json`
on Windows 11 / Python 3.14 against the generated all-passing suites. The raw
numbers live in the tracked `conformance/baselines.json`; `conformance/bench_results.json`
stays gitignored scratch for ad-hoc runs.

| files | tests | pytest collect | pytest run | rustest run |
| ----- | ----- | -------------- | ---------- | ----------- |
| 10    | 100   | 1.15s          | 1.14s      | 0.58s       |
| 100   | 1000  | 2.65s          | 1.74s      | 1.06s       |
| 500   | 5000  | 7.52s          | 7.49s      | 4.04s       |

Derived marginal cost per test (`derived` in the JSON):

- pytest: **1436.3 us/test**
- rustest: **746.8 us/test**

The derivation subtracts the second-largest size from the largest, cancelling the
fixed startup cost (interpreter boot, plugin loading, extension import) both runs
pay identically:

```
overhead_us = (run_s_big - run_s_small) / (tests_big - tests_small) * 1e6
```

**Caveat — these are indicative, not rigorous.** Each cell is a *single sample*
taken in a *fixed command order* (pytest collect, then pytest run, then rustest
run) inside one freshly generated suite, so the later commands read a warm
filesystem cache and warm `.pyc` files that the first command paid to populate.
Review measured that ordering bias at roughly **27-39%** — larger than several of
the gaps in the table above, so no per-row pytest-vs-rustest ratio here should be
quoted as a speedup. Proper warmup iterations, repetition with a
median/min-of-N statistic, and order randomization are a **Phase 2 work item**;
until then these numbers exist to catch order-of-magnitude regressions only.

## Requirements

The conformance harness requires **Python >= 3.12** (rustest v2's floor, per
the 2026-07-25 architecture decision recorded in the spec above). The
`Conformance` CI workflow (`.github/workflows/conformance.yml`) pins its job
to Python 3.12 accordingly.
