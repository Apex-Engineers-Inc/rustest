"""`pytest.approx` -- the numeric corpus, less the numpy half (see `builtins/approx-numpy`).

Port target: `_pytest/python_api.py` l. 20-806 -- `ApproxBase`, `ApproxNumpy`,
`ApproxMapping`, `ApproxSequenceLike`, `ApproxScalar`, `ApproxDecimal` and the `approx()`
dispatcher.

MECHANISM M9 of the Phase 4 Task 1b sweep. What rustest shipped was a REIMPLEMENTATION,
not a port, and it diverged in ways that cost real tests in Apex Member Designer:

  (a) no `__array_ufunc__`/`__array_priority__`, so `ndarray == approx(scalar)` produced an
      elementwise bool ARRAY and the enclosing assert raised ValueError (that half is the
      `builtins/approx-numpy` case, split out so a missing optional dependency can only
      move one case's verdict -- see its module docstring);
  (b) `rel`/`abs` defaulted to numbers instead of to None, so an explicit `abs=None` --
      which MD's own `def approx(expected, abs_tol=None)` helper passes on every call --
      became `TypeError: '>' not supported between 'NoneType' and 'float'`;
  (c) no `nan_ok` at all.

Everything below is an oracle answer probed on pytest 8.4.2, and several of them are the
opposite of what a from-scratch implementation reaches for.
"""

import math
from decimal import Decimal

import pytest

# --- the three defects, directly -------------------------------------------


def test_none_tolerances_mean_unspecified():
    """`rel=None`/`abs=None` are legal and mean "use the default" (`tolerance`, l. 475-522)."""
    assert 1.0000001 == pytest.approx(1.0, abs=None)
    assert 1.0000001 == pytest.approx(1.0, rel=None)
    assert 1.0000001 == pytest.approx(1.0, rel=None, abs=None)


def test_the_member_designer_helper_shape():
    """MD's conftest wraps approx and forwards `abs=abs_tol`, defaulting to None.

    It only ever failed for values that are NOT bit-identical, because exact equality
    short-circuits before any tolerance is computed -- which is why one test tripped it and
    hundreds of sibling assertions did not.
    """

    def md_approx(expected, abs_tol=None):
        return pytest.approx(expected, abs=abs_tol)

    assert 1.0 == md_approx(1.0)  # bit-identical: never reached the tolerance
    assert 1.0000001 == md_approx(1.0)  # not identical: this is the one that used to raise
    assert 0.5000000001 == md_approx(0.5)


def test_nan_ok():
    assert float("nan") == pytest.approx(float("nan"), nan_ok=True)
    assert not (float("nan") == pytest.approx(float("nan")))
    assert not (1.0 == pytest.approx(float("nan"), nan_ok=True))


# --- tolerance semantics ---------------------------------------------------


def test_relative_tolerance_scales_the_expected_value():
    """`rel * abs(self.expected)` (l. 502-504) -- not the larger of the two operands.

    rustest used `rel * max(abs(actual), abs(expected))`, a wider and asymmetric window.
    """
    assert 100 == pytest.approx(101, rel=0.01, abs=0)
    assert not (100 == pytest.approx(99, rel=0.01, abs=0))
    assert 100 == pytest.approx(99, rel=0.011, abs=0)


def test_abs_alone_is_used_alone():
    """With `abs` given and `rel` omitted, the relative tolerance is not consulted at all."""
    assert 1e6 == pytest.approx(1e6 + 0.5, abs=1.0)
    assert not (1e6 == pytest.approx(1e6 + 2.0, abs=1.0))


def test_both_given_takes_the_larger():
    assert 1e-12 == pytest.approx(2e-12, abs=1e-12, rel=1e-6)
    assert 1e12 == pytest.approx(1e12 + 1e6, rel=1e-6, abs=1e-12)


def test_negative_or_nan_tolerances_raise():
    with pytest.raises(ValueError, match="absolute tolerance can't be negative"):
        _ = 1.0 == pytest.approx(2.0, abs=-1)
    with pytest.raises(ValueError, match="relative tolerance can't be negative"):
        _ = 1.0 == pytest.approx(2.0, rel=-1)
    with pytest.raises(ValueError, match="absolute tolerance can't be NaN"):
        _ = 1.0 == pytest.approx(2.0, abs=float("nan"))


# --- the surprising branches ------------------------------------------------


