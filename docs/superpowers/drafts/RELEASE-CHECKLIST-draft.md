# Release Checklist (DRAFT — v2 arc)

> **Status: PREPARATION ONLY.** Written by the docs-endgame prep lane while two other
> lanes are still in flight: (1) v1 deletion, in an isolated worktree, and (4) a
> semantics review. Every number in this file that depends on their outcome is marked
> `TODO`. Do not execute any step here until both lanes have landed on
> `v2/phase0-conformance` and the endgame implementer has confirmed the TODOs. This file
> is not linked from anywhere and is not part of the published docs site.

---

## 1. Merge strategy: `v2/phase0-conformance` → `main`

**Facts, measured 2026-07-29:**

- `main` and `v2/phase0-conformance` share merge-base `4a314e7`.
- `v2/phase0-conformance` is **144 commits** ahead of `main` (not yet the whole story —
  the v1-deletion lane is landing more commits on top as a separate worktree at the same
  tip; re-count immediately before merging).
- `main` has no branch protection and allows merge, squash, and rebase (`gh api
  repos/Apex-Engineers-Inc/rustest --jq '{allow_merge_commit,allow_squash_merge,allow_rebase_merge}'`
  → all `true`), so the choice below is a judgment call, not a platform constraint.

**Recommendation: a single merge commit (`git merge --no-ff`), not a rebase and not a
squash.**

Rationale:

- The 144 commits are already a forensic record other artifacts point *into* by SHA:
  `CHANGELOG.md`'s `[Unreleased]` section, the SDD ledger (`.superpowers/sdd/progress.md`),
  and — critically — the fixed-in citations this checklist is about to write into GitHub
  issues #129–#140 (section 5). A rebase rewrites every one of those SHAs; a squash
  collapses them to one. Either breaks the citation trail the moment it's written.
- The branch has been a deliberate long-running integration branch since Phase 0 (its own
  name says so), built by a chain of implementer → reviewer → fix-wave commits. That
  structure is itself documentation of how each defect was found and closed — worth
  keeping, not worth flattening.
- A rebase of 144 commits across ~4 days of concurrent-lane work is real conflict risk for
  no offsetting benefit: `main` hasn't moved (the user's explicit "no releases until v2 is
  entirely finished" directive kept it static), so there's no linear-history problem a
  rebase would be solving.
- `--no-ff` (rather than a fast-forward merge) is what makes the merge itself a single
  addressable commit — useful as the one place to point "this is where v2 became default
  on `main`" for anyone reading `git log --first-parent` later.

**Order of operations (fill in once lanes 2 and 4 report done):**

1. TODO: confirm the v1-deletion worktree branch has merged *into* `v2/phase0-conformance`
   (not directly into `main`) — this checklist assumes one branch lands on `main`, not two
   racing ones.
2. TODO: confirm the semantics review (lane 4) filed its findings and any fix-wave commits
   are on `v2/phase0-conformance` before the cut.
3. Re-run the three conformance gates + `cargo test` + `uv run pytest python/tests` +
   `uv run pre-commit run --all-files` on `v2/phase0-conformance` tip — the last-known-green
   state is c32cb2e (2026-07-28/29); re-verify green after the two lanes land on top.
4. `git checkout main && git merge --no-ff v2/phase0-conformance`
5. Resolve conflicts if any (none expected — `main` is untouched since the merge-base).
6. Push `main`. **`publish.yml` triggers on every push to `main`** — see section 3 for why
   that must not run unpatched, and see section 4 for the tag-first alternative.

---

## 2. Version proposal: **1.0.0**

Current `pyproject.toml` version is `0.16.2`, classified `Development Status :: 3 - Alpha`.

**Recommendation: `1.0.0`, not `2.0.0`.**

Rationale:

- Semver's major version is a promise about API/behavior *stability*, not an internal
  codename. "v2" is this project's own name for its rewritten engine; it is not a
  public-facing version number and conflating the two would be confusing (users would
  reasonably ask "where did 1.x go?").
- `1.0.0` is the more honest signal: this is the *first* release the project is willing to
  call stable. Everything before it was `0.x` (anything-may-break) alpha. The v2 arc is
  exactly the work that earns that: pytest-compat is no longer a mode, it's the only
  behavior; the conformance corpus and the 17-suite real-world sweep are the evidence a
  1.0 claim needs; the `--pytest-compat` flag removal and `--v1` quarantine are the kind of
  one-time breaking cleanup a project does right before committing to semver discipline
  going forward.
