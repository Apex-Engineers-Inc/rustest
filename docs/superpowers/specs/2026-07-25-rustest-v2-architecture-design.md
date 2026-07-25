# rustest v2 Architecture & Decisions

Status: approved by Jeffrey 2026-07-25 (via decision questions). This spec governs the v2
rewrite. Phase plans live in `docs/superpowers/plans/` and are written at each phase gate.

## Product identity

rustest v2 is **the Vitest of Python testing**: a drop-in pytest replacement where
`import pytest` works at full fidelity with zero flags, delivering order-of-magnitude
speed via a Rust orchestrator. The native `rustest` API remains supported and unchanged,
but pytest compatibility is the default experience, not a mode. The `--pytest-compat`
flag and the compat banner are removed.

## Locked decisions (user-approved)

1. **Spine rewrite in-repo.** New core built alongside v1; v1 runner stays in-tree until
   the conformance corpus reports parity on core pytest, then is deleted.
2. **Compat by default.** Full-fidelity pytest shim always active. Native API is a bonus.
3. **Full tiered collection, phased.** Static AST tier (ruff parser crates) + dynamism
   detector + runtime fallback, plus assertion rewriting — sequenced after manifest +
   workers land, with releasable milestones in between.
4. **Core pytest parity gates secondary features.** asyncio + mock transplant from v1 and
   sys.monitoring coverage are critical but come *after* the core-pytest parity gate.

## Decisions made by Claude (documented, veto anytime)

- **Spawn-based process workers, parallel by default.** Spawn (not fork) is the portable
  primitive — Windows-first development makes this the honest default; fork/forkserver is
  a later POSIX optimization. `-n auto` semantics by default, `-n 0` for in-process.
- **Serializable manifest is the spine.** Collection output is data
  (`CollectedTest {id, path, qualname, class_name, param_id, param_values_ref, marks,
  fixtures}`) — never live PyObjects. Callables resolve inside workers at execution time.
  This one rule enables caching, process workers, and the static tier.
- **Config subsystem first-class.** Reads `[tool.pytest.ini_options]` / `pytest.ini` /
  `setup.cfg`: rootdir resolution, `testpaths`, `addopts`, `python_files/classes/functions`,
  `norecursedirs`, `markers`, `filterwarnings`. Nodeids derive from rootdir exactly as
  pytest's do.
- **Contracts are pytest's:** nodeid format byte-compatible, exit codes 0–5, `-k` full
  expression language, deterministic collection order.
- **Unsupported surface fails loudly.** No stub returns `None` where pytest returns a
  value (today's `request` stub does). Everything outside the supported subset raises
  with a docs link.
- **One owner per concern.** Rust owns orchestration, model, scheduling, config. Python
  owns user-facing API and thin in-worker shims. No split-brain async/fixture logic.
- **Python 3.10+ floor.** Coverage via `sys.monitoring` requires 3.12+; on 3.10/3.11
  coverage delegates to coverage.py's tracer or errors clearly.
- **Skip/xfail classified by exception type identity**, never message string matching.
- **Free-threading stance:** no GIL-dependent safety invariants in v2 (v1's
  `ACTIVE_RESOLVER` raw-pointer tunnel is condemned). Worker protocol is data-only.

## v1 audit findings driving the rewrite

- Discovery imports the world serially under the GIL, uncached (`src/discovery.rs:395`);
  `-k`/`-m` filter after import (`discovery.rs:903`); `TestCase` carries live callables
  (`discovery.rs:1199`) blocking cache/workers/static tier.
- `starts_with("test")` collects `testfoo`; pytest's `test_*` does not (`discovery.rs:1173`).
- Compat is an opt-in mode with a reduced feature set (`compat/pytest.py` docstring).
- Skip detection string-matches exception messages (`execution.rs:649`).
- GIL-dependent unsafe resolver tunnel (`execution.rs:160`).
- Eager cartesian expansion of parametrized fixtures at collection (`discovery.rs:901`).
- No ini/rootdir config subsystem; `request` stub silently returns `None`.

## v2 architecture

```
┌────────────── Rust core ──────────────┐
│ config/rootdir → tiered collection →  │
│ manifest (serializable) → scheduler → │
│ spawn worker pool → event stream →    │
│ reporters (terminal, JSON, JUnit)     │
└───────────────────────────────────────┘
   Tier S: static AST (ruff crates) + dynamism detector + manifest cache
   Tier D: runtime import-based enumeration inside workers (v1 collector logic, ported)
   Workers: CPython, spawn; import module → resolve qualname → fixture plan → run
   Assertion rewriting: AST transform at collection, cached in .pyc (Tier S infra)
```

## Phases and gates

- **Phase 0 — Conformance corpus + benchmarks** (plan: `2026-07-25-phase0-conformance-corpus.md`).
  Differential harness (pytest as dev-dep only) + corpus + waivers + 3 benchmark numbers
  (collection-only, per-test overhead, full run). *This is the fitness function; nothing
  else starts until it runs in CI.*
- **Phase 1 — Spine.** Manifest model, config subsystem, spawn worker pool, Tier D
  collection, compat-by-default. **Gate: corpus parity on core pytest** (fixtures,
  parametrize, marks, conftest, classes, unittest.TestCase; waivers only for documented
  non-goals).
- **Phase 2 — Speed.** Tier S static collection + manifest cache + pre-import `-k`/`-m`
  pruning + assertion rewriting. Gate: benchmark targets — collection ≤ 50ms warm on the
  5k synthetic suite; per-test overhead < 200µs; corpus still green.
- **Phase 3 — Secondary absorption.** asyncio + mock transplanted/validated under v2
  workers; coverage via sys.monitoring. Gate: corpus extended to those areas, green.
- **Cleanup.** Delete v1 runner; docs flip; release.

## Non-goals (unchanged from v1, now explicit)

pluggy/hook plugin API, `pytest_plugins`, custom collectors, `_pytest` internals as public
surface. pytest-django is out of v2 scope. Scope police: v2 is done when the corpus and
the three benchmark numbers say so — not when everything discussed in review is fixed.
