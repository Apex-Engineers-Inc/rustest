"""A `trylast` `pytest_collection_modifyitems` runs *after* `-k` has deselected: it sees only what
selection left, and what it marks is that list's first test (a hook that filters by file)."""

import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    if items:
        items[0].add_marker(pytest.mark.skip(reason="first of what -k left"))
