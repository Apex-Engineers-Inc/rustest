"""The slow-test guard's shape: a pytest_fixture_setup wrapper adds up each test's
function-scoped fixture setups in request.node.stash; a makereport wrapper copies the
count into user_properties.  Directly parametrized names count too (pytest gives them
a pseudo fixture), a cached module fixture only once, and a nested fixture is not
inside its dependent's hook call."""

import pytest

from hook_log import LOG

_count = pytest.StashKey[int]()


OURS = {"shared", "inner", "outer", "value", "letter"}


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    if fixturedef.argname in OURS:
        LOG.append(("setup", fixturedef.argname, fixturedef.scope))
    try:
        return (yield)
    finally:
        if fixturedef.argname in OURS:
            LOG.append(("done", fixturedef.argname))
        if fixturedef.scope == "function":
            stash = request.node.stash
            stash[_count] = stash.get(_count, 0) + 1


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if call.when == "call":
        report.user_properties.append(("fixtures", item.stash.get(_count, 0)))
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
