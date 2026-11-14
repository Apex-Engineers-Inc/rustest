"""A parametrized `@given` method: the plugin suppresses `differing_executors` (each case has
its own instance) and gives each node id its own database key."""

import pytest
from hypothesis import given, strategies as st


class TestParametrized:
    @pytest.mark.parametrize("n", [1, 2])
    @given(st.integers())
    def test_case(self, n, x):
        assert n in (1, 2)
