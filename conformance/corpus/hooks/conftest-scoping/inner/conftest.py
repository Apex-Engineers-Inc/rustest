"""A conftest's execution hooks reach only the tests under its directory."""

import pytest


def pytest_runtest_setup(item):
    pytest.skip("inner conftest")


def pytest_runtest_logreport(report):
    if report.when == "setup" and "inner" not in report.nodeid:
        raise AssertionError("an inner hook saw " + report.nodeid)