- Counter-consideration (why someone might argue for `2.0.0` instead): if the intent is to
  telegraph "this is a ground-up rewrite, treat it with the suspicion of a major version
  bump" independent of prior stability claims, `2.0.0` reads that way to a wary adopter.
  Weigh this against the `1.0.0` case above — it's a legitimate alternative, not a wrong
  one, and the final call belongs to whoever owns the public messaging (the user's own
  directive: "README/messaging rewrite happens at the VERY END").
- Either way: **this is a breaking release** (CHANGELOG `[Unreleased]` → `[1.0.0]`,
  `--pytest-compat` removed, default engine changed, `rustest.run()` still v1 needs a
  documented caveat — see the CHANGELOG draft). Do not ship it as a `0.17.0` minor bump.

TODO (endgame implementer): confirm final call with the user before editing
`pyproject.toml`'s `version =` field — this prep lane does not touch product files.

---

## 3. `publish.yml` review

Read in full (`.github/workflows/publish.yml`, 2026-07-29). Findings:

### 3.1 — Issue #132: no Python 3.14 wheel, despite `pyproject.toml` supporting it

`pyproject.toml` declares `requires-python = ">=3.12"` and classifies
`Programming Language :: Python :: 3.14`. `CLAUDE.md` and CI both test 3.12–3.14. But
`build-wheels` in `publish.yml` only builds for 3.12/3.13:

```yaml
      - name: Build wheels
        uses: PyO3/maturin-action@v1
        with:
          args: --release --out dist --interpreter 3.12 3.13
          manylinux: auto
```

**Proposed fix (diff snippet — NOT applied by this prep lane):**

```diff
       - name: Build wheels
         uses: PyO3/maturin-action@v1
         with:
-          args: --release --out dist --interpreter 3.12 3.13
+          args: --release --out dist --interpreter 3.12 3.13 3.14
           manylinux: auto
```

One line. TODO (endgame implementer): apply this diff to the real `publish.yml` as part
of the release PR, not before — `publish.yml` is a product/CI file this prep lane is
barred from touching. Verify `maturin-action@v1` actually has a 3.14 build available on
its bundled toolchain at release time (3.14 was new enough during this arc that the
matrix may need `PyO3/maturin-action`'s changelog checked, not just the flag added
blindly).

### 3.2 — Other observations (informational, no action prescribed)

- `check-version` derives the version to publish by grepping `pyproject.toml` and
  checking PyPI — so **the version bump in section 2 is what actually triggers a
  publish**, not a manual dispatch. Once `main` has `version = "1.0.0"` and is pushed,
  the workflow publishes automatically on the next push-to-main. This makes the *order*
  of "bump version" vs "merge the v2 branch" load-bearing: TODO (endgame implementer)
  decide whether to bump the version in the same commit/PR as the merge (one push, one
  publish) or as a separate follow-up push (deliberate, reviewable gap between merge and
  publish). The checklist in section 4 assumes the separate-push model since it's safer
  to review the merged `main` before triggering a real PyPI publish.
- `deploy-docs` runs as a `needs: publish` job inside this same workflow, i.e. **docs
  redeploy is currently gated on a successful PyPI publish**, not on doc content changing.
  If the great-docs cutover (section 6) changes how docs build, this coupling means a
  docs-only fix can't redeploy without also being a PyPI release — worth flagging to
  the endgame implementer as a possible follow-up decoupling (out of scope for this
  checklist to decide).
- `workflow_dispatch` is available as a manual trigger too — useful for a docs-only
  redeploy today via `deploy-docs`'s sibling `docs.yml` directly
  (`gh workflow run docs.yml`), which does NOT require a publish.
- No wheel is built for anything but `manylinux: auto` (linux) + windows-latest +
  macos-latest per the matrix — this looks correct and complete for the three-OS matrix;
  no gap found here beyond 3.1.

---

## 4. Tag / publish steps (for the user's hand — not to be automated by an agent)

TODO (endgame implementer): confirm this sequence with the user before anyone executes
it; nothing below has been run.

