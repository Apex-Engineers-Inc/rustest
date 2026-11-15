"""A pytest_fixture_setup wrapper that lists, on each test item's stash, the function-scoped
fixtures set up for it, and a makereport wrapper that reports how many on the call phase.
Directly parametrized names count too (pytest gives them a pseudo fixture), a cached module
fixture only once, and a nested fixture is not inside its dependent's hook call."""

import pytest

from hook_log import LOG

SET_UP = pytest.StashKey[list]()
WATCHED = frozenset({"shared", "inner", "outer", "value", "letter"})


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    name = fixturedef.argname
    if name in WATCHED:
        LOG.append(("setup", name, fixturedef.scope))
    try:
        result = yield
    finally:
        if name in WATCHED:
            LOG.append(("done", name))
        if fixturedef.scope == "function":
            request.node.stash.setdefault(SET_UP, []).append(name)
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when == "call":
        report.user_properties.append(("fixtures", len(item.stash.get(SET_UP, []))))
    return report


def pytest_runtest_logreport(report):
    if report.when == "call":
        LOG.append((report.nodeid.split("::")[-1], dict(report.user_properties)["fixtures"]))


@pytest.fixture(scope="module")
def shared():
    return "module"


@pytest.fixture
def inner():
    return 1


@pytest.fixture
def outer(inner):
    return inner + 1
