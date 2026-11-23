"""config.pluginmanager.register from pytest_configure: the object's own
pytest_configure is replayed at once (pluggy's historic call), its logreport and
makereport wrapper join the chains globally, and a name is returned."""

import pytest

from hook_log import LOG


class Plugin:
    def pytest_configure(self, config):
        LOG.append("plugin-configure")

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        if call.when == "call":
            report.user_properties.append(("plugin", True))
        return report

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            LOG.append((report.nodeid.split("::")[-1], report.outcome, report.user_properties))


def pytest_configure(config):
    LOG.append("conftest-configure")
    name = config.pluginmanager.register(Plugin(), "the-plugin")
    LOG.append(("registered", name))
    LOG.append("conftest-configure-done")
