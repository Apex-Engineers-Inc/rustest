# CHANGELOG draft — the v2 arc (DRAFT, not the real CHANGELOG.md)

> **Status: PREPARATION ONLY.** This is a restructured, headline-oriented draft of the
> entry that should eventually replace/consolidate `CHANGELOG.md`'s `[Unreleased]`
> section when the release ships. It is written from `CHANGELOG.md`'s existing
> `[Unreleased]` content (already substantial and accurate as of `c32cb2e`) plus the SDD
> ledger (`.superpowers/sdd/progress.md`), reorganized around what a *user* (not an
> implementer) needs to know, with `TODO` markers everywhere a number depends on the two
> lanes still in flight (v1 deletion, semantics review) or on the speed-offensive phase
> (4b) that ran concurrently with this research. **Do not copy this into the real
> `CHANGELOG.md` until every TODO is resolved and the user has signed off on the version
> number** (see `RELEASE-CHECKLIST-draft.md` section 2).

---

## [TODO: 1.0.0] - TODO: release date

### The headline

rustest's default test engine is now a ground-up Rust rewrite ("v2"): pytest
compatibility stopped being an opt-in mode and became the only behavior. Every run
resolves `import pytest` to rustest's own implementation; there is no flag that turns
this off short of `--v1`, which selects the old, now-frozen engine.

This release is the product of a conformance-driven rewrite: TODO(final count — was 52
cases as of the last recorded ledger entry, likely higher after lanes 2/4) corpus cases
diffed against real pytest on node IDs, outcome tallies, and exit codes; validated
against TODO(final count — was 17, "Coming from pytest" real-world sweep) real-world
open-source pytest suites (including a 6,000+ test internal suite) with a published
match/explained/diverge table; and — TODO(depends on the speed-offensive phase 4b,
which ran concurrently with this research and is not reflected in the numbers below) —
speed numbers to be finalized before release.

### Breaking changes

- **`--pytest-compat` is removed.** It used to opt into a compatibility *mode*; that mode
  is now the only mode, so the flag could only ever have been a no-op or a lie. Passing
  it now exits **4** (pytest's `USAGE_ERROR`) with a message pointing at this changelog.
  **Action required:** delete `--pytest-compat` from any CI command line, pre-commit
  hook, or script that still passes it.
- **The default engine changed.** `rustest <paths>` now runs the v2 engine
  unconditionally. If your suite depended on v1-specific behavior (see "Known gaps"
  below, and the v1-only bugs in the "Bugs fixed" table), pin `--v1` explicitly while you
  migrate, or read the gap list to see whether it's already closed.
- **Python 3.12 is now the floor** (`requires-python = ">=3.12"`), because the v2
  engine's coverage integration uses `sys.monitoring` unconditionally, which does not
  exist before 3.12. 3.10 and 3.11 are no longer supported. *(This landed earlier in the
  arc, not at the v2 flip itself — called out here because it's still a breaking change
  relative to the last stable release, `0.16.2`, which supported 3.10+.)*
- **`rustest.run()` (the Python API) still drives the v1 engine**, undocumented as such
  until this arc's docs pass. If you call `rustest.run()` directly rather than using the
  `rustest` CLI, you are on the frozen legacy engine today; a v2 Python API is
  TODO(tracked for a later phase — confirm status with the endgame implementer before
  publishing this line, since it may have landed during the v1-deletion lane).
- **`indirect=` parametrization semantics changed** to match pytest's own: the value now
  names a *fixture*, routed through `request.param`. It used to be rustest-specific
  (the value was read directly as a fixture name). See the parametrization guide for the
  one-line change that reproduces the old behavior if you need it.
- TODO(v1-deletion lane): if v1 is removed entirely in this release (rather than kept
  behind `--v1`), that is itself a headline breaking change and needs its own top-line
  bullet here, plus removal of every "known gap — use `--v1`" caveat below, plus a
  decision on issues #135/#139 (v1-only bugs that become moot rather than fixed — see
  the release checklist section 5).

### Added

