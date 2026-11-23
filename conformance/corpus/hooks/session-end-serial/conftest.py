"""A controller-only plugin (registered where there is no ``workerinput``: pytest's one process,
xdist's controller) sees every phase report and ends the session.  The exit code is how the case
proves it: the session fails (one failing test), and the plugin turns that into exit 0 only when
it saw exactly what pytest's controller sees -- nine reports, the facts of a session with one
failure among three collected tests, every call report from this one process.  rustest's default pool would be two workers here; without pytest-xdist it runs the session on one."""

import os

import pytest


class SeenByTheController:
    def __init__(self):
        self.reports = []

    def pytest_runtest_logreport(self, report):
        self.reports.append(report)

    def pytest_sessionfinish(self, session, exitstatus):
        calls = [r for r in self.reports if r.when == "call"]
        workers = {dict(r.user_properties).get("worker") for r in calls}
        if (
            len(self.reports) == 9
            and exitstatus == 1
            and session.testsfailed == 1
            and session.testscollected == 3
            and workers == {"-"}
        ):
            session.exitstatus = 0

    def pytest_terminal_summary(self, terminalreporter):
        terminalreporter.section("seen by the controller")
        terminalreporter.write_line(f"{len(self.reports)} reports")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if call.when == "call":
        report.user_properties.append(("worker", os.environ.get("PYTEST_XDIST_WORKER", "-")))
    return report


def pytest_configure(config):
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(SeenByTheController())
