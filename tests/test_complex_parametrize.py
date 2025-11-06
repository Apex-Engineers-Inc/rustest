"""Test file for complex parametrization scenarios."""

from rustest import parametrize, fixture


@parametrize(
    "x,y,expected",
    [
        (1, 2, 3),
        (5, 5, 10),
        (10, -5, 5),
        (0, 0, 0),
    ],
)
def test_addition(x, y, expected):
    """Test addition with multiple parameters."""
    assert x + y == expected


@parametrize("value", [1, 2, 3, 4, 5])
def test_single_param(value):
    """Test with single parameter."""
    assert value > 0


@parametrize("text", ["hello", "world", "test"])
def test_string_param(text):
    """Test with string parameters."""
    assert isinstance(text, str)
    assert len(text) > 0


@parametrize(
    "data",
    [
        {"key": "value"},
        {"a": 1, "b": 2},
        {},
    ],
)
def test_dict_param(data):
    """Test with dictionary parameters."""
    assert isinstance(data, dict)


@parametrize(
    "items",
    [
        [1, 2, 3],
        ["a", "b", "c"],
        [],
    ],
)
def test_list_param(items):
    """Test with list parameters."""
    assert isinstance(items, list)


@fixture
def multiplier():
    """Fixture that returns a multiplier."""
    return 10


@parametrize("value", [1, 2, 3])
def test_parametrize_with_fixture(value, multiplier):
    """Test combining parametrization with fixtures."""
    result = value * multiplier
    assert result == value * 10
    assert result >= 10
