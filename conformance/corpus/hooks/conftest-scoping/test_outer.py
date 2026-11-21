import pytest


def test_outer():
    pass


@pytest.mark.skip_at_root
def test_outer_marked():
    pass
