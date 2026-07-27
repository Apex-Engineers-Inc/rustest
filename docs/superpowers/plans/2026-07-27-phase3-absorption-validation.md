# Phase 3: Secondary Absorption + Real-World Validation

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Speed regime: full review only for Task 1's loop-scope semantics and Task 3's coverage correctness; gates sequential; no releases, no README messaging, no version bumps (user directive — endgame only).

**Goal:** The features real suites need — full asyncio semantics, mock/builtin completeness, sys.monitoring coverage — proven by running four real-world suites under `rustest` with zero flags: Apex-Member-Designer `server/` (user-designated), more-itertools, click, jinja2.

**Architecture:** asyncio absorption replaces the one-loop-per-worker stopgap with pytest-asyncio's loop-scope model (function/class/module/session loops per config), since Member Designer's suite depends on session-scoped loops. Remaining builtins land (capfd, caplog, cache, mocker/pytest-mock shim, tmpdir, request completeness). Coverage rides sys.monitoring (3.12+, unconditional per the floor decision). Validation is a new harness mode: run a real repo's suite under pytest and under rustest, diff outcomes and wall-clock — the corpus discipline applied to the wild.

## Global Constraints

- pytest-asyncio's installed source is the oracle for loop semantics (cite `pytest_asyncio/plugin.py`); pytest for everything else; differential tests throughout.
- All existing gates + self-suites green after every task; corpus grows with each absorbed feature (loop-scope cases, caplog case, coverage smoke).
- Real-suite validation NEVER modifies the target repos; Member Designer runs against `C:\Users\JeffreyMBloss\local-repos\Apex-Member-Designer\main\server` read-only (its own venv/deps — probe and document the invocation; if deps are missing locally, document precisely what's needed rather than installing into their env without care).
- Rewrite-reach follow-up rides along: decouple assertion-rewrite eligibility from the Tier-S collection gate (rewriting needs only parse-success + no walrus + no PYTEST_DONT_REWRITE; ModuleSideEffect/ConditionalDef/UnknownDecorator are collection concerns, not rewrite concerns) — target ~100% reach, differential-verified.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: asyncio absorption — loop scopes (FULL REVIEW on semantics)

- Port pytest-asyncio's loop-scope model: `asyncio_mode=auto|strict`, `loop_scope`/`asyncio_default_fixture_loop_scope`/`asyncio_default_test_loop_scope` ini options (the config subsystem already parses ini — extend the known keys), loops created/torn per scope (function default; class/module/session per marks+config), async fixtures bound to their loop scope, `event_loop_policy` basics. Cite plugin.py for each rule.
- Member Designer's config is the acceptance shape: `asyncio_mode=auto`, both defaults `session`. Corpus additions: loop-scope cases (session-scoped loop shared across files at `-n 1`; the multi-worker divergence documented like session fixtures), `loop_scope` mark override, async gen fixture on a session loop.
- Known 1c-era gaps closed here: parallel async batching within a loop scope (v1's feature — port the batching semantics where the loop scope allows it), asyncio timeout mark.

### Task 2: builtin completeness + mock shim (spot-review)

- capfd (fd-level capture — the design the protocol punted on; workers dup fds around phases), caplog (logging handler capture + `set_level`/`at_level`, records on the report), `cache` fixture (config.cache API over `.rustest_cache`), tmpdir (py.path legacy shim over tmp_path), `mocker` (pytest-mock's MockerFixture core: patch/spy/stub with teardown), request completeness pass (node, config, keywords — pytest-faithful subset, loud errors beyond it).
- Corpus: caplog + capfd cases; the pytest-exit residuals (returncode honor) if cheap.
- Rewrite-reach decoupling lands here (constraint above) with before/after reach numbers on rustest's own tree.

### Task 3: coverage via sys.monitoring (FULL REVIEW on correctness)

- `--cov`-compatible surface producing coverage.py-compatible data: sys.monitoring line events in workers (3.12+ unconditional), merged across the pool, written via coverage.py's data API (dep: coverage as an optional extra — decide packaging with a cited comparison of "emit .coverage via coverage-py API" vs "raw lcov"; prefer the former for ecosystem compat). Branch coverage explicitly deferred (documented).
- Differential: line sets vs coverage.py's own run on the same suite (small tolerance documented for known monitoring differences, each cited).

### Task 4: real-world validation sweep (controller-verified)

- New harness mode `python -m conformance --real <name>`: configs in `conformance/real/{member-designer,more-itertools,click,jinja2}.toml` (repo path/URL+rev, setup cmd, test dir, known-divergence ledger per repo). Runs pytest then rustest (sequential), diffs outcome counts + ids where feasible, records wall-clock both ways.
- OSS targets cloned shallow into a work dir at pinned revs (document revs); Member Designer used in place, read-only.
- Acceptance: each suite either MATCHES (counts+exit) or every divergence is ledgered with mechanism — zero unexplained. Wall-clock table published to the report (not README — endgame).
- Expect and ledger: plugin-dependent tests (pytest-cov flags in their addopts etc.) — strip plugin flags via the config's addopts handling, documented per repo.

## Definition of done

1. All four real suites run under flagless `rustest`: outcomes matching or fully ledgered; wall-clock recorded.
2. Corpus grown (loop scopes, caplog/capfd, coverage smoke); all gates green; rewrite reach ~100% verified.
3. Phase 4 (Cleanup: v1 deletion, README rewrite, zensical→great-docs, release prep) plan authored at this gate.
