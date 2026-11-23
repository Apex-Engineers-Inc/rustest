"""pytest_runtest_makereport wrappers around pytest's own: a plain wrapper sees the
report after skipping.py's xfail promotion, a trylast one before it, a tryfirst one
outermost; user_properties appended there reach pytest_runtest_logreport."""

import pytest

from hook_log import LOG


class Inner:
    @pytest.hookimpl(wrapper=True, trylast=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        if call.when == "call":
            LOG.append(("trylast", item.name, report.outcome, hasattr(report, "wasxfail")))
        return report


class Outer:
    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        if call.when == "call":
            LOG.append(("tryfirst", item.name, report.outcome))
        return report


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if call.when == "call":
        report.user_properties.append(("seen", item.name))
        LOG.append(("plain", item.name, report.outcome, hasattr(report, "wasxfail")))
    return report


def pytest_runtest_logreport(report):
    if report.when == "call":
        LOG.append(
            (
                "log",
                report.nodeid.split("::")[-1],
                report.outcome,
                list(report.user_properties),
                report.keywords.get("xfail"),
            )
        )
    elif report.when == "setup" and report.skipped:
        LOG.append(("log-skip", report.nodeid.split("::")[-1], report.longrepr[2]))


def pytest_configure(config):
    config.pluginmanager.register(Inner())
    config.pluginmanager.register(Outer(), "outer")
