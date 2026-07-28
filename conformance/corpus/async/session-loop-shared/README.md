A **session-scoped event loop** shared by two sibling files.

The question is whether `asyncio_default_test_loop_scope = session` produces one
loop for the whole run, or one per file. The loop *identity* is the assertion, so
the evidence is carried in a plain module (`_loops.py`) rather than in a fixture: a
session-scoped fixture is itself per-file in rustest today
(`conformance/corpus/fixtures/session-scope`, waived), so a fixture-held list would
answer a different question and answer it wrongly.

pytest-asyncio's loops are the `_{scope}_scoped_runner` fixtures of
`pytest_asyncio/plugin.py` l. 799-835; a session-scoped one is created once per
session.

**The caveat came true, and the ledger entry is where this file said it would go.** rustest
used to match because both files drew the *same* worker (stem-hash routing,
`src/v2/collect.rs::worker_for`). Phase 4 Task 1 stopped collecting markdown on a directory
walk, which removed this directory's own `README.md` from the target list and so changed the
pool size the stems are hashed against — the two files now split, and each worker builds its
own session loop. Waived in `waivers-v2-run.toml`, alongside the session-*fixture* boundary
at `conformance/corpus/fixtures/session-scope`, which is the same limitation.

Verified either way: `-n 1` is 3 passed (one loop, three tests); `-n 2` is 1 failed, and the
failing assertion is `len(_loops.SEEN) == 3` seeing 1 — two interpreters, not two loops in
one interpreter. The assertion is **not** weakened and the case is **not** pinned to `-n 1`:
it is the standing measurement of a real limitation, and closing it means giving the
orchestrator cross-worker session scope.
