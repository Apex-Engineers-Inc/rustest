"""``pytest.exit(returncode=N)`` from inside a test: the process exits N, not 2.

``_pytest/main.py::wrap_session`` sets ``session.exitstatus`` to the exception's return code
when it has one, and ``INTERRUPTED`` (2) only when it does not -- whatever already failed.
"""

import pytest


def test_first():
    assert False


def test_bails():
    pytest.exit("stopping here", returncode=7)


def test_never():
    raise AssertionError("must not run: pytest.exit() stopped the session above")
