"""Tests for the approx feature."""

import math

from rustest import approx


def test_approx_scalar_float() -> None:
    """Test approx with scalar float values."""
    assert 0.1 + 0.2 == approx(0.3)
    assert 1.0000001 == approx(1.0)
    assert 2.5 == approx(2.5)


def test_approx_scalar_int() -> None:
    """Test approx with integer values."""
    assert 1 == approx(1)
    assert 42 == approx(42)


def test_approx_scalar_with_rel_tolerance() -> None:
    """Test approx with custom relative tolerance."""
    assert 100 == approx(101, rel=0.02)
    assert 1000 == approx(1001, rel=0.002)


def test_approx_scalar_with_abs_tolerance() -> None:
    """Test approx with custom absolute tolerance."""
    assert 0.1 == approx(0.10001, abs=0.001)
    assert 1.0 == approx(1.0001, abs=0.001)


def test_approx_fails_outside_tolerance() -> None:
    """Test that approx fails when values are outside tolerance."""
    assert not (0.1 == approx(0.2))
    assert not (1.0 == approx(2.0))
    assert not (100 == approx(110, rel=0.01, abs=1e-12))


def test_approx_list() -> None:
    """Test approx with lists."""
    assert [0.1 + 0.2, 0.3] == approx([0.3, 0.3])
    assert [1.0, 2.0, 3.0] == approx([1.0000001, 2.0000001, 3.0000001])


def test_approx_tuple() -> None:
    """Test approx with tuples."""
    assert (0.1 + 0.2, 0.3) == approx((0.3, 0.3))
    assert (1.0, 2.0, 3.0) == approx((1.0000001, 2.0000001, 3.0000001))


def test_approx_dict() -> None:
    """Test approx with dictionaries."""
    assert {"a": 0.1 + 0.2} == approx({"a": 0.3})
    assert {"x": 1.0, "y": 2.0} == approx({"x": 1.0000001, "y": 2.0000001})


def test_approx_nested_structures() -> None:
    """Test approx with nested data structures."""
    actual = {
        "values": [0.1 + 0.2, 0.3],
        "nested": {"x": 1.0, "y": 2.0},
    }
    expected = {
        "values": [0.3, 0.3],
        "nested": {"x": 1.0000001, "y": 2.0000001},
    }
    assert actual == approx(expected)


def test_approx_complex_numbers() -> None:
    """Test approx with complex numbers."""
    assert (1 + 2j) == approx(1.0000001 + 2.0000001j)
    assert complex(0.1 + 0.2, 0.3) == approx(complex(0.3, 0.3))


def test_approx_infinity() -> None:
    """Test approx with infinity values."""
    assert math.inf == approx(math.inf)
    assert -math.inf == approx(-math.inf)
    assert not (math.inf == approx(-math.inf))
    assert not (1.0 == approx(math.inf))


def test_approx_nan() -> None:
    """Test approx with NaN values."""
    # NaN should never equal anything, not even itself
    assert not (math.nan == approx(math.nan))
    assert not (math.nan == approx(1.0))
    assert not (1.0 == approx(math.nan))


def test_approx_zero() -> None:
    """Test approx with zero values."""
    assert 0.0 == approx(0.0)
    assert 0.0 == approx(1e-13, abs=1e-12)
    assert not (0.0 == approx(1e-6, abs=1e-12, rel=1e-12))


def test_approx_negative_numbers() -> None:
    """Test approx with negative numbers."""
    assert -1.0 == approx(-1.0000001)
    assert -0.1 - 0.2 == approx(-0.3)
    assert -100 == approx(-101, rel=0.02)


def test_approx_mixed_types() -> None:
    """Test approx with mixed int and float types."""
    assert 1 == approx(1.0)
    assert 1.0 == approx(1)
    assert [1, 2.0, 3] == approx([1.0, 2, 3.0])


def test_approx_empty_collections() -> None:
    """Test approx with empty collections."""
    assert [] == approx([])
    assert {} == approx({})
    assert () == approx(())


def test_approx_list_length_mismatch() -> None:
    """Test that approx fails when list lengths don't match."""
    assert not ([1, 2] == approx([1, 2, 3]))
    assert not ([1, 2, 3] == approx([1, 2]))


def test_approx_dict_key_mismatch() -> None:
    """Test that approx fails when dict keys don't match."""
    assert not ({"a": 1} == approx({"b": 1}))
    assert not ({"a": 1, "b": 2} == approx({"a": 1}))


def test_approx_type_mismatch() -> None:
    """Test that approx fails when types don't match."""
    assert not ([1, 2] == approx((1, 2)))  # list vs tuple
    assert not ({"a": 1} == approx([("a", 1)]))  # dict vs list
    assert not (1.0 == approx("1.0"))  # float vs string


def test_approx_with_none() -> None:
    """Test approx with None values."""
    # For None checks, use 'is None' instead of '== approx(None)'
    # These tests verify the internal handling when None appears in collections
    assert {"a": None} == approx({"a": None})
    assert not ({"a": None} == approx({"a": 0}))
    assert not ({"a": 0} == approx({"a": None}))


def test_approx_repr() -> None:
    """Test string representation of approx."""
    a = approx(1.0)
    assert "approx" in repr(a)
    assert "1.0" in repr(a)

    a = approx(1.0, rel=1e-3, abs=1e-6)
    assert "approx" in repr(a)
    assert "1.0" in repr(a)
    assert "rel=0.001" in repr(a) or "rel=1e-3" in repr(a)
    assert "abs=1e-06" in repr(a) or "abs=1e-6" in repr(a)


def test_approx_with_scientific_notation() -> None:
    """Test approx with numbers in scientific notation."""
    assert 1e-10 == approx(1.0000001e-10)
    assert 1e10 == approx(1.0000001e10)
    assert 6.022e23 == approx(6.022e23 + 1e17, rel=1e-6)


def test_approx_relative_tolerance_scaling() -> None:
    """Test that relative tolerance scales with the magnitude of numbers."""
    # For small numbers, absolute tolerance dominates
    assert 1e-12 == approx(2e-12, abs=1e-12, rel=1e-6)

    # For large numbers, relative tolerance dominates
    assert 1e12 == approx(1e12 + 1e6, rel=1e-6, abs=1e-12)


def test_approx_percentage_style_tolerance() -> None:
    """Test approx with percentage-style tolerance."""
    # 1% tolerance
    assert 100 == approx(101, rel=0.01, abs=0)
    assert 100 == approx(99, rel=0.01, abs=0)
    assert not (100 == approx(102, rel=0.01, abs=0))


def test_approx_usage_example() -> None:
    """Test the usage example from the docstring."""
    # Basic usage
    result = 0.1 + 0.2
    expected = 0.3
    assert result == approx(expected)

    # With relative tolerance
    assert result == approx(expected, rel=1e-6)

    # With absolute tolerance
    assert result == approx(expected, abs=1e-9)

    # With lists
    results = [0.1 + 0.2, 0.3]
    expecteds = [0.3, 0.3]
    assert results == approx(expecteds)

    # With dicts
    result_dict = {"a": 0.1 + 0.2}
    expected_dict = {"a": 0.3}
    assert result_dict == approx(expected_dict)
