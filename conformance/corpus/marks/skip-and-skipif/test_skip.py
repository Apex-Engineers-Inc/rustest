import pytest


@pytest.mark.skip(reason="always skipped")
def test_skipped():
    raise AssertionError("must not run")


@pytest.mark.skipif(1 + 1 == 2, reason="condition true")
def test_skipped_conditionally():
    raise AssertionError("must not run")


@pytest.mark.skipif(1 + 1 == 3, reason="condition false")
def test_runs():
    assert True
