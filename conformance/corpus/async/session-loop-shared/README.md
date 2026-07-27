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

**Standing caveat, measured.** rustest matches here because both files are routed to the
*same* worker (stem-hash routing, `src/v2/collect.rs::worker_for`) — all three tests report
one pid even at `-n 4`, and the case was verified independently at `-n 1` before it existed.
A run that split them would see two session loops, which is the same per-worker boundary
already ledgered for session *fixtures* under `conformance/corpus/fixtures/session-scope`.
Should that routing change, this case turns red and the ledger entry belongs in
`waivers-v2-run.toml` alongside that one — it is not a reason to weaken the assertion.