1. Confirm section 1's merge has landed on `main` and CI is green on `main` HEAD.
2. Decide the version per section 2 (`1.0.0` recommended) and get the user's sign-off —
   this is exactly the kind of decision `progress.md` flags as user-owned ("Cleanup-phase
   work item" / "README rewrite... resolved then, not during Phase 2").
3. Apply the `publish.yml` 3.14 fix (section 3.1) in the same PR/commit as the version
   bump, so the fix ships with the release it's needed for.
4. Bump `pyproject.toml`'s `version =` field to the agreed number. This is a product-file
   edit — out of scope for this prep lane, in scope for the endgame implementer.
5. Move `CHANGELOG.md`'s `## [Unreleased]` heading to `## [1.0.0] - <release date>` (see
   `docs/superpowers/drafts/CHANGELOG-v2-draft.md` for the drafted body — TODO markers
   included for numbers that depend on lanes 2/4).
6. Re-sync `docs/CHANGELOG.md` — currently kept byte-identical to root `CHANGELOG.md` by
   a pre-commit hook (`.pre-commit-config.yaml`, hook id `sync-changelog`:
   `cp CHANGELOG.md docs/CHANGELOG.md`). If the great-docs cutover (section 6) has
   happened by release time, this hook and its target no longer exist in the same form —
   confirm which is true before assuming this step applies.
7. Commit + push to `main` in one push (version bump + CHANGELOG + publish.yml fix
   together) — this is the push that triggers `publish.yml`'s auto-publish per section
   3.2's mechanics. Do not push separately/incrementally once the version file is bumped.
8. Watch the `Publish to PyPI` workflow run (`gh run watch` or the Actions tab):
   `check-version` → `build-wheels` (3 OS × now 3 interpreters) → `build-sdist` →
   `publish` (trusted publishing, no token needed) → `deploy-docs`.
9. Verify on PyPI: `pip index versions rustest` or https://pypi.org/project/rustest/ shows
   the new version with a 3.14 wheel present for all three OSes.
10. Verify the docs site redeployed and reflects the new content (section 6).
11. Create the GitHub Release / tag pointing at the merge commit from section 1, with
    release notes drawn from the CHANGELOG entry. (`publish.yml` does not create a GitHub
    Release itself — it only publishes to PyPI and redeploys docs — so this is a manual
    `gh release create` step, or `great-docs changelog`'s GitHub-Releases-sourced flow if
    the docs tooling migration in section 6 has already replaced the changelog-authoring
    process by then.)
12. Proceed to section 5 (issue closures) once the tag/release is public.

---

## 5. Post-release issue closures (#129–#140)

All twelve issues were **OPEN** as of 2026-07-29 (`gh issue list --state all`, confirmed
live against GitHub, not just the ledger). Nine are fixed on `v2/phase0-conformance`
already; three are not. **Do not close any of these until the release in section 4 is
actually live** — closing on merge would be premature if the release is aborted.

