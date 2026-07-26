# Phase 2: Speed — Static Tier, Cache, and the Benchmark Gate

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Speed regime: full review only for Task 1 (Tier S correctness) and Task 3's rewriting semantics; mutation verification only for cache-key logic; gates sequential.

**Goal:** Hit the spec's Phase 2 targets on the committed baselines: warm collection ≤ 50ms on the 5k-test synthetic suite, per-test framework overhead < 200µs, corpus gates all green throughout. This is where "10-20x" stops being a thesis.

**Architecture:** Tier S — a Rust static collector (ruff parser crates, karva-style git deps) that parses test files without importing, extracts tests/params/fixture names, and emits the SAME manifest the Tier D worker produces, with a conservative dynamism detector routing non-static files to Tier D (which remains definitionally correct). A content-hash manifest cache makes warm collection near-free. Selection (`-k`/`-m`) prunes pre-dispatch on the cached manifest. Assertion rewriting (AST transform at collection, cached in .pyc-adjacent artifacts) gives pytest-grade failure introspection.

**Reference material:** karva (MIT, scratchpad clone) for ruff-crate wiring patterns; spec Tier S section; the 1b.2 evidence scripts for differential methodology.

## Global Constraints

- **Tier D is the oracle for Tier S:** on every corpus case and every synthetic suite, Tier S's manifest must byte-equal Tier D's (which byte-equals pytest). A three-way differential (S vs D vs pytest) is the core instrument. Files the dynamism detector flags route to D — false positives cost speed only, never correctness; a false NEGATIVE (static answer for a dynamic file) is the Critical class.
- All three conformance gates + self-suites stay green after every task; benchmarks run sequentially, never concurrent with gates.
- Rust: fmt/clippy clean; ruff crates pinned by rev in Cargo.toml with a comment.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Tier S static collector + dynamism detector (FULL REVIEW)

- New crate module `src/v2/static_collect.rs` (+ `Cargo.toml` git deps: ruff_python_parser, ruff_python_ast pinned rev).
- Extract per file WITHOUT importing: module-level `def test_*`/`async def` per naming rules; `Test*` classes (no `__init__`/`__new__`, inherited methods NOT resolvable statically → class-bases-not-object ⇒ dynamism flag); `@parametrize`/`@pytest.mark.parametrize` with LITERAL args (const-eval: literals, tuples, lists; anything else ⇒ flag); fixture-name params; marks with literal args; `pytestmark` literals.
- Dynamism detector (conservative, per file): star imports, `__getattr__`, exec/eval at module level, non-literal parametrize, class inheritance beyond object/TestCase-direct, decorators other than known mark/fixture forms, conditional `def` at module level, unittest.TestCase (dynamic — TestLoader semantics stay in D), conftest presence does NOT flag (fixtures resolve at execution; but parametrized fixtures in scope DO — detect via the manifest's closure needs: if Tier D's registry for that dir has parametrized fixtures, route to D — implement as: files in dirs whose conftest chain contains `params=` textually flag to D. Cite the reasoning in code).
- Orchestrator integration: Tier S first; flagged files → Tier D workers as today. Manifest entries carry `tier: "s"|"d"` (schema addition — bump manifest schema note, golden updated; this IS a contract change → mutation rows for the new field's omission rules).
- **The three-way differential test**: for every corpus case AND a generated 200-file mixed suite (static + deliberately-dynamic files), assert manifest(S-enabled) == manifest(D-only) == pytest ids. Per-file tier attribution asserted (the dynamic files went to D; the static ones to S).

### Task 2: Manifest cache + pruning + fast collect path

- Cache: `.rustest_cache/v2-manifest/` keyed by (file blake3, config hash, rustest version); hit ⇒ skip parse AND skip worker; invalidation tests (content change, config change, version change). Mutation rows for the key composition (the one place a stale-cache bug is silent).
- `-k`/`-m` prune on the cached manifest before any worker spawns; `--collect-only` warm path never spawns Python at all for fully-static cached trees.
- Wire `rustest_collect_s` benchmark: bench.py fills the reserved column via `--v2-collect-only` (cold + warm rows). **Gate check: warm ≤ 50ms on the 5k suite** — record actual numbers; if missed, profile and report the breakdown (do not tune blindly).

### Task 3: Assertion rewriting + per-test overhead (FULL REVIEW on semantics)

- AST rewrite at collection time for Tier S files (Tier D files keep plain asserts initially — document): pytest-style `assert a == b` introspection messages. Port the message FORMAT from pytest's assertion/util.py (cite; differential-test message text on the common shapes: ==, in, is, comparisons, f-string operands). Cache rewritten bytecode; invalidate with the manifest cache key.
- Per-test overhead attack: profile the execute path (worker dispatch round-trip per test is the suspect — batch ExecuteTest dispatch: send a file's tests as one batch request, stream results; protocol addition ⇒ goldens + mutation rows). **Gate check: overhead < 200µs/test** on the 5k suite (derived metric from bench.py); record actuals.
- Regenerate baselines.json with the full comparison table (pytest / v1 / v2-cold / v2-warm); README section updated with honest numbers and methodology caveats.

## Definition of done

1. Three-way differential green on corpus + mixed synthetic suite; all conformance gates + self-suites green; tier attribution correct.
2. Warm collection ≤ 50ms @ 5k tests and overhead < 200µs/test — or actuals + profile breakdown reported if targets missed (targets are the spec's; honest misses with data beat silent tuning).
3. Baselines regenerated; Phase 3 plan (asyncio/mock/coverage + real-world validation incl. Apex-Member-Designer server/) authored at this gate.
