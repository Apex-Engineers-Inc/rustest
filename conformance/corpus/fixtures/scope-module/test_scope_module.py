import itertools

import pytest

counter = itertools.count()


@pytest.fixture(scope="module")
def shared():
    return next(counter)


def test_first(shared):
    assert shared == 0


def test_second(shared):
    assert shared == 0  # module scope: same instance
