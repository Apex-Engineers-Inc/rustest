import pytest


@pytest.mark.smoke
def test_smoke_only():
    assert True


def test_unmarked():
    assert True
