# Evidence scripts

Ephemeral probe and mutation harnesses backing the task reports in `.superpowers/sdd/`.
**Not run in CI** — they exist so a report's tables can be re-derived rather than trusted.

| script | backs |
| --- | --- |
| `probe_exit_codes.py` | P1b.2 Task 4 §2 — the 23-row full-run exit-code table |
| `probe_dupes.py` | P1b.2 Task 4 §4 — pytest's duplicate-path-argument behaviour |
| `probe_selection.py` | P1b.2 Task 4 §1.3 — `-k` / `-m` matching semantics |
| `mutate_p1b2_task4.py` | P1b.2 Task 4 §9 — the 75-row mutation table |
| `mutate_p1b2_task5.py` | P1b.2 Task 5 §8 — the 30-row mutation table for the `--run` gate |
| `mutate_p1b2_task6.py` | P1b.2 Task 6 §8 — the 11-row mutation table for the bare-mark discriminator (#136/#137) |

Run them from the repository root with the project's interpreter, e.g.
`uv run python conformance/evidence/probe_exit_codes.py`.

All three `mutate_*.py` scripts **edit tracked source files in place** and restore them after
each row. They restore on every exit path they control, but a hard kill mid-row will leave a
mutant behind — check `git status` before trusting a subsequent test run. Their anchors are
quoted source text, so they rot as the code moves; a rotted anchor is reported as
`BAD ANCHOR` rather than silently skipped.

The tables aim at different targets, which is worth keeping straight: Task 4's and Task 6's
mutate **the runner** (`src/engine/*`, `python/rustest/*`) and ask whether the tests would
notice a behaviour change; Task 5's mutates **the conformance harness** and asks whether the
gate would notice if it stopped grading something. The gate itself answers the first
question for the run surface, so duplicating it there would be circular.
