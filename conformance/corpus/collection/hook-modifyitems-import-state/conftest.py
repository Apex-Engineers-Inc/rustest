"""Skips everything once both test modules have been imported. pytest imports both
before the hook; so does every rustest worker (xdist's model), whatever it owns."""

import pytest


def pytest_collection_modifyitems(config, items):
    import registry_state

    if len(registry_state.SEEN) >= 2:
        for item in items:
            item.add_marker(pytest.mark.skip(reason="all modules loaded"))
