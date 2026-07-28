"""`pytest.approx` against numpy -- MECHANISM M9a, the ValueError that is not a ValueError.

`ApproxBase.__array_ufunc__ = None` and `__array_priority__ = 100` are the entire reason
`ndarray == approx(scalar)` yields a plain `bool`: they make numpy's `ndarray.__eq__`
return `NotImplemented`, so Python falls back to approx's own `__eq__`. Without them numpy
wins the dispatch and produces an elementwise bool ARRAY, and the enclosing `assert` raises
`ValueError: The truth value of an array with more than one element is ambiguous`.

Measured in Apex Member Designer's own venv during the Task 1b sweep:
`np.array([5., 5., 5.]) == pytest.approx(5.0)` -> `True`;
the same against the old `rustest.approx` -> `array([True, True, True])`.
Two of MD's four residual failures were exactly this.

This file skips itself when numpy is absent, which is also a live exercise of the
module-level-skip machinery M7 added -- both runners skip it identically, and the case
still grades either way.
"""

import pytest

np = pytest.importorskip("numpy")


def test_array_against_a_scalar_is_a_bool():
    result = np.array([5.0, 5.0, 5.0]) == pytest.approx(5.0)
    assert isinstance(result, bool)
    assert result is True


def test_array_against_a_scalar_inside_an_assert():
    """The shape that used to raise: the assert itself is the failing operation."""
    assert np.array([5.0, 5.0, 5.0]) == pytest.approx(5.0)
    assert not (np.array([5.0, 6.0]) == pytest.approx(5.0))


def test_array_against_an_array():
    assert np.array([1.0, 2.0]) == pytest.approx(np.array([1.0000001, 2.0000001]))
    assert not (np.array([1.0, 2.0]) == pytest.approx(np.array([1.0, 3.0])))


def test_shape_mismatch_is_false_not_an_error():
    assert not (np.array([1.0, 2.0]) == pytest.approx(np.array([1.0, 2.0, 3.0])))


def test_list_against_an_array():
    assert [1.0, 2.0] == pytest.approx(np.array([1.0000001, 2.0000001]))


def test_numpy_scalar_against_a_python_float():
    assert np.float64(1.0000001) == pytest.approx(1.0)


def test_array_repr():
    assert repr(pytest.approx(np.array([1.0]))) == "approx([1.0 ± 1.0e-06])"


def test_two_dimensional():
    assert np.array([[1.0, 2.0], [3.0, 4.0]]) == pytest.approx(
        np.array([[1.0000001, 2.0000001], [3.0000001, 4.0000001]])
    )