- `-x` / `--exitfirst` — stops dispatch after the first failure; pytest's `--maxfail=1`
  semantics, sequential-exact at `-n 1`.
- `--lf` / `--ff` — last-failed / failed-first, backed by a v2-specific cache at
  `.rustest_cache/v2/lastfailed` (kept separate from v1's cache; the two do not collide).
- Verbose (`-v`) and quiet (`-q`) output now match pytest's own wording and percent
  column (`PASSED`/`FAILED`/`SKIPPED (reason)`/`XFAIL`/`XPASS`/`ERROR`); `-v -q` cancel
  out as they do under pytest.
- A pytest-shaped failure report: `ERRORS` / `FAILURES` / `short test summary info`
  sections with pytest's own separator rules, so a red run is read the same way whether
  it came from pytest or rustest.
- `-s` / `--no-capture` on the default engine (output reaches stderr, since a worker's
  stdout is the protocol channel).
- Markdown code-block testing works on the default engine, including directory walking
  (`rustest README.md docs/` — though see the great-docs migration note in the main
  report about `.qmd` if the docs site has migrated by release time).
- `pythonpath` ini support (`type="paths"`, applied to the worker's `sys.path` before
  import) — a `src/` layout now works without an editable install, closing a gap noted
  earlier in this same arc.
- Coverage integration via `sys.monitoring` (no `pytest-cov` plugin dance) — `--cov`,
  opt-in, adds nothing to a run that doesn't ask for it.
- Native async support: `@mark.asyncio` needs no plugin; loop scopes ported from
  pytest-asyncio's model (function/class/module/session), configured via the standard
  ini keys.
- A worker's full process tree is now torn down with it (job-object containment on
  Windows) — closes #140, a real leak that could otherwise leave orphaned child
  processes (e.g. subprocess-spawning tests) running indefinitely after a crashed or
  killed rustest run.

### Fixed — real bugs, not just v2-parity work

These were found by the conformance corpus (comparing rustest against real pytest
behavior) and are genuine correctness fixes, not new features:

