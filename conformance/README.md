# Conformance & Benchmarks

The fitness function for the rustest v2 rewrite
(see `docs/superpowers/specs/2026-07-25-rustest-v2-architecture-design.md`).

- `python -m conformance` — run every corpus case through real pytest and real
  rustest, diff collected IDs + outcome counts + exit codes. Exit 1 on any
  unwaived divergence. `--only PREFIX` filters cases.
- `conformance/corpus/<area>/<case>/` — one directory per case: `test_*.py`
  files, optional `conftest.py`, optional `case.toml` (`[case] args = [...]`).
- `conformance/waivers.toml` — every known divergence with a mandatory reason.
  Phase gates are defined as this file shrinking. `NEW-BUG:` prefix marks
  divergences discovered by the corpus that the v1 audit didn't predict.
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

## Requirements

The conformance harness requires **Python >= 3.12** (rustest v2's floor, per
the 2026-07-25 architecture decision recorded in the spec above). The
`Conformance` CI workflow (`.github/workflows/conformance.yml`) pins its job
to Python 3.12 accordingly.
