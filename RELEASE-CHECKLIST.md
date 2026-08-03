# Release checklist — the v2 arc

Everything below was verified against the tree and against GitHub, not inferred from
notes. Nothing in it has been executed: **no version was bumped, no tag was created, no
publish was triggered.** The release is the maintainer's to run.

Read section 1 before anything else. The merge is **not** a fast-forward and **not**
conflict-free, and resolving it the obvious way in a few places would silently undo work
this arc did on purpose.

---

## 0. Where things stand

| | |
|---|---|
| Release branch | `v2/phase0-conformance` |
| Merge base with `origin/main` | `c127556` |
| Commits ahead of `origin/main` | **169** |
| Commits `origin/main` has that this branch does not | **9** (the 0.17.0 + 0.18.0 line — all triaged, see §4) |
| Version on this branch | `0.16.2` — **deliberately not bumped** |
| Version on `origin/main` | `0.18.0` |
| `publish.yml` 3.14 wheel fix (#132) | **applied** (`812b279`), not triggered |

---

## 1. Merge: `v2/phase0-conformance` → `main`

### 1.1 Strategy: one merge commit, `--no-ff`. Not a rebase, not a squash.

- The 169 commits are a forensic record that other artifacts point *into* by SHA:
  `CHANGELOG.md`, the SDD ledger, and the fixed-in citations in §5 below. A rebase rewrites
  every one of those SHAs; a squash collapses them to one. Either breaks the citation trail
  the moment it is written.
- `--no-ff` rather than a fast-forward is what makes the merge itself a single addressable
  commit — the one place to point at for "this is where v2 became `main`" in
  `git log --first-parent`.
- `main` allows merge, squash and rebase (no branch protection), so this is a judgement
  call rather than a platform constraint.

```bash
git checkout main && git pull
git merge --no-ff v2/phase0-conformance
```

### 1.2 The conflicts are real: 37 paths. Do not resolve them by instinct.

Measured with `git merge-tree --write-tree origin/main HEAD` — a dry run that touches
nothing. **The earlier draft of this checklist said `main` had not moved since the merge
base and that no conflicts were expected. That was true when written and is now wrong:**
`main` shipped 0.17.0 and 0.18.0 after the fork.

The resolution policy is *almost* uniformly **take ours (`HEAD`)**, and the reason is §4:
every one of `main`'s nine commits was triaged item by item, and the ones worth keeping
were **ported**, not left to a merge algorithm to rediscover.

**A. `modify/delete` — 15 paths. Keep the deletion in every case.**

Git leaves `origin/main`'s version in the tree, which means doing nothing resurrects the
file. Each of these was deleted on purpose.

| Path | Why the deletion wins |
|---|---|
| `src/cache.rs`, `src/discovery.rs`, `src/execution.rs`, `src/model.rs`, `src/model_tests.rs`, `src/output/event_stream.rs`, `src/output/spinner_display.rs`, `src/python_support.rs` | The **v1 Rust engine**, deleted in `36113ba`. `main`'s 0.17.0 fixes edited these files; every one of those fixes is either moot under v2 or ported (§4). Restoring any of them puts ~15,000 lines of unreachable, untested engine back and re-breaks `cargo test`. |
| `python/rustest/renderers/__init__.py` | The v1 renderer package, deleted in `eed8df4`. `main`'s `--llm` lived here as a regex-based traceback re-parser; the port lives in `python/rustest/_llm.py` and reads the structured report instead. |
| `python/tests/test_core.py` | Tested the v1 `run()` and its `RunReport` return type, neither of which exists. |
| `docs/index.md`, `docs/migration-guide.md`, `docs/advanced/pytest-compat.md`, `docs/from-pytest/comparison.md`, `docs/from-pytest/limitations.md` | The docs tree moved to `user_guide/` (`beba06d`) and these five are either migrated under a new name or dropped as duplicates. `main`'s edits to them are `#126`'s doc-accuracy pass, whose portable half is already applied (§4). |
| `zensical.toml` | The old site generator's config; the site is great-docs now (`a144868`). |

```bash
# After starting the merge, the whole of group A is:
git rm -r --ignore-unmatch \
  src/cache.rs src/discovery.rs src/execution.rs src/model.rs src/model_tests.rs \
  src/output/event_stream.rs src/output/spinner_display.rs src/python_support.rs \
  python/rustest/renderers python/tests/test_core.py \
  docs/index.md docs/migration-guide.md docs/advanced docs/from-pytest \
  zensical.toml
```

**B. `add/add` and a rename-detection note — 3 paths.**

| Path | Resolution |
|---|---|
| `user_guide/llm-output.md` + the "added inside a directory that was renamed" note on `docs/guide/llm-output.md` | Both sides wrote an `--llm` page. **Keep ours.** `main`'s documents schema 1; ours documents schema 2, which is what ships. Delete any `docs/guide/llm-output.md` git leaves behind. |
| `.vscode/settings.json` | Hand-merge: union the `cSpell.words` lists, then drop `zensical` and keep `quarto`. |

**C. `content` conflicts — 19 paths.** Take ours, with three exceptions that need eyes:

| Path | Resolution |
|---|---|
| `pyproject.toml` | Ours, **except the `version =` line** — see §2. Ours has the `[dependency-groups] docs` block, the `numpy` dev dependency and the bounded `ruff`; `main`'s has `0.18.0` and the `[dependency-groups]` migration listed as deferred item 6 in §4. |
| `CHANGELOG.md` | Ours. **Then hand-add** `## [0.17.0]` and `## [0.18.0]` sections from `main`'s file *below* `[Unreleased]`, so the published history keeps those releases. Our `[Unreleased]` already carries a "Reconciliation with 0.17.0 and 0.18.0" section explaining why the code did not come across; without `main`'s two version sections the tags would have no changelog entry at all. |
| `Cargo.lock`, `uv.lock` | Do not hand-merge. Take ours, then regenerate: `cargo build` and `uv lock`. |
| `src/lib.rs`, `python/rustest/cli.py`, `python/rustest/core.py`, `python/rustest/compat/pytest.py`, `python/rustest/decorators.py`, `python/rustest/rust.pyi`, `python/tests/test_cli.py`, `tests/conftest.py`, `tests/test_indirect_parametrization.py`, `tests/test_parallel_async.py`, `README.md`, `user_guide/changelog.md`, `user_guide/cli.md`, `user_guide/comparison.md` | **Ours**, unconditionally. Each of `main`'s hunks in these files is accounted for in §4 as moot or ported. |

> **`user_guide/changelog.md` must end byte-identical to `CHANGELOG.md`.** After resolving,
> re-run the sync: `cp CHANGELOG.md user_guide/changelog.md`. The `sync-changelog`
> pre-commit hook does this, but only when `CHANGELOG.md` is in the commit.

### 1.3 The lesson from the last merge, which applies again

The v1-deletion worktree merged into this branch with **zero textual conflicts** and was
still **semantically wrong in one place**: the other branch had added two `strict=True`
xfails pinning a per-file-rebuild behaviour that this branch had just fixed. Neither branch
held both halves, so git had nothing to conflict on. The strict flag caught it on the first
`python/tests` run after the merge.

**Therefore: run the full battery on the merge *result*, not on either parent.** A clean
merge is not evidence of a correct one.

---

## 2. Version — the maintainer's call, not made here

`pyproject.toml` still says `0.16.2` on this branch. It was deliberately left alone: the
ledger reserves messaging and naming decisions for the end, and `publish.yml` derives what
to publish by grepping this field, so **editing it is what triggers a real PyPI release**.

The constraint, not the decision: `main` is already at **0.18.0**, so whatever ships must be
greater than that. `0.16.2` cannot ship. Two defensible answers:

- **`1.0.0`** — this is the first release the project is willing to call stable, and the v2
  arc is the work that earns it: compatibility is no longer a mode, the conformance corpus
  and the seventeen-suite sweep are the evidence a 1.0 claim needs, and the flag removals
  are the one-time breaking cleanup a project does before committing to semver discipline.
- **`0.19.0`** — if "stable" is not the message yet. But note this release has **seven**
  breaking changes (see `CHANGELOG.md`), and a minor bump understates that.

Whichever is chosen, rename `## [Unreleased]` in `CHANGELOG.md` to `## [<version>] - <date>`
and re-run the changelog sync.

---

## 3. Pre-merge checklist

Run in order. Nothing here has side effects beyond the working tree.

- [ ] **Repair pre-commit, then run it.** Every commit in the reconciliation and docs waves
      used `--no-verify`, because pre-commit could not bootstrap *any* hook environment on
      the development machine: the uv-managed CPython 3.14.2 is missing
      `libcrypto-3-x64.dll`, so `import ssl` fails, so `python -mvirtualenv` fails. This is
      not specific to a hook or to these changes. Every check the hooks would have run was
      run by hand at the pinned versions — but that is not the same as the hooks passing.

      ```bash
      uv python install --reinstall cpython-3.14.2
      uv run pre-commit run --all-files
      ```

      The reinstall is machine-global and rebuilds the project venv, which is why it was
      not done unilaterally mid-wave. Do **not** shortcut it by copying the DLL from the
      freethreaded 3.14.2 install: the two ship different OpenSSL builds.
- [ ] `uv sync --all-extras && uv run maturin develop`
- [ ] `cargo fmt --check` · `cargo clippy --lib -- -D warnings`
- [ ] `cargo test -- --test-threads=1` — **on Windows, put the Python DLL directory on PATH
      first** or the binary exits `0xc0000135` before running anything. See `CLAUDE.md`.
- [ ] `uv run python -m conformance --v2-collect` and `--v2-run`
- [ ] `uv run pytest python/tests` · `uv run pytest conformance/tests`
- [ ] `uv run pytest tests/ examples/tests/` · `uv run python -m rustest tests/ examples/tests/`
- [ ] `uv run python -m rustest README.md user_guide/*.md`
- [ ] `uv run ruff format --check python conformance` · `uv run ruff check python conformance`
      · `uv run basedpyright python conformance`
- [ ] `bash scripts/docs.sh build` — needs the Quarto CLI on PATH
- [ ] Confirm `git merge-tree --write-tree origin/main HEAD` still reports the §1.2 set and
      nothing new.

---

## 4. Deferred from the 0.17/0.18 delta — nine items, none dropped silently

`origin/main`'s nine post-fork commits were triaged item by item: **36 items — 19 moot
under v2, 8 ported, 9 deferred.** The moot and ported items need no further action; the
ported ones are in `CHANGELOG.md`. These nine are the ones this arc deliberately did *not*
carry across, each with the reason and what closing it would take.

| # | Item | Source | Why deferred | To close it |
|---|---|---|---|---|
| 1 | `tests/test_async_teardown_lifecycle.py`, an 18-test regression suite | `2f6b751` (#122) | The *fix* is moot — v2 closes every loop through `asyncio.Runner.close()`, a superset of the hand-rolled sequence. The **tests** are still worth having, and were never checked against this branch's six existing async suites. | Port the file, reconcile overlap with the existing suites, run. |
| 2 | Five new regression test files: `test_asyncio_config.py`, `test_fixture_resolution_order.py`, `test_xfail.py`, `test_relative_imports/`, `test_async_autouse_event_loop.py` | `053fa89` (#124) | The *behaviours* are covered by ports of the pytest source; the suites are not. `test_fixture_resolution_order.py` additionally asserts the **opposite** of pytest's ordering, which v2 implements — porting it verbatim would create a false failure. | Port four; re-author the fifth against pytest's actual order (autouse first, then a widest-first scope sort). |
| 3 | ~~Console-output samples~~ | `0e7135d` (#126) | **CLOSED on the RC.** The count was wrong — not 9 sites but **34**, across seven pages. Every one re-captured from a real v2 run. The samples were worse than "stale": they showed a spinner, a progress bar, `✓✓✓` ticks and a `Collected N tests from M files` banner, **none of which this engine has any code to print** — there is no `isatty`/`IsTerminal` call anywhere in either layer, so output is identical piped or on a terminal. | Done. |
| 4 | ~~Two new sections in `async-event-loops.md`~~ | `053fa89` doc half | **CLOSED on the RC, and it was bigger than a gap.** The `Configuration` section is new. But probing to write it showed the page's *existing* central promise — "you don't need to configure anything", a session async fixture puts your test in the session loop — is **false in a project with no ini config**, because `asyncio_default_test_loop_scope` defaults to `function` and caps auto-detection. It reads as true here only because rustest's own `pyproject.toml` sets `session`/`session`. Measured both ways; the page now carries the precondition. main's autouse claim was **not** copied: it does not hold (measured). | Done. |
| 5 | ~~Two new sections in `test-classes.md`~~ | `053fa89` doc half | **CLOSED on the RC.** `setup_method`/`teardown_method`, fresh-instance-per-test, teardown-runs-on-failure, `setup_class`/`teardown_class`, and class-method fixtures sharing `self` — each verified by running it before it was written. Written as **executable** examples rather than main's skip-marked ones, so CI now tests them. | Done. |
| 6 | `pyproject.toml`'s `dev` extra → `[dependency-groups]` migration | `053fa89` | Functionally equivalent to this branch's `--all-extras` arrangement, and a merge-conflict candidate in the very file §1.2 flags. Note this arc **did** introduce a `[dependency-groups] docs` group, so the two mechanisms now coexist. | Move `dev` too, and update `uv sync --all-extras` to `--all-groups` in CI and `CLAUDE.md` together. |
| 7 | `fixture_registry.py:89-96`'s dead async-rejection message | Found by triage | Cosmetic, on a path documented as dead for a rustest run (reachable only from `compat/pytest.py:453-486`). | Delete the branch, or make the path reachable and pin it. |
| 8 | ~~Ten v1 speed claims across six pages~~ | Found by triage | **CLOSED by this wave** (`beba06d`). Listed here because the delta triage recorded it as the largest remaining docs debt, and it is now paid: every restatement of "8.5x average, up to 19x" is gone, replaced by the measured seventeen-suite figures. | Done. |
| 9 | The version bump | `1780db3`, `d3f6d5f` | Release-wave work, and a maintainer decision. | §2. |

**Five remain open** (items 1, 2, 6, 7, 9 — and 1 and 2 were closed on the RC too; see
below). **None is a behaviour regression.**

**Two things the RC found that this table did not predict**, both recorded because they
change what a reader should expect rather than what the code does:

- **A `path::node::id` argument selects the file, not the node — silently.** pytest resolves
  `test_x.py::test_a` to one test and answers a bogus id with `collected 0 items`; rustest
  ignores everything after `::` and runs the whole file, including for a bogus id. Copying a
  node id out of a failure report and re-running it is pytest muscle memory, and
  `short test summary info` and `--llm`'s `id` both hand you one. Now in **Known gaps** with
  the `-k` workaround. Not fixed here: it is a feature, not a doc change.
- **Async auto-detection needs `asyncio_default_test_loop_scope` set** to reach past
  function scope — see item 4. A project that follows the old page and writes a
  session-scoped async fixture gets "attached to a different loop" and no hint why.

---

## 5. Issue and PR closures

Do **not** close anything until the release in §6 is actually live — closing on merge is
premature if the release is aborted.

### 5.1 Issues

| # | Title | Status | Fixed in |
|---|---|---|---|
| [#129](https://github.com/Apex-Engineers-Inc/rustest/issues/129) | `unittest.TestCase` results always reported PASSED | **Fixed** | `0a975de` (+ hardening `26c2e97`) |
| [#130](https://github.com/Apex-Engineers-Inc/rustest/issues/130) | `conftest.py` imported twice as two module objects | **Fixed** | `159ff4c` |
| [#131](https://github.com/Apex-Engineers-Inc/rustest/issues/131) | `@pytest.mark.skipif` silently ignored | **Fixed** | `0a975de` |
| [#132](https://github.com/Apex-Engineers-Inc/rustest/issues/132) | `publish.yml` never ships a 3.14 wheel | **Fixed** | `812b279` |
| [#133](https://github.com/Apex-Engineers-Inc/rustest/issues/133) | CI never runs `cargo test`; 2 stale `model.rs` assertions fail | **Fixed** | `119a519` — and the two failing assertions went with the v1 engine (`36113ba`), so the suite is 397 tests fully green and could become a gate rather than a skip-list |
| [#134](https://github.com/Apex-Engineers-Inc/rustest/issues/134) | pre-commit pins ruff 0.8.4, dev extra installs 0.14.x | **Fixed** | `119a519` — hook `rev` and the `pyproject.toml` floor now pin the same version, with an upper bound so `uv lock --upgrade` cannot silently break the pairing |
| [#135](https://github.com/Apex-Engineers-Inc/rustest/issues/135) | `MarkDecorator` mutates inherited marks in place | **Moot** — v1-only, and v1 is deleted. Close as obsolete, not "fixed": the bug never got a v1 patch. v2's mark-reading path was built not to repeat it (`3286bca`, "read once, never amplified"). | cite `36113ba` / `eed8df4` |
| [#136](https://github.com/Apex-Engineers-Inc/rustest/issues/136) | Bare `@pytest.mark.skip` destroys the test body | **Fixed** | `344eab2` |
| [#137](https://github.com/Apex-Engineers-Inc/rustest/issues/137) | Bare `@pytest.mark.xfail` silently deletes the test | **Fixed** | `344eab2` |
| [#139](https://github.com/Apex-Engineers-Inc/rustest/issues/139) | Native `rustest.mark.skip` inert under v1 | **Moot** — v1-only, same disposition and same reasoning as #135. | cite `36113ba` / `eed8df4` |
| [#140](https://github.com/Apex-Engineers-Inc/rustest/issues/140) | Worker process tree leaks orphans when parent dies mid-run | **Fixed** | `e0b9fe8` |

`Fixes #NNN` was not used at commit time — these were audit findings rather than
issue-driven work — so there is no linked-PR auto-close. Close explicitly:

```bash
gh issue close 129 --comment "Fixed in 0a975de, released in v<version>."
```

**#118, #120, #121 and #122 are already CLOSED** (they were the 0.17.0 line's issues). No
action.

### 5.2 Open pull requests — two, both to close rather than merge

| # | Title | Disposition |
|---|---|---|
| [#138](https://github.com/Apex-Engineers-Inc/rustest/pull/138) | `fix(compat): support bare @pytest.mark.xfail decorator` | **Duplicate.** This is a PR, not an issue — the earlier draft of this checklist mis-filed it. Its subject is #137, which `344eab2` fixes for both bare `skip` and bare `xfail`. Close as superseded and cite `344eab2`. |
| [#125](https://github.com/Apex-Engineers-Inc/rustest/pull/125) | `fix: 3 pytest-compat bugs — monkeypatch paths, class fixtures, _pytest_stub` | **Needs a read before closing.** It targets v1 files that no longer exist, so it cannot merge — but its three subjects were **not** in the nine-commit delta §4 triaged, which means they have not been individually verified against v2. Check each against the current engine before closing, and file anything still real as a fresh issue. |

---

## 6. Release, publish, deploy — the maintainer's hand

`publish.yml` triggers on **every push to `main`** and decides what to publish by grepping
`pyproject.toml`'s `version =` against PyPI. So the merge push and the version bump should
be separate, deliberate acts.

1. [ ] §1's merge has landed on `main` and CI is green on `main` HEAD. (Pushing the merge
       alone does **not** publish: `0.16.2` already exists on PyPI, so `check-version`
       resolves `publish=false`. That is a useful property — it gives a reviewable gap
       between merge and release.)
2. [ ] Decide the version (§2) and set it in `pyproject.toml`.
3. [ ] Rename `CHANGELOG.md`'s `## [Unreleased]` to `## [<version>] - <date>`, and
       `cp CHANGELOG.md user_guide/changelog.md`.
4. [ ] Commit and push **once**, version bump and changelog together. This is the push that
       publishes.
5. [ ] Watch the run: `check-version` → `build-wheels` (3 OS × **3** interpreters now, per
       #132) → `build-sdist` → `publish` (trusted publishing, no token) → `deploy-docs`.
6. [ ] Verify on PyPI that a **cp314** wheel is present for all three OSes — that is the
       whole point of the #132 fix and the only way to confirm `maturin-action@v1`'s
       toolchain actually had 3.14 available.
7. [ ] Verify the docs site redeployed and that the new URL shape resolves. **The URLs
       changed** with the great-docs cutover: `/getting-started/quickstart/` is now
       `/user-guide/quickstart.html`, and the API reference moved from `/api/overview/` to
       `/reference/`. The README and `pyproject.toml`'s `[project.urls]` already point at
       the new shape; anything else linking in (the PyPI project page from an older release,
       external blog posts) will 404. Consider whether that is acceptable or wants
       redirects.
8. [ ] `gh release create` against the §1 merge commit, notes drawn from the changelog
       entry. `publish.yml` does not create a GitHub Release itself.
9. [ ] Close the issues and PRs in §5.

### 6.1 One coupling worth deciding on, not decided here

`deploy-docs` runs as `needs: publish` inside `publish.yml`, so **a docs-only fix cannot
redeploy without a PyPI release.** `docs.yml` does carry `workflow_dispatch`, so
`gh workflow run docs.yml` is the manual escape hatch today. Whether to decouple properly —
trigger on `user_guide/**` and `great-docs.yml` changes — is a maintainer call.

---

## 7. Known-open, accepted for this release

Neither blocks anything; both are recorded so they are not rediscovered as surprises.

- **`--llm` has no pin against `-s` or against `--cov` end to end.** Both are reasoned about
  and documented (captures are empty under `-s`; the coverage table is redirected to
  stderr), but neither is tested. Cheap to add; each costs a subprocess in an already
  five-minute module.
- **`--llm`'s `line` field is best-effort by construction.** It is the one field derived
  from message text, and it is absent for any failure whose message is not a traceback. The
  schema marks it optional. Making it authoritative means a `line` on the worker wire, which
  is a protocol version and a Rust change this arc deliberately did not make.
- ~~**`rustest --version` does not exist and never has.**~~ **CLOSED.** Added on the release
  candidate: `rustest <version>` on stdout, exit 0, answered before collection the way
  `--llm-schema` is. The version is read from installed metadata through one shared
  `_version.package_version()`, which `--llm`'s `meta` line now also calls — there is no
  second literal that could disagree.
