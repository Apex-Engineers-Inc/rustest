"""String ``skipif``/``xfail`` conditions and a bare ``skip``.

A string condition is evaluated at run time against ``os``/``sys``/``platform`` plus the
module's globals (`_pytest/skipping.py::evaluate_condition`) -- it is never judged by the
truthiness of its source text, and it needs no ``reason=`` because ``condition: <source>``
is the default. A boolean condition without ``reason=`` stays an error, as in pytest.
"""

import pytest

FLAG = False


@pytest.mark.skip
def test_bare_skip():
    raise AssertionError("must not run")


@pytest.mark.skipif("sys.platform != 'no-such-platform'")
def test_true_string_skips_without_a_reason():
    raise AssertionError("must not run")


@pytest.mark.skipif("1 == 2")
def test_false_string_runs():
    assert True


@pytest.mark.skipif("FLAG", reason="reads module globals")
def test_module_global_false_runs():
    assert True


@pytest.mark.xfail("1 == 1")
def test_true_string_xfails():
    assert False


@pytest.mark.xfail("1 == 2")
def test_false_string_xfail_is_a_plain_failure():
    assert False


@pytest.mark.skipif(True)
def test_boolean_without_reason_is_an_error():
    pass
