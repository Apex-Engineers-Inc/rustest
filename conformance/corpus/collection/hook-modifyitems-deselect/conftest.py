"""Tests removed through `config.hook.pytest_deselected` are counted as deselected."""


def pytest_collection_modifyitems(config, items):
    removed = [item for item in items if "internal" in item.name]
    if removed:
        config.hook.pytest_deselected(items=removed)
        items[:] = [item for item in items if item not in removed]
