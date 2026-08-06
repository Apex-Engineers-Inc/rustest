`asyncio_mode = strict`, set explicitly on both sides.

Strict mode is `pytest_asyncio/plugin.py`'s "autoprocessing disabling" (l. 82-87):
only a marked async test is run, and only a `@pytest_asyncio.fixture` async fixture
is awaited. The two unmarked shapes fail through `_pytest/python.py::async_fail`, and
the plain `@pytest.fixture async def` hands the test a **coroutine object** -- probed
on pytest 8.4.2 + pytest-asyncio 1.2.0, which is the behaviour this case pins rather
than one rustest chose.

Note what is deliberately here: a bare async generator *test*. In strict mode it is
not converted into pytest-asyncio's item class, so it acquires no `xfail(run=False)`
and fails like any other unrunnable async body -- the mode-dependence of
`_worker.py::_async_generator_xfail`.
