"""An xfail mark applied after setup -- from a fixture, or from the body -- still
decides the outcome: skipping.py re-evaluates the marks around the call."""

import pytest


@pytest.fixture
def expect_failure(request):
    request.applymarker(pytest.mark.xfail(reason="from the fixture"))


def test_fixture_marks_a_failure(expect_failure):
    assert False


def test_body_marks_a_failure(request):
    request.applymarker(pytest.mark.xfail(reason="from the body"))
    assert False


def test_body_marks_a_pass(request):
    request.applymarker(pytest.mark.xfail(reason="from the body"))


def test_body_marks_a_strict_pass(request):
    request.applymarker(pytest.mark.xfail(reason="strict", strict=True))


def test_marked_with_raises_that_do_not_match(request):
    request.applymarker(pytest.mark.xfail(raises=KeyError, reason="wrong type"))
    raise ValueError("not a KeyError")


@pytest.fixture
def not_even_run(request):
    request.applymarker(pytest.mark.xfail(reason="not run", run=False))


def test_fixture_marks_xfail_run_false(not_even_run):
    raise AssertionError("the body does not run")
