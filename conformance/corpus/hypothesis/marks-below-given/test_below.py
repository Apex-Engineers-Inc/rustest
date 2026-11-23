import pytest
from hypothesis import given, settings, strategies as st


@pytest.fixture
def doubled(request):
    return request.param * 2


@given(st.integers())
@pytest.mark.skip(reason="below given")
def test_skip_below(x):
    assert False


@given(st.integers())
@pytest.mark.xfail(reason="below given", strict=True)
def test_xfail_below(x):
    assert x > 10**10


@given(st.integers())
@pytest.mark.usefixtures("tmp_path")
def test_usefixtures_below(x):
    pass


@given(st.integers())
@pytest.mark.parametrize("p", [1, 2])
def test_parametrize_below(p, x):
    assert p in (1, 2)


@given(st.integers())
@pytest.mark.parametrize("doubled", [3], indirect=True)
def test_indirect_below(doubled, x):
    assert doubled == 6


@settings(max_examples=3)
@pytest.mark.slowish
@given(st.integers())
def test_mark_between(x):
    pass