def test_infinity_is_approximately_equal_to_nothing_but_itself():
    """A relative tolerance against inf is infinite, which would make inf ~= everything."""
    assert math.inf == pytest.approx(math.inf)
    assert -math.inf == pytest.approx(-math.inf)
    assert not (math.inf == pytest.approx(-math.inf))
    assert not (1.0 == pytest.approx(math.inf))
    assert not (math.inf == pytest.approx(1.0))


def test_bool_is_not_numeric():
    """`is_bool(expected) and not is_bool(actual)` -> False (l. 440-441), before anything."""
    assert not (1 == pytest.approx(True))
    assert True == pytest.approx(True)
    # ...but a bool ACTUAL against a numeric expected goes through exact equality first.
    assert True == pytest.approx(1)


def test_a_non_numeric_falls_back_to_strict_equality():
    assert not (1.0 == pytest.approx("1.0"))
    assert "x" == pytest.approx("x")
    assert None == pytest.approx(None)


def test_approx_is_not_allowed_in_a_boolean_context():
    """`ApproxBase.__bool__` raises rather than being silently truthy (l. 79-84)."""
    with pytest.raises(AssertionError, match="not supported in a boolean context"):
        bool(pytest.approx(1.0))


def test_approx_is_unhashable():
    with pytest.raises(TypeError):
        hash(pytest.approx(1.0))


# --- containers -------------------------------------------------------------


def test_sequences():
    assert [0.1 + 0.2, 0.3] == pytest.approx([0.3, 0.3])
    assert (0.1 + 0.2, 0.3) == pytest.approx((0.3, 0.3))
    assert [1, 2] == pytest.approx((1, 2))  # list vs tuple is fine
    assert [] == pytest.approx([])
    assert not ([1, 2] == pytest.approx([1, 2, 3]))


def test_mappings():
    assert {"a": 0.1 + 0.2} == pytest.approx({"a": 0.3})
    assert {} == pytest.approx({})
    assert not ({"a": 1} == pytest.approx({"b": 1}))
    assert not ({"a": 1, "b": 2} == pytest.approx({"a": 1}))


def test_nested_containers_are_refused():
    """A container holding its OWN type is a TypeError (l. 297-303, l. 370-375)."""
    with pytest.raises(TypeError, match="does not support nested dictionaries"):
        pytest.approx({"nested": {"x": 1.0}})
    with pytest.raises(TypeError, match="does not support nested data structures"):
        pytest.approx([[1.0], [2.0]])


def test_a_list_inside_a_dict_is_compared_exactly():
    """Not refused -- but the inner list reaches `ApproxScalar`, so no tolerance applies.

    This is the trap the refusal above exists to make loud, and the reason rustest's old
    recursive comparison was a divergence rather than a courtesy.
    """
    assert not ({"v": [0.1 + 0.2]} == pytest.approx({"v": [0.3]}))
    assert {"v": [0.3]} == pytest.approx({"v": [0.3]})


def test_unordered_collections_are_refused():
    with pytest.raises(TypeError, match="only supports ordered sequences"):
        pytest.approx({1, 2})


# --- other numeric types ----------------------------------------------------


def test_complex():
    assert (1 + 2j) == pytest.approx(1.0000001 + 2.0000001j)
    assert complex(0.1 + 0.2, 0.3) == pytest.approx(complex(0.3, 0.3))


def test_decimal_gets_decimal_tolerances():
    """`ApproxDecimal` overrides the defaults so the arithmetic never mixes types."""
    assert Decimal("0.1") + Decimal("0.2") == pytest.approx(Decimal("0.3"))
    assert not (Decimal("0.1") == pytest.approx(Decimal("0.2")))


# --- repr -------------------------------------------------------------------


def test_repr_is_expected_plus_minus_tolerance():
    assert repr(pytest.approx(1.0)) == "1.0 ± 1.0e-06"
    assert repr(pytest.approx(1.0, rel=1e-3, abs=1e-6)) == "1.0 ± 0.001"
    assert repr(pytest.approx([1.0])) == "approx([1.0 ± 1.0e-06])"
    assert repr(pytest.approx((1.0,))) == "approx((1.0 ± 1.0e-06,))"
    assert repr(pytest.approx({"a": 1.0})) == "approx({'a': 1.0 ± 1.0e-06})"


def test_repr_shows_no_tolerance_where_none_applies():
    assert repr(pytest.approx(math.inf)) == "inf"
    assert repr(pytest.approx("x")) == "x"
