An `async def` + `yield` fixture on a **session** loop: setup, both halves of the
generator, and teardown all on one loop, with teardown deferred to the end of the
session.

`pytest_asyncio/plugin.py::_wrap_asyncgen_fixture` (l. 299-342) advances the
generator to its yield through `runner.run(setup())` and registers a finalizer that
resumes it on the *same* runner. A dependent async-gen fixture is stacked on it so
the ordering of two deferred teardowns on one loop is exercised, not just one.
