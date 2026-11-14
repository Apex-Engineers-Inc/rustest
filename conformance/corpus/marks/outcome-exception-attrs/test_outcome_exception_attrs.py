"""`pytest.fail.Exception` / `skip.Exception` / `xfail.Exception` / `exit.Exception`.

Port target: `_pytest/outcomes.py::_with_exception` (l. 92-98) and its four decorated
uses. Each helper carries the class it raises, so `except pytest.fail.Exception:` names
the outcome without importing an internal module.

The module-level asserts are deliberate: psutil reaches `pytest.fail.Exception` at
*import* time, inside a helper's body, and that alone -- never raising it -- was enough
to break all 19 of its test modules and lose all 713 of its tests (Task 1b sweep, §5 M6).
Reaching the attribute is the failure mode, so the case reaches it during collection too.
"""

import pytest

assert isinstance(pytest.fail.Exception, type)
assert isinstance(pytest.skip.Exception, type)
assert isinstance(pytest.xfail.Exception, type)
assert isinstance(pytest.exit.Exception, type)


def test_fail_exception_catches_fail():
    try:
        pytest.fail("deliberate")
    except pytest.fail.Exception as exc:
        assert "deliberate" in str(exc)
    else:
        raise AssertionError("pytest.fail did not raise")


def test_skip_exception_catches_skip():
    try:
        pytest.skip("deliberate")
    except pytest.skip.Exception as exc:
        assert "deliberate" in str(exc)
    else:
        raise AssertionError("pytest.skip did not raise")


def test_xfail_exception_catches_xfail():
    try:
        pytest.xfail("deliberate")
    except pytest.xfail.Exception as exc:
        assert "deliberate" in str(exc)
    else:
        raise AssertionError("pytest.xfail did not raise")


def test_raises_exception_alias_is_the_fail_class():
    assert pytest.raises.Exception is pytest.fail.Exception
