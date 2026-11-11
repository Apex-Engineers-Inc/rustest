from hypothesis import given, strategies as st


@given(st.integers())
def test_property(x):
    assert isinstance(x, int)


def test_plain():
    pass
