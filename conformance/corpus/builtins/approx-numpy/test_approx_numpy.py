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

**Its own case directory, and numpy is a declared dev dependency, for one reason.** This
file used to live beside `test_approx_numeric.py` in `builtins/approx-numeric`, and its
docstring claimed the `importorskip` above meant "both runners skip it identically, and
the case still grades either way". That was false for the **v1** gate, and it made one
case's verdict a function of what happened to be installed:

* with numpy present, both runners run all eight tests and the case MATCHES everywhere;
* with numpy absent -- which is what `uv sync --all-extras` produced in CI, since nothing
  declared numpy -- pytest skips the module and v1 raises the `Skipped` out of collection
  as an unimportable file. That is a collection error, and a collection error aborts the
  session under pytest semantics: exit 2 against pytest's 0. The v1 gate went red on a
  machine without numpy and green on one with it.

So numpy is now in the `dev` extra (`pyproject.toml`), which makes the verdict the same
everywhere and keeps the numpy differential **graded, not waived, under all three gates**.
A waiver would have been the wrong instrument twice over: it would be a STALE-WAIVER on
every machine that has numpy, and the v1 module-level-skip gap it would cite is already
pinned -- environment-independently -- by `collection/module-level-skip`, whose
`test_importorskip_missing.py` skips on a module that is guaranteed absent
(`rustest_no_such_module_9f3c`) rather than on one that is merely optional.

The `importorskip` stays as the pytest-idiomatic guard for anyone running the corpus
outside the dev environment; it is no longer load-bearing for the gate.
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
