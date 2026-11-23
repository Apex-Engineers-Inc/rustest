import pytest


def pytest_collection_modifyitems(config, items):
    seen = set()
    for item in items:
        if item.nodeid in seen:
            item.add_marker(pytest.mark.skip(reason="dup"))
        seen.add(item.nodeid)
