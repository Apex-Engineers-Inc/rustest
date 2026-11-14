"""A conftest hook in a/ that reaches into b/: pytest runs the hook on every directory's items,
so b/test_b.py::test_b is deleted from the session; the hook runtime shows it the whole collection too."""


def pytest_collection_modifyitems(items):
    items[:] = [item for item in items if "test_b" not in item.nodeid]
