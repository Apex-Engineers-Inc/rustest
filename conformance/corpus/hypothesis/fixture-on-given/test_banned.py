"""The plugin makes `@pytest.fixture` on a `@given` function a `RuntimeError` at import, so
this module is a collection error."""

import pytest
from hypothesis import given, strategies as st


@pytest.fixture
@given(st.integers())
def bad_fixture(x):
    return x


def test_never_collected():
    pass
