import pytest


@pytest.fixture(params=[1, 2])
def number(request):
    return request.param


def test_number(number):
    assert number in (1, 2)  # collects as two tests
