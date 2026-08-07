import pytest


@pytest.mark.xfail(reason="known broken")
def test_expected_failure():
    assert False


def test_normal():
    assert True
