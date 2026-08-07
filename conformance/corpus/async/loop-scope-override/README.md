`@pytest.mark.asyncio(loop_scope=...)` overriding `asyncio_default_test_loop_scope`.

`pytest_asyncio/plugin.py::_get_marked_loop_scope` (l. 763-781): the mark's
`loop_scope` wins over the ini, and `PytestAsyncioFunction._loop_scope` (l. 476-489)
is what the item resolves. Three claims in one file: module-scoped tests share a loop
with each other, a class-scoped pair shares a *different* one, and an unmarked test
gets neither (the ini's `function` default).
