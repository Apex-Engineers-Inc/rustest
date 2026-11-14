import pytest
from hypothesis import given, seed, strategies as st


@given(st.integers())
def test_given(x):
    pass


@seed(1234)
@given(st.integers())
def test_seeded(x):
    pass


@pytest.mark.parametrize("k", [1, 2])
@given(st.integers())
def test_parametrized(k, x):
    pass


def test_plain():
    pass
