"""
Conftest using rustest_fixtures (preferred approach).

This demonstrates the rustest-native way of loading fixtures from
external modules, with clearer and more explicit naming.
"""

import rustest

# Rustest-native approach (preferred)
# This is clearer than pytest_plugins - it explicitly states it's for fixtures,
# not for loading actual pytest plugins (which rustest doesn't support)
rustest_fixtures = "rustest_fixtures_module"


@rustest.fixture
def local_fixture():
    """Fixture defined directly in conftest."""
    return "local_value"