- **`unittest.TestCase` failures/errors/skips are no longer silently reported as
  PASSED.** (#129) This was a real, shipped defect: any `unittest`-style test that
  failed, errored, or was skipped showed green. Fixed by routing through
  `unittest.TestResult` callbacks the same way pytest itself does, rather than
  discarding the result object.
- **`conftest.py` is no longer imported twice as two separate module objects.** (#130)
  Shared state written by one test and read via `import conftest` in another now
  actually is shared — it silently wasn't before.
- **`@pytest.mark.skipif` is no longer ignored.** (#131) A skip-guarded test body used
  to execute anyway; the condition is now evaluated, including string conditions
  (`eval`'d exactly as pytest does).
- **Bare `@pytest.mark.skip` (no parentheses) no longer destroys the test body.** (#136)
  It used to replace the test function with the decorator's own closure.
- **Bare `@pytest.mark.xfail`/`@pytest.mark.skipif` (no parentheses) no longer silently
  deletes the test from collection.** (#137) The test used to vanish with no error and
  exit 0.
- **A failing `async def` test now fails**, instead of being awaited incorrectly and
  reported PASSED. Async fixtures got the same fix. This was found in this project's own
  test suite: 100+ of rustest's own async tests were passing vacuously before this fix.
- **`pytest.exit()` now actually stops the session** (exits 2, keeps results already
  produced) instead of being a silent no-op that let every subsequent test keep running.
- TODO(v1-deletion lane / semantics review): add any additional bugs those two lanes
  find. In particular check whether #135 (`MarkDecorator` mutates inherited marks in
  place) needs a line here — v2 avoids the defect structurally rather than patching it,
  and its final disposition (fixed vs. moot-because-v1-is-gone) is TODO per the release
  checklist.

### Known gaps as of this draft (confirm each is still accurate before publishing)

TODO(endgame implementer): this list is copied from `CHANGELOG.md`'s `[Unreleased]`
section and `docs/advanced/pytest-compat.md` as they stood on `c32cb2e`. Some of these
may have closed during the concurrent lanes — re-verify every line, don't publish stale
gaps as current:

- No parallel async batching (same-`loop_scope` tests run sequentially); `loop_scope` is
  accepted but only one event loop per worker.
- No automatic `src/` layout insertion beyond the `pythonpath` ini support added above —
  pytest doesn't do this either, so this is parity, not a gap, but worth a line since v1
  *did* do it automatically (a behavior change, covered under "Breaking changes" too).
- Several pytest built-in fixtures not yet implemented: `capfdbinary`, `capsysbinary`,
  `capteesys`, `doctest_namespace`, `pytester`, `record_property`,
  `record_testsuite_property`, `record_xml_attribute`, `recwarn`, `testdir`. Requesting
  one is a loud, named error, not a generic "fixture not found."
- No pytest plugin support (by design — see the plugin compatibility guide for
  alternatives to the top 10 most popular plugins).
- No hook system (`pytest_configure`, `pytest_collection_modifyitems`,
  `pytest_generate_tests`); a conftest's fixtures load, its hooks are ignored.
- No warnings channel yet (pytest diagnostic messages aren't printed; behavior matches,
  message doesn't).
- `session`/`package`-scoped fixtures rebuild per file, not once per run.
- No item reordering for shared higher-scoped parametrized fixtures.
- `xfail_strict` ini and `--runxfail` not implemented (the `strict=` keyword works).
- Capture is stream-level, not file-descriptor-level, on the *default* engine
  (`capfd`-style fd-level capture is one of the not-yet-implemented fixtures above).

### Performance

TODO(entire section — blocked on the speed-offensive phase 4b, which ran concurrently
with this documentation-prep research and is explicitly the reason numbers are not
final yet). Do not publish a headline multiplier until that phase's own report is in and
the user has reviewed it. For reference, the last recorded state before this prep lane
started (NOT for publication, context only):

- 17-suite real-world wall-clock table: 13 MATCH / 4 EXPLAINED / 0 DIVERGE, ratios
  ranging roughly 1.17x–5.66x per-suite (psutil lowest, fastapi highest), with two
  body-bound giants (psutil, an internal 6k-test suite) dragging the aggregate to ~1.23x
  and the rest of the fleet averaging ~2.74x.
- Synthetic 5,000-test baseline: pytest 8.30s, v1 4.80s, v2 2.07s (4.0x vs pytest, 2.3x
  vs v1) — but the speed-offensive phase's own verdicts (default-to-default multipliers
  reported as high as 8.5x/7.1x/4.7x on synthetic suites, 6.9x on a real one) landed
  *after* this baseline was recorded and are not yet reconciled into one number.
- **The existing README.md hero copy ("8.5× average speedup") is a v1-only number and is
  known-stale for v2** — already flagged in the project's own execution ledger as a
  "Cleanup-phase work item" for the user to resolve at the very end, not something this
  changelog draft should quote as current. Do not carry it forward into the real
  changelog without the user's explicit sign-off on which number replaces it.

### Migration

- If you were passing `--pytest-compat`: stop. It's the default now; just run
  `rustest tests/`.
- If you need the old engine while you migrate: `rustest --v1 tests/`. It is frozen (no
  further fixes) and TODO(v1-deletion lane): may not exist at all by the time this ships
  — confirm before publishing this bullet.
- Full gap-by-gap detail: `docs/advanced/pytest-compat.md` (already rewritten for v2 as
  of this research — confirmed current, not stale, unlike several of its sibling pages;
  see the migration map in the main report).

---

## Format note for whoever finalizes this

The real `CHANGELOG.md` already has almost all of this content, but organized
implementer-first (grouped by `### Changed` / `### Added` / `### Fixed` / `### Known
gaps`, in the order features landed). This draft reorganizes it user-first (headline,
breaking changes, real bug fixes separated from v2-parity plumbing, gaps, performance,
migration) because that's the shape a release announcement needs, not the shape a
running engineering log needs. Recommend keeping `CHANGELOG.md`'s existing entry as the
detailed record and using this draft's structure for the top-level summary / GitHub
Release notes, rather than replacing one with the other outright — that decision belongs
to the endgame implementer, not this prep lane.
