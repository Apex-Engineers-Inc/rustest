# Evidence scripts

Ephemeral probe and mutation harnesses backing the task reports in `.superpowers/sdd/`.
**Not run in CI** — they exist so a report's tables can be re-derived rather than trusted.

| script | backs |
| --- | --- |
| `probe_exit_codes.py` | P1b.2 Task 4 §2 — the 23-row full-run exit-code table |
| `probe_dupes.py` | P1b.2 Task 4 §4 — pytest's duplicate-path-argument behaviour |
| `probe_selection.py` | P1b.2 Task 4 §1.3 — `-k` / `-m` matching semantics |
| `mutate_p1b2_task4.py` | P1b.2 Task 4 §9 — the 75-row mutation table |

Run them from the repository root with the project's interpreter, e.g.
`uv run python conformance/evidence/probe_exit_codes.py`.

`mutate_p1b2_task4.py` **edits tracked source files in place** and restores them after each
row. It restores on every exit path it controls, but a hard kill mid-row will leave a mutant
behind — check `git status` before trusting a subsequent test run. Its anchors are quoted
source text, so they rot as the code moves; a rotted anchor is reported as `BAD ANCHOR`
rather than silently skipped.
