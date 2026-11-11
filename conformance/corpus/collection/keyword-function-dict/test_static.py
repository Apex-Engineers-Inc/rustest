import pytest


@pytest.mark.parametrize("a", [1])
class TestClassParam:
    def test_method(self, a):
        pass


@pytest.mark.smoke
def test_marked():
    pass


@pytest.mark.parametrize("b", [1, 2])
def test_parametrized(b):
    pass


def test_unmarked():
    pass
