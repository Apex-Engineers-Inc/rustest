"""Tests for the approx feature."""

import math

import pytest

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


def test_approx_refuses_nested_structures() -> None:
    """Nested containers are a TypeError, not a recursive comparison.

    `ApproxMapping._check_type` / `ApproxSequenceLike._check_type`
    (`_pytest/python_api.py` l. 297-303 and l. 370-375). rustest used to recurse, which is
    the more permissive answer and the wrong one: a nested dict reaches `ApproxScalar` and
    is compared by exact equality, so the tolerance silently stops applying. pytest refuses
    the shape instead. Probed on 8.4.2: `pytest.approx({"nested": {"x": 1.0}})` raises
    `pytest.approx() does not support nested dictionaries: key='nested' value={'x': 1.0}`.
    """
    with pytest.raises(TypeError, match="does not support nested dictionaries"):
        approx({"values": [0.3, 0.3], "nested": {"x": 1.0, "y": 2.0}})

    with pytest.raises(TypeError, match="does not support nested data structures"):
        approx([[1.0, 2.0], [3.0]])

    # A list inside a dict is not refused -- `_check_type` only rejects a value of the
    # container's OWN type -- but it is compared by EXACT equality, because the inner list
    # reaches `ApproxScalar` and falls through to the non-numeric branch. Probed on 8.4.2:
    # `{"values": [0.1 + 0.2, 0.3]} == pytest.approx({"values": [0.3, 0.3]})` is False.
    # This is the trap pytest's refusal of same-type nesting exists to make loud, and the
    # reason rustest's old recursive comparison was a divergence rather than a courtesy.
    assert not ({"values": [0.1 + 0.2, 0.3]} == approx({"values": [0.3, 0.3]}))
    assert {"values": [0.3, 0.3]} == approx({"values": [0.3, 0.3]})


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
    """Test that approx fails when fundamentally different types don't match."""
    # Note: list vs tuple now passes (relaxed type checking like pytest.approx)
    assert [1, 2] == approx((1, 2))  # list vs tuple - NOW WORKS
    # But these should still fail (fundamentally different types)
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
    """`ApproxScalar.__repr__` is `<expected> ± <tolerance>` (l. 384-419).

    Not `approx(...)` with the keywords echoed back, which is what rustest printed. The
    tolerance is the *resolved* one, so it reflects `tolerance`'s rules -- with both `rel`
    and `abs` given, the larger wins, and `1e-3 <= tol < 1e3` prints in `n` format while
    anything outside prints in `.1e`. Probed on pytest 8.4.2: `repr(approx(1.0))` is
    `1.0 \u00b1 1.0e-06` and `repr(approx(1.0, rel=1e-3, abs=1e-6))` is `1.0 \u00b1 0.001`.
    """
    assert repr(approx(1.0)) == "1.0 \u00b1 1.0e-06"
    assert repr(approx(1.0, rel=1e-3, abs=1e-6)) == "1.0 \u00b1 0.001"

    # Containers wrap their elements' reprs.
    assert repr(approx([1.0])) == "approx([1.0 \u00b1 1.0e-06])"
    assert repr(approx({"a": 1.0})) == "approx({'a': 1.0 \u00b1 1.0e-06})"

    # No tolerance is shown for a value no tolerance applies to.
    assert repr(approx(math.inf)) == "inf"


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
    """The relative tolerance scales the EXPECTED value, not the larger of the two.

    `ApproxScalar.tolerance` (l. 502-504): `rel * abs(self.expected)`. rustest used
    `rel * max(abs(actual), abs(expected))`, which is a wider window and is not symmetric
    with pytest's -- it made `100 == approx(99, rel=0.01)` true (window 1.0 from the actual)
    where pytest makes it false (window 0.99 from the expected). Probed on 8.4.2: `False`.
    """
    assert 100 == approx(101, rel=0.01, abs=0)  # window 1.01 around 101
    assert not (100 == approx(99, rel=0.01, abs=0))  # window 0.99 around 99 -- misses by .01
    assert 100 == approx(99, rel=0.011, abs=0)  # widen it slightly and it lands
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
