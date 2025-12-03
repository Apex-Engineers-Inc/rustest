"""Tests for parametrize with various data types.

Verifies that parametrization works correctly with different types of values
and that IDs are generated appropriately.

This test file is designed to be run with rustest:
    uv run python -m rustest tests/test_parametrize_data_types.py
"""

from __future__ import annotations

import sys

# Skip this entire module when running with pytest
if "pytest" in sys.argv[0]:
    import pytest

    pytest.skip(
        "This test file requires rustest runner (rustest-specific parametrize features)",
        allow_module_level=True,
    )

from dataclasses import dataclass
from typing import Any, NamedTuple

from rustest import parametrize, raises
from rustest.decorators import ParameterSet


# Create a param helper function similar to pytest.param
def param(*values: object, id: str | None = None, marks: object = None) -> ParameterSet:
    """Helper to create a ParameterSet (like pytest.param)."""
    return ParameterSet(values=values, id=id, marks=marks)


# =============================================================================
# Basic Data Types
# =============================================================================


@parametrize("value", [None])
def test_parametrize_none(value: Any) -> None:
    """Test parametrization with None value."""
    assert value is None


@parametrize("value", [True, False])
def test_parametrize_bool(value: bool) -> None:
    """Test parametrization with boolean values."""
    assert isinstance(value, bool)


@parametrize("value", [0, 1, -1, 42, 1000000])
def test_parametrize_int(value: int) -> None:
    """Test parametrization with integer values."""
    assert isinstance(value, int)


@parametrize("value", [0.0, 1.5, -3.14, float("inf"), float("-inf")])
def test_parametrize_float(value: float) -> None:
    """Test parametrization with float values."""
    assert isinstance(value, float)


@parametrize("value", ["", "hello", "a" * 100, "unicode: \u4e2d\u6587"])
def test_parametrize_string(value: str) -> None:
    """Test parametrization with string values."""
    assert isinstance(value, str)


@parametrize("value", [b"", b"bytes", b"\x00\x01\x02"])
def test_parametrize_bytes(value: bytes) -> None:
    """Test parametrization with bytes values."""
    assert isinstance(value, bytes)


# =============================================================================
# Collection Types
# =============================================================================


# Note: Lists and tuples in parametrize are treated as argument unpacking.
# To pass a list/tuple as a single value, wrap in param() with explicit tuple.
@parametrize("value", [
    param(([],), id="empty_list"),
    param(([1],), id="single_list"),
    param(([1, 2, 3],), id="multi_list"),
])
def test_parametrize_list(value: list[Any]) -> None:
    """Test parametrization with list values (wrapped in param)."""
    assert isinstance(value, list)


@parametrize("value", [
    param(((),), id="empty_tuple"),
    param(((1,),), id="single_tuple"),
    param(((1, 2, 3),), id="multi_tuple"),
])
def test_parametrize_tuple(value: tuple[Any, ...]) -> None:
    """Test parametrization with tuple values (wrapped in param)."""
    assert isinstance(value, tuple)


@parametrize("value", [set(), {1}, {1, 2, 3}])
def test_parametrize_set(value: set[Any]) -> None:
    """Test parametrization with set values."""
    assert isinstance(value, set)


@parametrize("value", [frozenset(), frozenset([1]), frozenset([1, 2, 3])])
def test_parametrize_frozenset(value: frozenset[Any]) -> None:
    """Test parametrization with frozenset values."""
    assert isinstance(value, frozenset)


@parametrize("value", [{}, {"a": 1}, {"nested": {"key": "value"}}])
def test_parametrize_dict(value: dict[str, Any]) -> None:
    """Test parametrization with dict values - single param."""
    assert isinstance(value, dict)


# =============================================================================
# Complex Types
# =============================================================================


class Point(NamedTuple):
    """Named tuple for testing."""

    x: int
    y: int


@parametrize("point", [Point(0, 0), Point(1, 2), Point(-1, -1)])
def test_parametrize_namedtuple(point: Point) -> None:
    """Test parametrization with NamedTuple values."""
    assert isinstance(point, Point)
    assert hasattr(point, "x")
    assert hasattr(point, "y")


@dataclass
class DataPoint:
    """Dataclass for testing."""

    name: str
    value: int


@parametrize(
    "data",
    [
        DataPoint("first", 1),
        DataPoint("second", 2),
        DataPoint("third", 3),
    ],
)
def test_parametrize_dataclass(data: DataPoint) -> None:
    """Test parametrization with dataclass values."""
    assert isinstance(data, DataPoint)
    assert isinstance(data.name, str)
    assert isinstance(data.value, int)


class CustomObject:
    """Custom object for testing."""

    def __init__(self, value: int) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"CustomObject({self.value})"


@parametrize("obj", [CustomObject(1), CustomObject(2), CustomObject(3)])
def test_parametrize_custom_object(obj: CustomObject) -> None:
    """Test parametrization with custom object values."""
    assert isinstance(obj, CustomObject)
    assert obj.value in (1, 2, 3)


