"""Strict xfail: the shape where an *unexpected pass* must fail the suite.

Phase 0 Task 6 flagged this as the xfail sub-case most likely to be got wrong, because
it is the one where the mark inverts twice: a strict xfail whose body passes is not an
`xpassed` at all -- pytest rewrites the report to `failed` before it is counted
(`_pytest/skipping.py::pytest_runtest_makereport`, the `[XPASS(strict)]` branch), which
flips the exit code from 0 to 1. A runner that treated every xpass alike would report a
green run for a suite pytest calls red.

Both marks use the *called* decorator form, `@pytest.mark.xfail(...)`, which is the only
form the compat shim supports -- see the Task 5 report's finding on bare
`@pytest.mark.xfail`.
"""

import pytest


@pytest.mark.xfail(strict=True, reason="must fail, but does not")
def test_strict_xpass():
    assert True


@pytest.mark.xfail(strict=True, reason="genuinely broken")
def test_strict_xfail():
    assert False
