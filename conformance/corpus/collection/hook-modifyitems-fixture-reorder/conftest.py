"""pytest reorders by the module-scoped parametrized fixture before this hook runs
(FixtureManager is registered after the initial conftests), so it keeps test_1[a], test_2[a]."""


def pytest_collection_modifyitems(config, items):
    items[:] = items[:2]
