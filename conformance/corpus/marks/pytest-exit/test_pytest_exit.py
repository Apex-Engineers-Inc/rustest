"""``pytest.exit()`` from inside a test body -- the mid-run bail-out shape.

pytest raises ``Exit`` (not ``Interrupted``), so the session stops where it stands: the
tests before the call keep their reports, the ones after are never run, and the process
exits 2 -- the same code a collection error produces, which is why the run gate keys its
"nothing ran" rule on the *collect* pass (P1b.2 Task 5 report Sec 10.2).
"""

import pytest


def test_first():
    assert True


def test_bails():
    pytest.exit("stopping here")


def test_never():
    raise AssertionError("must not run: pytest.exit() stopped the session above")
