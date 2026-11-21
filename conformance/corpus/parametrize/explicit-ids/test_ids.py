import pytest


@pytest.mark.parametrize("value", [1, 2], ids=["one", "two"])
def test_named(value):
    assert value in (1, 2)
