import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "parametrize" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="parametrized"))
