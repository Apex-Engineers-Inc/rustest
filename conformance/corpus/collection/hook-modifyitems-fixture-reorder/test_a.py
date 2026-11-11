import pytest


@pytest.fixture(scope="module", params=["a", "b"])
def res(request):
    return request.param


def test_1(res):
    pass


def test_2(res):
    pass
