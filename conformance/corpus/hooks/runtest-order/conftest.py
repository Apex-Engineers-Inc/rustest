"""pytest_runtest_setup / call / teardown at every priority, against pytest's own
implementations: a tryfirst setup hook runs before the skip marks are evaluated, a
plain one after them and before the fixtures, a trylast one after the fixtures; a
skip mark never reaches the plain hook."""

import pytest

from hook_log import LOG


class First:
    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item):
        LOG.append(("setup-tryfirst", item.name))


class Last:
    @pytest.hookimpl(trylast=True)
    def pytest_runtest_setup(self, item):
        LOG.append(("setup-trylast", item.name, "resource" in item.funcargs))

    @pytest.hookimpl(trylast=True)
    def pytest_runtest_call(self, item):
        LOG.append(("call-trylast", item.name))


def pytest_configure(config):
    config.pluginmanager.register(First())
    config.pluginmanager.register(Last())


def pytest_runtest_setup(item):
    LOG.append(("setup", item.name, "resource" in item.funcargs))


def pytest_runtest_call(item):
    LOG.append(("call", item.name, "resource" in item.funcargs))


def pytest_runtest_teardown(item):
    LOG.append(("teardown", item.name))


def pytest_runtest_logstart(nodeid, location):
    LOG.append(("logstart", nodeid.split("::")[-1], location[2]))


def pytest_runtest_logfinish(nodeid):
    LOG.append(("logfinish", nodeid.split("::")[-1]))


@pytest.fixture
def resource():
    LOG.append("resource-up")
    yield 1
    LOG.append("resource-down")
