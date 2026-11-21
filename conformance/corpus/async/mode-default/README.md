The `asyncio_mode` **default**, with nothing configured -- the one place rustest
knowingly departs from pytest-asyncio, and the case exists to measure the departure
rather than to hide it.

pytest-asyncio declares `addini("asyncio_mode", default="strict")` (plugin.py
l. 106-110), so under pytest an unmarked `async def` test fails with
`_pytest/python.py::async_fail`. rustest defaults to `auto` and runs it. The
reasoning is at `src/engine/config.rs::DEFAULT_ASYNCIO_MODE`: strict is a *coexistence*
default for one installable plugin among several, and rustest has no plugin ecosystem
and no way to be "not installed", so strict would ship the runner with its own
documented async support off and answer an unmarked async test with advice to install
pytest-asyncio.

Everything else about the option is identical, which is why `mode-strict` (explicit)
and `session-loop-shared` (explicit `auto`) both MATCH. Ledgered in
`conformance/waivers-run.toml`.
