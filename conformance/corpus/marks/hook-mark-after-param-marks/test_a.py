import pytest


@pytest.mark.parametrize(
    "x", [pytest.param(1, marks=pytest.mark.xfail(reason="param", strict=False))]
)
def test_p(x):
    pass
