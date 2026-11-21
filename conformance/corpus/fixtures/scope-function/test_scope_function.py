import itertools

import pytest

counter = itertools.count()


@pytest.fixture
def fresh():
    return next(counter)


def test_first(fresh):
    assert fresh == 0


def test_second(fresh):
    assert fresh == 1  # function scope: new instance per test
