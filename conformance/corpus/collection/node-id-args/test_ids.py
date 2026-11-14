import pytest


def test_a():
    assert True


def test_b():
    assert True


@pytest.mark.parametrize("x", [1, 2])
def test_p(x):
    assert x


class TestC:
    def test_one(self):
        assert True

    def test_two(self):
        assert True
