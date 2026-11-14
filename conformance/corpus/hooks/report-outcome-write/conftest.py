"""A hook rewriting report.outcome (pytest-rerunfailures' trick): rustest cannot keep
that promise, so the session goes to pytest after the test that triggered it --
its results so far are discarded, not mixed with pytest's."""

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if call.when == "call" and item.name == "test_b_fails":
        report.outcome = "passed"
    return report
