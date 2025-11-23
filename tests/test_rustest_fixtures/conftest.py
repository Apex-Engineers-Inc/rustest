"""
Conftest using rustest_fixtures (preferred approach).

This demonstrates the rustest-native way of loading fixtures from
external modules, with clearer and more explicit naming.
"""

import pytest

# Rustest-native approach (preferred)
# This is clearer than pytest_plugins - it explicitly states it's for fixtures,
# not for loading actual pytest plugins (which rustest doesn't support)
rustest_fixtures = "fixtures_module"


@pytest.fixture
def local_fixture():
    """Fixture defined directly in conftest."""
    return "local_value"