| # | Title | Status on `v2/phase0-conformance` | Fixed-in commit(s) |
|---|---|---|---|
| [#129](https://github.com/Apex-Engineers-Inc/rustest/issues/129) | `unittest.TestCase` results always reported PASSED | **Fixed.** Commit message states "closes #129 at the root." | `0a975de` (+ hardening `26c2e97`) |
| [#130](https://github.com/Apex-Engineers-Inc/rustest/issues/130) | `conftest.py` imported twice as two module objects | **Fixed.** Commit message: "Module identity (issue #130): files are imported under their REAL dotted name..." | `159ff4c` |
| [#131](https://github.com/Apex-Engineers-Inc/rustest/issues/131) | `@pytest.mark.skipif` silently ignored | **Fixed.** Commit message states "closing #131 at the root." | `0a975de` |
| [#132](https://github.com/Apex-Engineers-Inc/rustest/issues/132) | `publish.yml` never ships a 3.14 wheel | **Not fixed yet** — this is section 3.1 above. | *(apply diff at release time)* |
| [#133](https://github.com/Apex-Engineers-Inc/rustest/issues/133) | CI never runs `cargo test`; 2 stale `model.rs` assertions fail in-tree | **Not fixed.** Still open per the ledger's final entry ("(4) #133... still open"). | — |
| [#134](https://github.com/Apex-Engineers-Inc/rustest/issues/134) | pre-commit pins ruff 0.8.4, dev extra installs 0.14.x | **Not fixed.** Still open per the ledger. | — |
| [#135](https://github.com/Apex-Engineers-Inc/rustest/issues/135) | `MarkDecorator` mutates inherited marks in place | **Not reproducible in v2** (the v2 worker's mark-reading path was built to not repeat the defect — see commit body, "read once, never amplified"). This is a v1-only bug; whether it counts as "fixed" or "moot" depends on whether v1 survives the release — see the #139 row and TODO below. | `3286bca` (v2 avoids it structurally) |
| [#136](https://github.com/Apex-Engineers-Inc/rustest/issues/136) | Bare `@pytest.mark.skip` destroys the test body | **Fixed**, explicitly, both engines. | `344eab2` |
| [#137](https://github.com/Apex-Engineers-Inc/rustest/issues/137) | Bare `@pytest.mark.xfail` silently deletes the test | **Fixed**, explicitly, both engines. | `344eab2` |
| [#138](https://github.com/Apex-Engineers-Inc/rustest/issues/138) | "fix(compat): support bare `@pytest.mark.xfail` decorator" | **Open, title looks like a duplicate of #137** — flagged as an open question below, not resolved by this prep lane. | — |
| [#139](https://github.com/Apex-Engineers-Inc/rustest/issues/139) | Native `rustest.mark.skip` inert under v1 | **Open, v1-specific.** Its disposition depends entirely on the concurrent v1-deletion lane — see TODO below. | — |
| [#140](https://github.com/Apex-Engineers-Inc/rustest/issues/140) | Worker process tree leaks orphans when parent dies mid-run | **Fixed**, explicitly. | `e0b9fe8` |

**TODOs for the endgame implementer, before closing anything:**

1. **#138 vs #137 duplicate check.** #138's title ("fix(compat): support bare
   `@pytest.mark.xfail` decorator") reads like an issue-tracker artifact rather than a bug
   report, and its subject is identical to #137's. Read both on GitHub, confirm whether
   #138 should be closed as a duplicate of #137 (which `344eab2` already fixes) or whether
   it's tracking something narrower that's still open.
2. **#135 and #139 depend on the v1-deletion lane's outcome**, which was still in progress
   in a separate worktree as of this report. If v1 is deleted entirely in this release:
   - #135 (v1-only in-place mark mutation) → close as **wontfix / obsolete**, not "fixed"
     — the bug never got a v1 patch, it's just gone with v1. Cite the deletion commit.
   - #139 (v1-only inert native skip) → same disposition, same reasoning.
   If v1 survives this release as `--v1` (unchanged from today's plan), these two stay
   open and should be labeled/noted as "known, frozen-engine-only, no fix planned" rather
   than closed.
3. **#133 and #134 are real, currently-open gaps** with no fix landed anywhere in this
   arc. Do not close them at release time; consider whether either should block the
   release (a CI gap and a lint-tool version skew are unlikely to block, but that's a
   call for whoever owns release sign-off, not this prep lane).
4. For each issue closed, the standard GitHub `Fixes #NNN` convention wasn't used at
   commit time (these were research/audit findings, not issue-driven work), so closing
   must be done explicitly post-release — `gh issue close NNN --comment "Fixed in
   <sha>, released in v1.0.0."` — rather than relying on an automatic linked-PR close.

---

## 6. great-docs deployment cutover steps

See the main report (`.superpowers/sdd/p4-docs-prep-report.md`, section "great-docs
findings") for the full research. Summary of the cutover mechanics only:

1. `pip install great-docs` (or add to the `docs` extra in `pyproject.toml` alongside /
   replacing `zensical` — TODO: endgame implementer decides whether this is a hard
   replacement in one release or a parallel-build transition period).
2. Requires the **Quarto CLI** installed separately (great-docs builds by shelling out to
   `quarto render`) — this is a new CI/local-dev prerequisite `zensical` did not have.
   Confirm the GitHub Actions runner needs an explicit Quarto install step (Quarto
   publishes its own `quarto-dev/quarto-actions/setup` action) — `great-docs
   setup-github-pages` may or may not wire this in automatically; verify by inspecting
   the workflow file it generates before trusting it blindly.
3. `great-docs init` at repo root: scans the `python/rustest` package, detects docstring
   style, writes `great-docs.yml`.
4. Content migration (see migration map in the main report for the page-by-page plan):
   move/rewrite narrative docs from `docs/*.md` into a new `user_guide/*.qmd` tree
   (directory name is great-docs' convention, not required to be `docs/` — TODO: decide
   whether to rename the top-level directory or keep `docs/` and point great-docs at it
   via config, to minimize churn in existing inbound links).
5. Rename `.md` → `.qmd` per moved file. **Plain triple-backtick fences are inert by
   default** in `.qmd` (confirmed via great-docs' own docs, section "Markdown flavor" in
   the main report) — only ` ```{python} ` cells execute under Quarto. This means the
   *content* of rustest's existing code blocks needs no syntax change to remain
   non-executed-by-Quarto, but see the next point for what tests them instead.
6. **rustest's own doc-code-block testing walks files by extension and by explicit
   naming** (`docs/guide/markdown-testing.md`: "rustest automatically discovers and
   tests Python code blocks in `.md` files"; `CLAUDE.md`: "a directory argument collects
   no `.md`... markdown must be NAMED, not walked"). Renaming to `.qmd` silently drops
   every doc page out of CI's documentation-testing net **unless** one of:
   - the CI invocation is updated to name `*.qmd` files explicitly (`rustest
     user_guide/**/*.qmd`), **and** rustest's markdown-tier collector is confirmed to
     parse `.qmd` fences identically to `.md` (untested — `.qmd` is Quarto-flavored
     Pandoc markdown, a superset of CommonMark; the fence syntax itself is unchanged per
     the great-docs research, so this is *likely* a non-issue, but "likely" is not
     "verified" — TODO: endgame implementer should add one `.qmd` file to the conformance
     or example suite and confirm rustest's own walker collects its fences before relying
     on this in CI), or
   - the tested pages are kept as `.md` (not moved into the `.qmd` tree) and cross-linked
     from the great-docs nav instead of being authored there, or
   - a build step generates the `.qmd` narrative from a tested `.md` source (more
     moving parts, not recommended unless the above two are both ruled out).
   **This is the single highest-risk item in the whole migration** — flagged again in
   the main report's open questions.
7. `great-docs.yml`'s `reference:` section replaces `mkdocstrings`/zensical's manual
   `api/*.md` pages (`overview.md`, `decorators.md`, `core.md`, `reporting.md`,
   `approx.md`) with auto-discovery from `python/rustest/__init__.py`'s public exports.
   TODO: confirm docstring coverage in the Python package is sufficient for this to
   produce pages at least as good as the current hand-written `api/*.md` — spot-check
   with `great-docs scan` before deleting the hand-written pages.
8. `great-docs build` locally; fix whatever it flags (broken cross-refs, missing
   frontmatter `title`/`guide-section` keys per file).
9. `great-docs setup-github-pages` generates a new GitHub Actions workflow. This
   **replaces** `.github/workflows/docs.yml`'s `zensical build` step, and — per section
   3.2's finding — `docs.yml` is currently invoked as a `needs: publish` job inside
   `publish.yml`. TODO: decide whether the new great-docs workflow keeps that
   publish-gated coupling or becomes independently triggered on `docs/**` /
   `user_guide/**` changes (arguably better, since docs fixes currently can't ship
   without a PyPI release).
10. Update the `docs` extra in `pyproject.toml` (`zensical` → `great-docs`) and the
    `poe docs` task (`uv run zensical serve` → `great-docs preview`) — **both are
    product-file edits, out of scope for this prep lane.**
11. Enable GitHub Pages → Source: GitHub Actions in repo settings if not already so
    configured (it already is, since `docs.yml` uses `actions/deploy-pages@v4` today —
    confirm the new workflow targets the same Pages environment rather than creating a
    second one).
12. Cut over DNS/URL expectations: `site_url` in the old `zensical.toml` was
    `https://apex-engineers-inc.github.io/rustest` — confirm great-docs preserves this
    exact URL (no path prefix change) so existing inbound links (PyPI project page,
    README badges, `pyproject.toml`'s `[project.urls]`) keep resolving.
13. Delete `zensical.toml` and the `zensical` dependency only after the great-docs build
    is verified live and green in CI — keep both building in parallel for at least one
    PR cycle if risk tolerance allows it.
