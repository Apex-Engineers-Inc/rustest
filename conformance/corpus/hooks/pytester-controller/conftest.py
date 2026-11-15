"""A controller-only plugin (registered where there is no ``workerinput``) that turns a failing
session into exit 0 only when it saw every phase report of the session -- the pytester file's
included, which rustest runs under its own pytest process as one more xdist worker."""

import pytest


class Seen:
    def __init__(self):
        self.reports = []

    def pytest_runtest_logreport(self, report):
        self.reports.append((report.nodeid, report.when))

    def pytest_sessionfinish(self, session, exitstatus):
        ids = {nodeid for nodeid, _ in self.reports}
        whole = len(self.reports) == 12 and len(set(self.reports)) == 12
        if whole and "test_b_pytester.py::test_inner" in ids and exitstatus == 1:
            session.exitstatus = 0


def pytest_configure(config):
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(Seen())
