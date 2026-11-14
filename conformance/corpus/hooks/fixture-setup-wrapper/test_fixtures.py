import pytest

from hook_log import LOG


def test_nested(outer, shared):
    assert outer == 2


@pytest.mark.parametrize("value", [1, 2])
def test_parametrized(value, inner, shared):
    pass


@pytest.fixture(params=["a"])
def letter(request):
    return request.param


def test_param_fixture(letter):
    assert letter == "a"


def test_zz_the_log_is_pytests():
    assert LOG == EXPECTED


EXPECTED: list[object] = [
    ("setup", "shared", "module"),
    ("done", "shared"),
    ("setup", "inner", "function"),
    ("done", "inner"),
    ("setup", "outer", "function"),
    ("done", "outer"),
    ("test_nested", 2),
    ("setup", "value", "function"),
    ("done", "value"),
    ("setup", "inner", "function"),
    ("done", "inner"),
    ("test_parametrized[1]", 2),
    ("setup", "value", "function"),
    ("done", "value"),
    ("setup", "inner", "function"),
    ("done", "inner"),
    ("test_parametrized[2]", 2),
    ("setup", "letter", "function"),
    ("done", "letter"),
    ("test_param_fixture[a]", 1),
]
