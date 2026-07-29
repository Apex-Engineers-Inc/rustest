# The v1 conformance ledger (archived 2026-07-29)

`waivers.toml` in this directory is the complete, final state of the **v1 conformance
gate's** waiver ledger, moved here unchanged from `conformance/waivers.toml` when the v1
engine was deleted in Phase 4 Task 2.

## What it was

Between Phase 1b and Phase 4 the conformance harness ran **three** gates over the same
corpus, each with its own ledger, because an entry in one says nothing about the others:

| gate | invocation | ledger | what it graded |
|---|---|---|---|
| v1 end-to-end | `python -m conformance` | `waivers.toml` (this file) | pytest vs the **v1** engine: id sets, four outcome buckets, exit code |
| v2 collection | `python -m conformance --v2-collect` | `waivers-v2-collect.toml` | pytest vs `rustest --v2-collect-only`: ordered ids, exit code |
| v2 full run | `python -m conformance --v2-run` | `waivers-v2-run.toml` | pytest vs a flagless `rustest`: ordered ids, six-value tally, exit code |

The two v2 ledgers stay live in `conformance/`. This one is archived because the runner it
described no longer exists.

## What the 24 entries are

**They are v1's bugs**, not deferred work and not disagreements about what pytest means.
Each entry carries the probed evidence of a divergence, the mechanism with a `file:line`,
and — for every one that was closed — a **`FIXED IN V2 by <commit>`** citation naming the
commit that fixed it and the gate that proves it. Three commits close most of the list
(`159ff4c`, `051b9c5`, `0a975de`), and five entries also correspond to filed issues
(#129 unittest outcomes graded PASSED, #130 conftest loaded twice, #131 `skipif` ignored,
#136/#137 the bare-mark family).

The 24, by area:

- **collection** — `class-collection`, `empty-suite`, `unittest-basic`,
  `module-level-skip`, `all-modules-skipped`, `xunit-setup`
- **marks** — `bare-marks`, `deselect-all`, `skip-and-skipif`, `xfail`, `xfail-strict`,
  `pytest-exit`, `pytestmark-value`, `param-marks`
- **async** — `mode-default`, `mode-strict`, `session-loop-shared`, `loop-scope-mismatch`,
  `contextvar-isolation`
- **builtins** — `recwarn`, `capfd-fd-level`, `caplog-levels`
- **fixtures** — `autouse`
- **parametrize** — `indirect`

The file's own header comment records the scoreboard at each phase gate (1b.2 → 1c.2 → P3.1
→ P3.2) and states the rule this archive executes: *"These entries are deleted wholesale
when v1 is deleted, not one at a time."* Deleting them wholesale is what happened; keeping
the file is what this directory is for.

## Why it is kept

The ledger is the **record of what the v2 rewrite was for**. Read as a list it is the
answer to "what did the old engine actually get wrong, measured against pytest, case by
case" — and every entry that says `FIXED IN V2` is a claim that can still be checked, since
the corpus case it names is still in `conformance/corpus/` and still graded by the two
surviving gates.

Two entries are worth noting because they did **not** move to a v2 ledger as bugs:

- `async/mode-default` is also waived under `--v2-run`, and deliberately: rustest defaults
  `asyncio_mode` to `auto` where pytest-asyncio defaults to `strict`. That entry is a
  **design decision with its reasoning**, not a defect, and its v2 counterpart says so.
- `marks/pytest-exit` was closed as a divergence and survives in the v2 run ledger as a
  *grading* asymmetry (pytest has no machine-readable per-test list for a truncated
  session), which is a harness limitation rather than a runner one.

## Reading it

The file is TOML: one `[cases]` table whose keys are corpus case names
(`area/case-directory`) and whose values are the waiver prose. It is no longer loaded by
anything — `conformance/__main__.py` knows only the two v2 ledgers — so it can be read as a
document without any risk of it silently affecting a gate.
