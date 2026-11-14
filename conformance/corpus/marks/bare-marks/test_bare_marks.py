"""The bare (uncalled) mark decorator forms -- defects #136 and #137.

`@pytest.mark.skip` and `@pytest.mark.xfail` written *without parentheses* are ordinary,
extremely common pytest. Both were broken in rustest's shim, in two different ways, and
the second was the worst class of failure a runner can have:

* **#136** -- `_PytestMarkCompat.skip` was a plain method, so the bare form applied the
  method itself: the test function arrived as `reason`, the module attribute became
  `skip_decorator`'s inner closure, and the body was destroyed. v1 lost the test; v2
  reported a `FixtureLookupError: fixture 'func' not found` error status, because the
  closure's parameter is named `func`.
* **#137** -- `mark.xfail` and `mark.skipif` were properties returning bound methods, so
  the bare form bound the test function to `condition` and left a `MarkDecorator` in the
  module. Not a function, therefore not a test: the test **silently vanished from
  collection** under v1 *and* v2. Exit 0 with a test missing is the failure nobody sees.

Each bare form here is paired with its *called* control, so a regression that broke the
called form while fixing the bare one -- or vice versa -- cannot pass this case. The
skipped bodies raise rather than pass so that a skip which fails to happen is loud.

pytest 8.4.2 on this file: `1 passed, 4 skipped, 2 xfailed`, exit 0. Note the bare
`skipif` in particular: pytest does *not* treat a `skipif` with no condition as an error,
it treats it as an unconditional skip (`_pytest/skipping.py::evaluate_skip_marks`
l. 177-179), which is why it is a `skipped` here and not an `error`.
"""

import pytest


@pytest.mark.skip
def test_bare_skip():
    raise AssertionError("must not run")


@pytest.mark.skip(reason="called skip")
def test_called_skip():
    raise AssertionError("must not run")


@pytest.mark.skipif
def test_bare_skipif():
    raise AssertionError("must not run")


@pytest.mark.skipif(1 + 1 == 2, reason="called skipif")
def test_called_skipif():
    raise AssertionError("must not run")


@pytest.mark.xfail
def test_bare_xfail():
    raise AssertionError("expected to fail")


@pytest.mark.xfail(reason="called xfail")
def test_called_xfail():
    raise AssertionError("expected to fail")


def test_control():
    assert True
