A fixture asked to run on a loop **narrower than itself** — a `ScopeMismatch`, and
the same one pytest raises.

`pytest_asyncio/plugin.py::pytest_fixture_setup` (l. 742-743) acquires the loop by
*requesting* a fixture — `request.getfixturevalue(f"_{loop_scope}_scoped_runner")` —
so pytest's ordinary `SubRequest._check_scope` rejects a session-scoped fixture
reaching for a function-scoped runner exactly as it would reject any other
wide-requests-narrow pair. The rule is never written down in the plugin because the
plugin never had to write it down.

rustest calls `FixtureRunner.loop_runner` directly, which skips the fixture graph and
therefore skipped the check. Until Phase 3 Task 1's review this file ran **green**
under rustest and `2 errors` under pytest, with the async generator's teardown
resuming on a *newly built* loop after the one its setup ran on had been closed
(probed: `teardown same=False setup_loop_closed=True`). A fixture holding anything
loop-bound — a pool, a task, a lock — was being torn down against a foreign loop with
nothing reported.

The shape is not exotic: `asyncio_default_fixture_loop_scope = function` is what
pytest-asyncio's own deprecation warning tells you future versions will default to,
so a suite that sets it and keeps one session-scoped async fixture lands here.