# =============================================================================
# Mixed Types
# =============================================================================


@parametrize(
    "value",
    [
        1,
        1.0,
        "1",
        [1],
        (1,),
        {1},
        {"one": 1},
    ],
)
def test_parametrize_mixed_types(value: Any) -> None:
    """Test parametrization with mixed types."""
    # Each value is different but test should run for each
    assert value is not None


# =============================================================================
# Parameter Sets with IDs
# =============================================================================


@parametrize(
    "value",
    [
        param(1, id="one"),
        param(2, id="two"),
        param(3, id="three"),
    ],
)
def test_parametrize_with_custom_ids(value: int) -> None:
    """Test parametrization with custom IDs via param()."""
    assert value in (1, 2, 3)


@parametrize(
    "a,b,expected",
    [
        param(1, 2, 3, id="positive"),
        param(-1, 1, 0, id="zero-result"),
        param(0, 0, 0, id="zeros"),
    ],
)
def test_parametrize_multiple_values_with_ids(a: int, b: int, expected: int) -> None:
    """Test parametrization with multiple values and custom IDs."""
    assert a + b == expected


# =============================================================================
# Edge Cases
# =============================================================================


@parametrize("value", [
    param(([None],), id="list_single_none"),
    param(([None, None],), id="list_double_none"),
])
def test_parametrize_list_with_none(value: list[None]) -> None:
    """Test parametrization with lists containing None."""
    assert isinstance(value, list)
    assert all(v is None for v in value)


@parametrize("value", [
    param(((None, 1),), id="none_first"),
    param(((1, None),), id="none_second"),
    param(((None, None),), id="both_none"),
])
def test_parametrize_tuple_with_none(value: tuple[Any, Any]) -> None:
    """Test parametrization with tuples containing None."""
    assert isinstance(value, tuple)
    assert len(value) == 2


@parametrize("value", [{"": "empty_key"}, {"key": ""}])
def test_parametrize_dict_with_empty_string(value: dict[str, str]) -> None:
    """Test parametrization with dicts containing empty strings."""
    assert isinstance(value, dict)


@parametrize(
    "value",
    [
        param(((1, 2, 3),), id="homogeneous"),
        param(((1, "two", 3.0),), id="heterogeneous"),
        param(((1, [2, 3], {"four": 4}),), id="nested"),
    ],
)
def test_parametrize_nested_structures(value: tuple[Any, ...]) -> None:
    """Test parametrization with nested data structures."""
    assert isinstance(value, tuple)
    assert len(value) == 3


# =============================================================================
# Callable IDs
# =============================================================================


@parametrize(
    "value",
    [1, 2, 3],
    ids=lambda x: f"value_{x}",
)
def test_parametrize_callable_ids(value: int) -> None:
    """Test parametrization with callable ids function."""
    assert value in (1, 2, 3)


@parametrize(
    "value",
    [1, 2, None],
    ids=lambda x: f"num_{x}" if x is not None else "none",
)
def test_parametrize_callable_ids_with_none(value: int | None) -> None:
    """Test parametrization with callable ids that handles None."""
    assert value in (1, 2, None)


# =============================================================================
# Multiple Parametrize Decorators
# =============================================================================


@parametrize("a", [1, 2])
@parametrize("b", ["x", "y"])
def test_multiple_parametrize(a: int, b: str) -> None:
    """Test multiple parametrize decorators create cross-product."""
    assert a in (1, 2)
    assert b in ("x", "y")


@parametrize("a", [1, 2])
@parametrize("b", [10, 20])
@parametrize("c", ["a", "b"])
def test_three_parametrize_decorators(a: int, b: int, c: str) -> None:
    """Test three parametrize decorators create 8 combinations."""
    assert a in (1, 2)
    assert b in (10, 20)
    assert c in ("a", "b")


# =============================================================================
# String Argument Names
# =============================================================================


@parametrize("a, b", [(1, 2), (3, 4)])
def test_parametrize_comma_separated_names(a: int, b: int) -> None:
    """Test parametrize with comma-separated argument names."""
    assert (a, b) in [(1, 2), (3, 4)]


@parametrize("a,b,c", [(1, 2, 3), (4, 5, 6)])
def test_parametrize_no_space_names(a: int, b: int, c: int) -> None:
    """Test parametrize with comma-separated names without spaces."""
    assert a + b + c in (6, 15)


# =============================================================================
# Empty and Edge Cases
# =============================================================================


def test_parametrize_validates_empty_names() -> None:
    """Test that empty argument names raise an error."""
    with raises(ValueError, match="at least one argument"):

        @parametrize("", [1, 2])
        def test_func(x: int) -> None:
            pass


def test_parametrize_validates_ids_length() -> None:
    """Test that ids must match values length."""
    with raises(ValueError, match="ids must match"):

        @parametrize("x", [1, 2, 3], ids=["a", "b"])  # Only 2 ids for 3 values
        def test_func(x: int) -> None:
            pass
