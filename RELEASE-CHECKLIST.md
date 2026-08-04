# Release checklist — the v2 arc

Everything below was verified against the tree and against GitHub, not inferred from notes.

> **§1's merge has been performed** on `v2/release-candidate` (PR #141), and §§3–5 have
> moved on accordingly — read §0 for what is left. **No tag was created and no publish was
> triggered**, and none can be from that branch: `publish.yml` fires only on a push to
> `main`. The release itself is still the maintainer's to run.

---

## 0. Where things stand

| | |
|---|---|
| Release branch | `v2/release-candidate` — **PR [#141](https://github.com/Apex-Engineers-Inc/rustest/pull/141)** |
| Superseded branch | `v2/phase0-conformance` (unchanged; the RC branched from its tip) |
| Merge with `origin/main` | **Done.** All 37 conflicting paths resolved per §1.2, plus three deviations §1.2 did not predict — see the merge commit |
| Version | `1.0.0rc1` (`pyproject.toml`) / `1.0.0-rc.1` (`Cargo.toml`) — an RC number for unambiguous git installs, **not** a decision to ship 1.0.0. See §2 |
| Version on `origin/main` | `0.18.0` |
| **Does merging publish?** | **Yes — this changed.** `1.0.0rc1` is not on PyPI, so `check-version` resolves `publish=true` and the merge push itself releases and deploys docs. The reviewable gap §6 step 1 used to promise is gone. **Decide the version before merging**, not after — see §6 step 1 |
| `publish.yml` 3.14 wheel fix (#132) | **applied** (`812b279`), not triggered |
| Deferred items closed on the RC | 1, 2, 3, 4, 5, 8 — see §4 |
| Deferred items still open | **3**: items 6, 7 and 9 (the version). None is a behaviour regression |

### What is left for you

1. **Review the docs.** `bash scripts/docs.sh build` → `great-docs/_site/index.html`. Builds
   with zero warnings. The seven beginner/CLI pages changed most: every console sample was
   re-captured from a real run.
2. **Try it on other projects** — install from the branch:
   ```bash
   uv pip install "rustest @ git+https://github.com/Apex-Engineers-Inc/rustest.git@v2/release-candidate"
   ```
3. **Decide the version** (§2) — **before** merging PR #141, because at `1.0.0rc1` the merge
   push publishes on its own. Then run the release (§6). Close issues only once it is live (§5).

---

## 1. Merge: `v2/phase0-conformance` → `main` — **DONE, kept as the record**

> **This section describes work already performed.** The merge was taken the other way
> round — `origin/main` merged **into** a `v2/release-candidate` branch cut from
> `v2/phase0-conformance` — which keeps "ours" meaning the v2 side throughout, exactly as
> §1.2's table assumes, while leaving `main` untouched until you merge PR #141. The section
> is kept because §1.2's resolution table is the reasoning behind the merge commit, and
> because §1.3's lesson turned out to apply again (three paths merged *cleanly* and wrongly;
> see the merge commit message).
>
> **Merging the PR is now a fast-forward** — the RC already contains `origin/main`. To keep
> §1.1's addressable-merge-commit property, use a **merge commit** on the PR rather than
> squash or rebase.

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

- [x] **~~Repair pre-commit, then run it.~~ DONE.** `uv python install --reinstall
      cpython-3.14.2` fixed it — the uv-managed interpreter was missing
      `libcrypto-3-x64.dll`, so `import ssl` failed, so `python -mvirtualenv` failed, so no
      hook environment could bootstrap and every commit in the previous two waves used
      `--no-verify`. **`uv run pre-commit run --all-files` is now fully green**, and every
      commit on the RC branch was made with the hooks running.

      One wrinkle worth knowing if it recurs: the reinstall first failed with
      `Access is denied` because two live processes were running out of that interpreter
      directory. Windows will not let uv replace it while anything holds it open — stop
      those first.
- [ ] `uv sync --all-extras && uv run maturin develop`
- [ ] `cargo fmt --check` · `cargo clippy --lib -- -D warnings`
- [ ] `cargo test --no-default-features -- --test-threads=1` — the `--no-default-features`
      half turns off `extension-module`, without which the test *executable* does not link
      on Linux. **On Windows, put the Python DLL directory on PATH
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
| 1 | ~~`tests/test_async_teardown_lifecycle.py`, an 18-test regression suite~~ | `2f6b751` (#122) | **CLOSED on the RC.** All 18 green. Overlap reconciled against the six existing async suites and there is none worth removing: `test_asyncio.py` covers async basics, `test_async_fixture_regression.py` session-fixture identity, `test_async_fixture_event_loop_issue.py` the loop mismatch — none of them assert what this one does, which is that a *previous* test's fixture teardown actually **completed**. Kept whole. | Done. |
| 2 | ~~Five new regression test files~~ | `053fa89` (#124) | **CLOSED on the RC.** Four arrived through the merge as clean adds and are green (`test_asyncio_config.py` 1, `test_xfail.py` 2+1 xfail+1 xpass, `test_relative_imports/` 2, `test_async_autouse_event_loop.py` 3). The fifth was re-authored — but not for the reason predicted. It did **not** produce a false failure: its autouse fixture *depended on* its session fixture, so the dependency edge forced the order and its assertions were tautologies that would pass against an implementation ordering fixtures exactly backwards. Replaced with a real pin, measured on pytest 8.4.2 across both axes: the order is `s_auto, s_req, f_auto, f_req` — **scope is the primary key, autouse the secondary within a scope**, which is the reverse of this table's old parenthetical. rustest produces the identical list; the new test asserts it under both runners, and additionally pins that a test's *parameter* order does not override scope. | Done. |
| 3 | ~~Console-output samples~~ | `0e7135d` (#126) | **CLOSED on the RC.** The count was wrong — not 9 sites but **34**, across seven pages. Every one re-captured from a real v2 run. The samples were worse than "stale": they showed a spinner, a progress bar, `✓✓✓` ticks and a `Collected N tests from M files` banner, **none of which this engine has any code to print** — there is no `isatty`/`IsTerminal` call anywhere in either layer, so output is identical piped or on a terminal. | Done. |
| 4 | ~~Two new sections in `async-event-loops.md`~~ | `053fa89` doc half | **CLOSED on the RC, and it was bigger than a gap.** The `Configuration` section is new. But probing to write it showed the page's *existing* central promise — "you don't need to configure anything", a session async fixture puts your test in the session loop — is **false in a project with no ini config**, because `asyncio_default_test_loop_scope` defaults to `function` and caps auto-detection. It reads as true here only because rustest's own `pyproject.toml` sets `session`/`session`. Measured both ways; the page now carries the precondition. main's autouse claim was **not** copied: it does not hold (measured). | Done. |
| 5 | ~~Two new sections in `test-classes.md`~~ | `053fa89` doc half | **CLOSED on the RC.** `setup_method`/`teardown_method`, fresh-instance-per-test, teardown-runs-on-failure, `setup_class`/`teardown_class`, and class-method fixtures sharing `self` — each verified by running it before it was written. Written as **executable** examples rather than main's skip-marked ones, so CI now tests them. | Done. |
| 6 | `pyproject.toml`'s `dev` extra → `[dependency-groups]` migration | `053fa89` | Functionally equivalent to this branch's `--all-extras` arrangement, and a merge-conflict candidate in the very file §1.2 flags. Note this arc **did** introduce a `[dependency-groups] docs` group, so the two mechanisms now coexist. | Move `dev` too, and update `uv sync --all-extras` to `--all-groups` in CI and `CLAUDE.md` together. |
| 7 | `fixture_registry.py:89-96`'s dead async-rejection message | Found by triage | Cosmetic, on a path documented as dead for a rustest run (reachable only from `compat/pytest.py:453-486`). | Delete the branch, or make the path reachable and pin it. |
| 8 | ~~Ten v1 speed claims across six pages~~ | Found by triage | **CLOSED by this wave** (`beba06d`). Listed here because the delta triage recorded it as the largest remaining docs debt, and it is now paid: every restatement of "8.5x average, up to 19x" is gone, replaced by the measured seventeen-suite figures. | Done. |
| 9 | The version bump | `1780db3`, `d3f6d5f` | Release-wave work, and a maintainer decision. | §2. |

**Three remain open** — item 6 (the `dev` extra → `[dependency-groups]` migration), item 7
(a dead async-rejection message on an unreachable path) and item 9 (the version bump, §2).
Items 1–5 and 8 were closed on the release candidate. **None of the three is a behaviour
regression**, and none blocks a release.

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

1. [ ] §1's merge has landed on `main` and CI is green on `main` HEAD.

   > **⚠ The gap this step used to promise no longer exists. Read before merging.**
   >
   > This step read: "Pushing the merge alone does **not** publish: `0.16.2` already exists
   > on PyPI, so `check-version` resolves `publish=false`. That is a useful property — it
   > gives a reviewable gap between merge and release." **That was true only while the
   > version was `0.16.2`.** The version is now `1.0.0rc1`, and `1.0.0rc1` is **not** on
   > PyPI — verified against `https://pypi.org/pypi/rustest/json`, whose `releases` map runs
   > `0.1.0` … `0.18.0` and contains no `1.0.0rc1`.
   >
   > So `check-version` resolves **`publish=true`**, and merging PR #141 runs
   > `build-wheels` → `build-sdist` → `publish` → `deploy-docs` on the merge push itself.
   > **Merging is the release**, and a published PyPI version can be yanked but never
   > replaced.
   >
   > It also splits the story if allowed to fire: pip skips pre-releases by default, so
   > `pip install rustest` would still resolve `0.18.0` while the docs site had already
   > redeployed describing v2 — users reading 1.0 docs against a 0.18 install.
   >
   > **Therefore: settle §2's version decision *before* the merge, not after.** The
   > merge-then-bump sequence in steps 2–4 below assumes a gap this tree does not have.
   > Either set the intended final version first and accept that the merge publishes it, or
   > park the version at something already on PyPI to restore the gap deliberately.
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

## 6.5 The first CI run the v2 arc ever had

**This branch had never been pushed, so PR #141 is the first CI run in the whole arc** — the
first time any of this code was built or tested anywhere but one Windows machine. Four
problems surfaced that no local run could have caught. None of the 12 test failures was a
regression from this work; each was a test or a behaviour that had simply never executed
under Linux, or under the interpreter CI was really using.

> **The four CI problems below are resolved**, and the matrix now genuinely runs three
> interpreters: **3.12.13, 3.13.14 and 3.14.6**. That is the first time 3.13 and 3.14 have
> been tested in this project at all.
>
> **This section used to read "every check is green". That was true when written and went
> stale**, which is worth recording because it is the second time this document has
> asserted a green state that a later commit invalidated (§1.2's conflict claim was the
> first). A *later* commit on this branch, `e0dc4a8`, ungated the failure report under `-q`
> and left `test_quiet_prints_only_the_summary` asserting the behaviour it had just
> removed — so all three matrix jobs failed on that one test. Fixed by rewriting the test
> to pin what the ladder now does. **The lesson is the same one as §1.3: a green run is
> evidence about the commit it ran on, not about the branch.** Re-read the PR's checks
> before trusting any "green" in this file.
>
> Found while probing that fix, and not fixed here: **`-q` is now indistinguishable from
> the default rung.** rustest's default output has no session banner, no `collected N
> items` line and no progress column, so once the failure report stopped being gated there
> was nothing left for `-q` to suppress. Only `-v` differs. Under pytest the two rungs
> genuinely differ. This follows from the deliberate no-`isatty` stance rather than being a
> bug, and the rewritten test pins the equality either way — but it is a fair question for
> a release claiming pytest ergonomics.

**Fixed on the RC:**

- `cargo test` could never have linked on Linux. `extension-module` was an unconditional
  pyo3 feature, which leaves the CPython symbols undefined for a host interpreter — correct
  for the wheel, impossible for a test *executable*. Windows links `pythonXY.lib` regardless,
  which is why `CLAUDE.md`'s command passed locally forever. Now a default feature, with CI
  running `cargo test --no-default-features`.
- `cli.py`'s `_colorize` import could not type-check at the 3.12 floor, and could not be
  suppressed either — `reportUnnecessaryTypeIgnoreComment` is an error too, so an ignore
  fails wherever the bare import would have passed. Now a dynamic import.
- **The Python matrix was not testing three Pythons.** `setup-uv@v3`'s `python-version`
  input did not take, and every job resolved the runner's system CPython 3.12.3, so `3.13`
  and `3.14` were never exercised. Fixed with `UV_PYTHON` plus an explicit
  `uv python install`, and a guard step that fails the job when the running interpreter is
  not the one the matrix asked for — so this cannot regress quietly.

### The 12 failures, resolved — and why they all appeared at once

> **The first triage's framing was wrong, and that is worth recording.** It read the 12 as
> *Linux* failures, because Linux was the visible new variable. They are not. **All three
> matrix jobs were running CPython 3.12.3** — the runner's system interpreter — so `3.13`
> and `3.14` were never tested at all, and the discriminator was the *version*. The giveaway
> was that all three jobs failed on exactly the same 12 tests, including tests whose subject
> only exists on 3.14. A version matrix that silently collapses to one interpreter is worse
> than no matrix: it reports three green checks for one configuration. Fixed, with a guard
> step that fails the job if the interpreter is not the one the matrix asked for.

| Cluster | Tests | What it actually was | Product bug? |
|---|---|---|---|
| **A** | 8 | GitHub sets `CI=true`, flipping pytest's `running_on_ci()` to a **full diff**, where rustest's stdlib `pprint.pformat` renders differently from pytest's vendored `PrettyPrinter`. **Already known** — `_assertion.py` l. 487-490 called that branch "unreachable at the pinned verbosity except on CI". | A real divergence, on that branch only. The tests now pin the default rendering (`CI`/`BUILD_NUMBER` stripped) and the divergence is asserted outright by `test_the_full_diff_branch_diverges_from_pytest`, so it fails the day it is closed. **Closing it means porting `_pytest/_io/pprint.py` — 673 lines.** A decision, not an oversight. |
| **B** | `test_symlinks_to_excluded_directories` | **Not a bug — the test asserted a guess.** It hardcoded `len(ids) == 1` under a comment admitting "symlink might be followed or not". Measured: pytest collects **2** on that tree too. Exclusion is by directory *name* (`venv_link` does not match `venv`) while the walk follows symlinks — pytest's `Dir.collect` is `scandir` + `direntry.is_dir()`, which defaults to `follow_symlinks=True` (`_pytest/main.py` l. 528-529). It never failed before because creating a symlink on Windows needs a privilege CI does not grant, so it always skipped. | **No.** rustest matches pytest exactly. Rewritten as a differential. |
| **C** | `test_unencodable_nodeids_are_escaped…` | The escape happens only when the child's stdout **encoding** cannot represent the name — cp1252 on Windows. On a UTF-8 stdout both runners print the id raw. The test pinned only the escaped spelling. | **No** — test portability. The byte-for-byte agreement between the two runners, which is the actual contract, held throughout. |
| **D1** | `test_annotation_code_objects_are_not_measured` | PEP 649 deferred annotations are **3.14+**. Before that a bare `x: int` in a class body is evaluated at class-definition time, so its line legitimately *is* executed and measured — there is no `__annotate__` code object to skip. The test was ungated. | **No.** Now `skipif(sys.version_info < (3, 14))`. |
| **D2** | `test_a_non_coroutine_awaitable_body_is_awaited` | **A real bug, and the one worth having found.** `asyncio.Runner.run()` accepts only a true coroutine before 3.14 — `Lib/asyncio/runners.py` l. 86-89 raises `ValueError: a coroutine was expected` — and **3.14 added** a wrapper for arbitrary awaitables (l. 96-104). So everything `_consume_test_result` does to duck-type on `__await__` the way pytest does (a `Future`, an anyio task wrapper, any object with `__await__`) stopped at the last step on 3.12 and 3.13. | **Yes.** The symptom was a false **red**: such a test failed citing asyncio internals rather than anything the user wrote. Fixed by `_as_coroutine`, which reproduces 3.14's wrapper where CPython lacks it. Verified failing, then passing, on a real 3.12. |

**Net: one real product bug (D2), one documented-divergence decision (A), three bad tests.**
D2 would have shipped in a 1.0 claiming 3.12 support, and could only have been caught by
actually running on 3.12 — which, until this branch was pushed, nothing ever did.

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
