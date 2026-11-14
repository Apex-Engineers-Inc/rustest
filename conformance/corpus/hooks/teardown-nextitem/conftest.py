"""A teardown hook that reads nextitem: rustest tears class and module fixtures down
at the next test, not inside this chain, so it hands the session to pytest up front."""


def pytest_runtest_teardown(item, nextitem):
    if nextitem is None:
        item.config._last_seen = item.name
