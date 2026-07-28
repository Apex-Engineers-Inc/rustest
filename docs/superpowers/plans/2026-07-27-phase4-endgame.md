# Phase 4: Endgame — Real-Suite Closure, v1 Deletion, Docs, Release Prep

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Speed regime: full review only for Task 1's compat semantics; gates sequential. **HARD RULE (user directive): NOTHING is released, versioned, or published in this phase. Task 3 PREPARES; the user executes the release themselves after reviewing the finished branch.**

**Goal:** All four real-world suites pass under flagless `rustest` (Member Designer's 6,084-test main tree actually running and matching); v1 deleted; docs migrated to great-docs; README rewritten against final measured numbers; release prepared for the user's hand.

## Global Constraints

- pytest's installed source remains the oracle; differential everything; conformance gates + self-suites green after every task (sequential); Member Designer's tree stays read-only.
- No version bumps, no publishes, no tags. publish.yml may be REVIEWED/fixed (#132's 3.14 wheel gap) but not triggered.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: The six mechanisms + re-sweep (FULL REVIEW on compat semantics)

Fix each sweep-ledgered mechanism, cited to its oracle, differential-pinned, then re-run `--real` on all four suites:

1. **monkeypatch.setattr dotted-path semantics** — port `_pytest/monkeypatch.py::resolve`/`annotated_getattr` (importable-prefix walk), killing click's 14 errors.
2. **pytest.raises legacy callable form** — `raises(Exc, func, *args, **kwargs)` per `_pytest/python_api.py`; plus **ExceptionInfo.tb and .match()** (and audit adjacent attrs: .value/.type/.typename exist? — complete the pytest-faithful subset), killing jinja2's 50 failures.
3. **Module-level `pytestmark = pytest.mark.asyncio` (bound-method shape)** — the mark-property dual-duty work (#136/#137 family) must extend to bare marks ASSIGNED to pytestmark (single mark or list, bare or called); kills Member Designer's 4 collection errors. Probe pytest's handling of every pytestmark shape and match.
4. **`indirect=` parametrization** — real implementation (params routed through the named fixtures per `_pytest/python.py`'s indirect handling; ids from the param, fixture receives request.param), killing 120 Member Designer failures. This is the largest item — it touches collection (ids), fixtures (request.param), and the worker; differential-pin against pytest on the shapes Member Designer uses plus the corpus fixtures/parametrized-fixture interaction.
5. **`pythonpath` ini** — implement (config subsystem key + worker sys.path application per `_pytest/python_path.py`); restores v1's src-magic as pytest semantics.
6. **`addopts` application** — resolved config's addopts prepended to CLI argv per pytest's Config handling (interaction with the removed flags: unknown/removed flags in a repo's addopts must error the way the CLI does — probe pytest's behavior with unknown addopts and match).

**Re-sweep gate:** `python -m conformance --real` all four — target: more-itertools + click + jinja2 MATCH; member-designer main tree RUNS to completion with outcomes matching pytest or a minimal ledger where each residual has mechanism + a decision (fix now vs documented limitation). Corpus grows where each mechanism deserves a permanent case (at minimum: indirect=, raises-legacy, pytestmark-bare). Wall-clock table updated in the report.

### Task 1b: Expanded validation — seven approved suites (controller-verified; USER-APPROVED LIST)

User approved (2026-07-27) for cloning: **networkx, Pygments, marshmallow, rich, Werkzeug, sqlparse, python-dateutil** (packaging not selected; alternates cachetools/humanize/tabulate/itsdangerous if any fails the hook audit — swap requires a note in the report, not new approval, per the named-alternate arrangement).

[AUDIT ADJUDICATED 2026-07-27: KEEP marshmallow/rich/Werkzeug/sqlparse; SWAP networkx→cachetools (load-bearing 55-file collect_ignore) and Pygments→humanize (custom collector classes); dateutil KEPT over the auditor's mechanical flag (single shallow modifyitems = the user's "limited ways" case; delta ledgered; hypothesis in its own venv). USER ADDITION: **PyniteFEA** (local read-only tree, audited zero-hooks/zero-config, unittest-heavy `Testing/` package — config drafted at conformance/real/pynite.toml). Slate = EIGHT new suites; twelve total in the final table.]

- Pre-clone hook audit per project (WebFetch their conftest.py files at the pinned rev): any `pytest_generate_tests`/custom-collector/hook-heavy conftest → swap to an alternate, documented.
- Each: shallow clone at pinned rev (documented), isolated venv, `conformance/real/<name>.toml`, pytest-then-rustest sequential, outcome diff + wall-clock, per-repo ledger with mechanisms. hypothesis-marked tests (dateutil) deselected+documented if they trip.
- Acceptance: all seven MATCH or minimally ledgered with mechanism + decision; **new-mechanism budget: if the seven suites surface more than TWO new fix-worthy mechanisms, pause and report to the user before fixing** (signal that the edge-case tail isn't converging).
- Wall-clock table for all twelve suites goes to the report (README numbers come from this table in Task 3).

### Task 2: v1 deletion + repo simplification (spot-review)

- Delete the v1 engine: discovery.rs, execution.rs (v1 halves), v1 model/renderer paths, `--v1` flag and `_run_v1`, the v1 compat-mode plumbing; `rustest.run()` public API repointed at v2 (breaking-change noted for the endgame changelog). The v1 conformance gate retires — its ledger ARCHIVED (docs/superpowers/history/) not deleted; stale-waiver machinery keeps the two v2 gates. #133's dead tests die with v1; add the cargo-test CI step now that it can be green (pin --test-threads=1 per ledger note).
- Pre-commit/CI cleanup: fix the ruff version skew (#134 — align pre-commit rev with the dev extra), remove v1-only jobs/steps, conformance workflow keeps three→two gates + real-sweep smoke (more-itertools only in CI — the others documented as local/scheduled).
- Self-suites: the rustest-under-rustest run keeps only the v2 path; docs code-block tests intact.

### Task 3: Docs migration + README + release prep (controller-verified; USER EXECUTES RELEASE)

- **great-docs migration:** research https://posit-dev.github.io/great-docs/ (WebFetch its docs), port the docs/ tree from zensical (structure map documented; redirects/nav preserved where the tool allows); `poe docs` updated; CI docs job updated; pytest-compat page, coverage page, migration guide rewritten for the v2 reality.
- **README rewrite** against final measured numbers: honest hero (collection ~37x, overhead ~8x vs pytest, real-suite table incl. Member Designer), quickstart on the flagless default, compat statement (what's absorbed, what's ledgered), the "8.5x" v1 claim retired.
- **CHANGELOG** for the whole v2 arc (breaking changes: 3.12 floor, --pytest-compat removed, rustest.run() v2, --v1 gone).
- **Release prep only:** version proposal (recommend 1.0.0 — the product identity flip warrants it; user decides), publish.yml reviewed + #132 3.14 wheel added (NOT run), a RELEASE-CHECKLIST.md for the user (merge strategy for v2/phase0-conformance → main, tag, publish trigger, post-release issue closures #129-#139 with fixed-in commits).

## Definition of done

1. Four real suites clean (or minimally ledgered with decisions); corpus + gates green.
2. v1 gone; CI enforces cargo test + two gates + real smoke; ruff skew fixed.
3. Docs on great-docs; README/CHANGELOG truthful to final numbers; release checklist ready.
4. **Nothing released.** Final wrap-up report to the user with everything they need to review and ship.
