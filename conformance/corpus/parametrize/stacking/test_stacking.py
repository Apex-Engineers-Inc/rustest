import pytest


@pytest.mark.parametrize("a", [1, 2])
@pytest.mark.parametrize("b", ["x", "y"])
def test_grid(a, b):
    assert (a, b)  # collects 4 cases: [x-1] [x-2] [y-1] [y-2]
