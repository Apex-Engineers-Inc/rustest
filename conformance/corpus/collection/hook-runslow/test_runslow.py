import pytest


def test_fast():
    pass


@pytest.mark.slow
def test_slow():
    pass


@pytest.mark.slow
class TestSlowClass:
    def test_in_class(self):
        pass
