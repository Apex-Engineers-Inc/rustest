"""A `pytest_collection_modifyitems` that reorders: the run order is the hook's, not the file's."""


def pytest_collection_modifyitems(items):
    items.sort(key=lambda item: item.name, reverse=True)
