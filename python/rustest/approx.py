"""Approximate comparison — a port of ``pytest.approx``.

Port of `_pytest/python_api.py` (pytest 8.4.2, l. 20-776): :class:`ApproxBase`,
:class:`ApproxNumpy`, :class:`ApproxMapping`, :class:`ApproxSequenceLike`,
:class:`ApproxScalar`, :class:`ApproxDecimal`, the :func:`approx` dispatcher and the three
helpers it turns on.

**MECHANISM M9 of the Phase 4 Task 1b sweep.** What was here before was a *reimplementation*
— one class, a recursive ``_approx_compare``, and a signature of its own — and it diverged
from pytest in three ways that each cost real tests in Apex Member Designer:

*(a) numpy dispatch.* ``ApproxBase.__array_ufunc__ = None`` and ``__array_priority__ = 100``
are the entire reason ``ndarray == approx(scalar)`` works under pytest: they make numpy's
``ndarray.__eq__`` return ``NotImplemented`` so Python falls back to approx's own ``__eq__``,
which returns a plain ``bool``. Without them numpy wins the dispatch and yields an
*elementwise bool array*, so the enclosing ``assert`` raises ``ValueError: The truth value of
an array with more than one element is ambiguous``. Measured in that suite's own venv:
``np.array([5., 5., 5.]) == pytest.approx(5.0)`` → ``True``; the same against the old
``rustest.approx`` → ``array([True, True, True])``.

*(b) ``None`` means "unspecified".* pytest's signature is ``approx(expected, rel=None,
abs=None, nan_ok=False)`` and :attr:`ApproxScalar.tolerance` resolves the defaults. rustest's
was ``approx(expected, rel=1e-6, abs=1e-12)``, taking what it was handed literally — so a
helper spelled ``def approx(expected, abs_tol=None): return pytest.approx(expected,
abs=abs_tol)``, which Member Designer's own conftest has, passed ``abs=None`` on every call
and produced ``TypeError: '>' not supported between instances of 'NoneType' and 'float'``.
It only fired when the two floats were **not bit-identical**, because the exact-equality
short circuit came first — which is why one test tripped it and hundreds of sibling
``approx`` assertions did not.

*(c) ``nan_ok``* did not exist at all: ``TypeError: unexpected keyword argument``.

Beyond those three, porting rather than paraphrasing brings the parts a reimplementation has
no reason to invent and a suite can still see: the ``1.0 ± 1e-06`` repr, ``__bool__``
refusing a boolean context, ``__hash__ = None``, ``Decimal`` getting its own defaults, the
refusal of nested containers, the rule that ``bool`` is *not* numeric, and the
``_repr_compare`` tables an assertion failure prints.
"""

from __future__ import annotations

import math
import pprint
import sys
from collections.abc import Collection, Mapping, Sequence, Sized
from decimal import Decimal
from numbers import Complex
from typing import Any


def _compare_approx(
    full_object: object,
    message_data: Sequence[tuple[str, str, str]],
    number_of_elements: int,
    different_ids: Sequence[object],
    max_abs_diff: float,
    max_rel_diff: float,
) -> list[str]:
    """The aligned Index/Obtained/Expected table an approx failure prints (l. 21-43)."""
    _ = full_object
    message_list = list(message_data)
    message_list.insert(0, ("Index", "Obtained", "Expected"))
    max_sizes = [0, 0, 0]
    for index, obtained, expected in message_list:
        max_sizes[0] = max(max_sizes[0], len(index))
        max_sizes[1] = max(max_sizes[1], len(obtained))
        max_sizes[2] = max(max_sizes[2], len(expected))
    return [
        f"comparison failed. Mismatched elements: {len(different_ids)} / {number_of_elements}:",
        f"Max absolute difference: {max_abs_diff}",
        f"Max relative difference: {max_rel_diff}",
    ] + [
        f"{indexes:<{max_sizes[0]}} | {obtained:<{max_sizes[1]}} | {expected:<{max_sizes[2]}}"
        for indexes, obtained, expected in message_list
    ]


class ApproxBase:
    """Shared machinery for every approx flavour — `python_api.py::ApproxBase` (l. 50-111)."""

    #: **The two attributes that make ``ndarray == approx(...)`` return a bool.**
    #: ``__array_ufunc__ = None`` opts this type out of numpy's ufunc protocol, so
    #: ``ndarray.__eq__`` answers ``NotImplemented`` and Python falls back to the reflected
    #: operand — this class's :meth:`__eq__`. ``__array_priority__`` is the pre-ufunc
    #: spelling of the same request, kept for older numpy. Omitting either lets numpy win the
    #: dispatch and produce an elementwise bool *array*, which makes the enclosing ``assert``
    #: raise ``ValueError: The truth value of an array ... is ambiguous`` (Task 1b, M9a).
    __array_ufunc__ = None
    __array_priority__ = 100

    def __init__(
        self,
        expected: Any,
        rel: Any = None,
        abs: Any = None,  # noqa: A002 - pytest's parameter name
        nan_ok: bool = False,
    ) -> None:
        __tracebackhide__ = True
        super().__init__()
        self.expected = expected
        self.abs = abs
        self.rel = rel
        self.nan_ok = nan_ok
        self._check_type()

    def __repr__(self) -> str:
        raise NotImplementedError

    def _repr_compare(self, other_side: Any) -> list[str]:
        return [
            "comparison failed",
            f"Obtained: {other_side}",
            f"Expected: {self}",
        ]

    def __eq__(self, actual: Any) -> bool:
        return all(a == self._approx_scalar(x) for a, x in self._yield_comparisons(actual))

    def __bool__(self) -> bool:
        """`ApproxBase.__bool__` (l. 79-84) — a refusal, not a value.

        ``if x == approx(y)`` is fine (that calls ``__eq__``); ``if approx(y)`` is a mistake
        that would otherwise be silently truthy.
        """
        __tracebackhide__ = True
        raise AssertionError(
            "approx() is not supported in a boolean context.\n"
            + "Did you mean: `assert a == approx(b)`?"
        )

    #: Unhashable, because ``__eq__`` is not an equivalence relation (l. 86-87).
    __hash__ = None  # type: ignore[assignment]

    def __ne__(self, actual: Any) -> bool:
        return not (actual == self)

    def _approx_scalar(self, x: Any) -> ApproxScalar:
        if isinstance(x, Decimal):
            return ApproxDecimal(x, rel=self.rel, abs=self.abs, nan_ok=self.nan_ok)
        return ApproxScalar(x, rel=self.rel, abs=self.abs, nan_ok=self.nan_ok)

    def _yield_comparisons(self, actual: Any) -> Any:
        """The (actual, expected) pairs :meth:`__eq__` walks."""
        raise NotImplementedError

    def _check_type(self) -> None:
        """Refuse an unusable ``expected``. Only the container flavours override it."""


def _recursive_sequence_map(f: Any, x: Any) -> Any:
    """Map *f* over a sequence of arbitrary depth (l. 114-122).

    ``list``/``tuple`` keep their own type; anything else sequence-like becomes a ``list``.
    """
    if isinstance(x, (list, tuple)):
        seq_type = type(x)
        return seq_type(_recursive_sequence_map(f, xi) for xi in x)
    elif _is_sequence_like(x):
        return [_recursive_sequence_map(f, xi) for xi in x]
    else:
        return f(x)


class ApproxNumpy(ApproxBase):
    """``expected`` is a numpy array — `python_api.py::ApproxNumpy` (l. 125-226)."""

    def __repr__(self) -> str:
        list_scalars = _recursive_sequence_map(self._approx_scalar, self.expected.tolist())
        return f"approx({list_scalars!r})"

    def _repr_compare(self, other_side: Any) -> list[str]:
        import itertools

        def get_value_from_nested_list(nested_list: Any, nd_index: tuple[Any, ...]) -> Any:
            value: Any = nested_list
            for i in nd_index:
                value = value[i]
            return value

        np_array_shape = self.expected.shape
        approx_side_as_seq = _recursive_sequence_map(self._approx_scalar, self.expected.tolist())

        other_side_as_array = _as_numpy_array(other_side)
        assert other_side_as_array is not None

        if np_array_shape != other_side_as_array.shape:
            return [
                "Impossible to compare arrays with different shapes.",
                f"Shapes: {np_array_shape} and {other_side_as_array.shape}",
            ]

        number_of_elements = self.expected.size
        max_abs_diff = -math.inf
        max_rel_diff = -math.inf
        different_ids: list[Any] = []
        for index in itertools.product(*(range(i) for i in np_array_shape)):
            approx_value = get_value_from_nested_list(approx_side_as_seq, index)
            other_value = get_value_from_nested_list(other_side_as_array, index)
            if approx_value != other_value:
                abs_diff = abs(approx_value.expected - other_value)
                max_abs_diff = max(max_abs_diff, abs_diff)
                if other_value == 0.0:
                    max_rel_diff = math.inf
                else:
                    max_rel_diff = max(max_rel_diff, abs_diff / abs(other_value))
                different_ids.append(index)

        message_data = [
            (
                str(index),
                str(get_value_from_nested_list(other_side_as_array, index)),
                str(get_value_from_nested_list(approx_side_as_seq, index)),
            )
            for index in different_ids
        ]
        return _compare_approx(
            self.expected,
            message_data,
            number_of_elements,
            different_ids,
            max_abs_diff,
            max_rel_diff,
        )

    def __eq__(self, actual: Any) -> bool:
        import numpy as np

        array: Any = actual
        if not np.isscalar(array):
            try:
                array = np.asarray(array)
            except Exception as e:
                raise TypeError(f"cannot compare '{actual}' to numpy.ndarray") from e

        # `np.isscalar` is a `TypeGuard` in numpy's stubs, so the checker narrows `array`
        # to the scalar types on the right-hand side of the `and`. It is wrong -- the guard
        # is negated -- and the ignore is cheaper than restructuring pytest's expression.
        if not np.isscalar(array) and array.shape != self.expected.shape:  # pyright: ignore[reportAttributeAccessIssue]
            return False

        return super().__eq__(array)

    __hash__ = None

    def _yield_comparisons(self, actual: Any) -> Any:
        import numpy as np

        if np.isscalar(actual):
            for i in np.ndindex(self.expected.shape):
                yield actual, self.expected[i].item()
        else:
            for i in np.ndindex(self.expected.shape):
                yield actual[i].item(), self.expected[i].item()


class ApproxMapping(ApproxBase):
    """``expected`` is a mapping with numeric values — l. 229-306.

    Keys must match **exactly** (as a set); only the values are compared with a tolerance.
    """

    def __repr__(self) -> str:
        return f"approx({ {k: self._approx_scalar(v) for k, v in self.expected.items()}!r})"

    def _repr_compare(self, other_side: Mapping[object, float]) -> list[str]:
        approx_side_as_map = {k: self._approx_scalar(v) for k, v in self.expected.items()}

        number_of_elements = len(approx_side_as_map)
        max_abs_diff = -math.inf
        max_rel_diff = -math.inf
        different_ids: list[Any] = []
        for (approx_key, approx_value), other_value in zip(
            approx_side_as_map.items(), other_side.values()
        ):
            if approx_value != other_value:
                # Both guards are pytest's: a mapping may legitimately hold `None` values,
                # and subtracting one is a TypeError rather than a mismatch.
                expected_value: Any = approx_value.expected
                # `other_value` is annotated `float` by the signature, but a real mapping
                # can hold `None`, and pytest guards for it. Kept.
                if expected_value is not None and other_value is not None:  # pyright: ignore[reportUnnecessaryComparison]
                    try:
                        max_abs_diff = max(max_abs_diff, abs(expected_value - other_value))
                        if expected_value == 0.0:
                            max_rel_diff = math.inf
                        else:
                            max_rel_diff = max(
                                max_rel_diff,
                                abs((expected_value - other_value) / expected_value),
                            )
                    except ZeroDivisionError:
                        pass
                different_ids.append(approx_key)

        message_data = [
            (str(key), str(other_side[key]), str(approx_side_as_map[key])) for key in different_ids
        ]

        return _compare_approx(
            self.expected,
            message_data,
            number_of_elements,
            different_ids,
            max_abs_diff,
            max_rel_diff,
        )

    def __eq__(self, actual: Any) -> bool:
        try:
            if set(actual.keys()) != set(self.expected.keys()):
                return False
        except AttributeError:
            return False

        return super().__eq__(actual)

    __hash__ = None

    def _yield_comparisons(self, actual: Any) -> Any:
        for k in self.expected.keys():
            yield actual[k], self.expected[k]

    def _check_type(self) -> None:
        """Nested mappings are refused, loudly (l. 297-303).

        A nested dict would compare by identity through ``ApproxScalar``, i.e. exactly, which
        silently means "no tolerance applies here" — so pytest raises instead. This is the
        one place the old rustest implementation was more permissive *and* wrong: it
        recursed, so a nested structure compared approximately under rustest and raised
        ``TypeError`` under pytest.
        """
        __tracebackhide__ = True
        for key, value in self.expected.items():
            if isinstance(value, type(self.expected)):
                msg = (
                    "pytest.approx() does not support nested dictionaries: "
                    "key={!r} value={!r}\n  full mapping={}"
                )
                raise TypeError(msg.format(key, value, pprint.pformat(self.expected)))


class ApproxSequenceLike(ApproxBase):
    """``expected`` is an ordered sequence of numbers — l. 309-375."""

    def __repr__(self) -> str:
        seq_type = type(self.expected)
        if seq_type not in (tuple, list):
            seq_type = list
        return f"approx({seq_type(self._approx_scalar(x) for x in self.expected)!r})"

    def _repr_compare(self, other_side: Sequence[float]) -> list[str]:
        if len(self.expected) != len(other_side):
            return [
                "Impossible to compare lists with different sizes.",
                f"Lengths: {len(self.expected)} and {len(other_side)}",
            ]

        approx_side_as_map = _recursive_sequence_map(self._approx_scalar, self.expected)

        number_of_elements = len(approx_side_as_map)
        max_abs_diff = -math.inf
        max_rel_diff = -math.inf
        different_ids: list[Any] = []
        for i, (approx_value, other_value) in enumerate(zip(approx_side_as_map, other_side)):
            if approx_value != other_value:
                try:
                    abs_diff = abs(approx_value.expected - other_value)
                    max_abs_diff = max(max_abs_diff, abs_diff)
                # Ignore non-numbers for the diff calculations (pytest #13012).
                except TypeError:
                    pass
                else:
                    if other_value == 0.0:
                        max_rel_diff = math.inf
                    else:
                        max_rel_diff = max(max_rel_diff, abs_diff / abs(other_value))
                different_ids.append(i)
        message_data = [
            (str(i), str(other_side[i]), str(approx_side_as_map[i])) for i in different_ids
        ]

        return _compare_approx(
            self.expected,
            message_data,
            number_of_elements,
            different_ids,
            max_abs_diff,
            max_rel_diff,
        )

    def __eq__(self, actual: Any) -> bool:
        try:
            if len(actual) != len(self.expected):
                return False
        except TypeError:
            return False
        return super().__eq__(actual)

    __hash__ = None

    def _yield_comparisons(self, actual: Any) -> Any:
        return zip(actual, self.expected)

    def _check_type(self) -> None:
        __tracebackhide__ = True
        for index, x in enumerate(self.expected):
            if isinstance(x, type(self.expected)):
                msg = (
                    "pytest.approx() does not support nested data structures: "
                    "{!r} at index {}\n  full sequence: {}"
                )
                raise TypeError(msg.format(x, index, pprint.pformat(self.expected)))


class ApproxScalar(ApproxBase):
    """``expected`` is a single number — `python_api.py::ApproxScalar` (l. 378-522)."""

    DEFAULT_ABSOLUTE_TOLERANCE: float | Decimal = 1e-12
    DEFAULT_RELATIVE_TOLERANCE: float | Decimal = 1e-6

    def __repr__(self) -> str:
        """``1.0 ± 1e-06``, ``(3+4j) ± 5e-06 ∠ ±180°`` — l. 384-419.

        Non-numerics and infinities print bare, because no tolerance applies to them; a
        tolerance that cannot be computed prints ``???`` rather than raising out of a repr.
        """
        expected: Any = self.expected
        if (
            isinstance(expected, bool)
            or (not isinstance(expected, (Complex, Decimal)))
            # pytest's own expression, quirk included: by this point `expected` is
            # narrowed to `Complex | Decimal`, so the `isinstance(..., bool)` can never fire.
            # Reproduced rather than "corrected" -- the `or` short-circuits on any non-zero
            # magnitude, so the dead operand only ever guards `abs(expected) == 0`, and
            # rewriting it would change which values take the bare-`str` path.
            or math.isinf(abs(expected) or isinstance(expected, bool))  # pyright: ignore[reportUnnecessaryIsInstance]
        ):
            return str(expected)

        try:
            if 1e-3 <= self.tolerance < 1e3:
                vetted_tolerance = f"{self.tolerance:n}"
            else:
                vetted_tolerance = f"{self.tolerance:.1e}"

            if (
                isinstance(self.expected, Complex)
                and self.expected.imag
                and not math.isinf(self.tolerance)
            ):
                vetted_tolerance += " ∠ ±180°"
        except ValueError:
            vetted_tolerance = "???"

        return f"{self.expected} ± {vetted_tolerance}"

    def __eq__(self, actual: Any) -> bool:
        """Port of l. 421-470. Every branch below is load-bearing:

        * a numpy array on the *actual* side is compared element by element (and the manual
          ``self.__eq__`` call is pytest's own guard against infinite recursion on old numpy);
        * ``bool`` is treated as **non**-numeric even though it supports the arithmetic, so
          ``True == approx(1)`` is ``False`` — probed, and the opposite of what a
          "numbers are numbers" implementation does;
        * exact equality short-circuits **before** any tolerance is computed, which is why
          the old implementation's ``abs=None`` crash only fired on non-identical floats;
        * NaN is equal to nothing unless ``nan_ok``, and infinity is equal to nothing but
          itself — the latter matters because a relative tolerance against infinity is
          infinite, which would make ``inf`` approximately equal to *everything*.
        """

        def is_bool(val: Any) -> bool:
            if isinstance(val, bool):
                return True
            if np := sys.modules.get("numpy"):
                return isinstance(val, np.bool_)
            return False

        asarray = _as_numpy_array(actual)
        if asarray is not None:
            return all(self.__eq__(a) for a in asarray.flat)

        if is_bool(self.expected) and not is_bool(actual):
            return False
        elif actual == self.expected:
            return True

        if is_bool(self.expected) or not (
            isinstance(self.expected, (Complex, Decimal)) and isinstance(actual, (Complex, Decimal))
        ):
            return False

        if math.isnan(abs(self.expected)):
            return self.nan_ok and math.isnan(abs(actual))

        if math.isinf(abs(self.expected)):
            return False

        result: bool = abs(self.expected - actual) <= self.tolerance
        return result

    __hash__ = None

    @property
    def tolerance(self) -> Any:
        """The larger of the absolute and relative tolerances — l. 475-522.

        **This property is where ``rel=None``/``abs=None`` acquire their meaning**, and the
        reason pytest's signature defaults them to ``None`` rather than to numbers.
        ``None`` is "unspecified", so it is replaced here with the class default; and if the
        caller gave an ``abs`` but no ``rel``, the absolute tolerance is returned *alone*
        rather than being maxed against a relative one they never asked for. The old rustest
        implementation defaulted them to ``1e-6``/``1e-12`` in the signature and took a
        literal ``None`` at face value, which is exactly the ``TypeError: '>' not supported
        between instances of 'NoneType' and 'float'`` Member Designer hit (Task 1b, M9b).
        """

        def set_default(x: Any, default: Any) -> Any:
            return x if x is not None else default

        absolute_tolerance = set_default(self.abs, self.DEFAULT_ABSOLUTE_TOLERANCE)

        if absolute_tolerance < 0:
            raise ValueError(f"absolute tolerance can't be negative: {absolute_tolerance}")
        if math.isnan(absolute_tolerance):
            raise ValueError("absolute tolerance can't be NaN.")

        if self.rel is None:
            if self.abs is not None:
                return absolute_tolerance

        relative_tolerance = set_default(self.rel, self.DEFAULT_RELATIVE_TOLERANCE) * abs(
            self.expected
        )

        if relative_tolerance < 0:
            raise ValueError(f"relative tolerance can't be negative: {relative_tolerance}")
        if math.isnan(relative_tolerance):
            raise ValueError("relative tolerance can't be NaN.")

        return max(relative_tolerance, absolute_tolerance)


class ApproxDecimal(ApproxScalar):
    """``expected`` is a ``Decimal`` — l. 525-548, with ``Decimal`` defaults so the
    arithmetic never mixes ``Decimal`` and ``float`` (which is a ``TypeError``)."""

    DEFAULT_ABSOLUTE_TOLERANCE = Decimal("1e-12")
    DEFAULT_RELATIVE_TOLERANCE = Decimal("1e-6")

    def __repr__(self) -> str:
        rel = Decimal.from_float(self.rel) if isinstance(self.rel, float) else self.rel
        abs_ = Decimal.from_float(self.abs) if isinstance(self.abs, float) else self.abs

        tol_str = "???"
        if rel is not None and Decimal("1e-3") <= rel <= Decimal("1e3"):
            tol_str = f"{rel:.1e}"
        elif abs_ is not None:
            tol_str = f"{abs_:.1e}"

        return f"{self.expected} ± {tol_str}"

    __hash__ = None


def approx(
    expected: Any,
    rel: Any = None,
    abs: Any = None,  # noqa: A002 - pytest's parameter name
    nan_ok: bool = False,
) -> ApproxBase:
    """Assert that two numbers, or two ordered sequences of numbers, are approximately equal.

    Port of `_pytest/python_api.py::approx` (l. 551-776) — the dispatcher, whose whole body
    is the ``cls`` selection below.

    ``rel`` and ``abs`` default to ``None``, **not** to numbers: ``None`` means "unspecified"
    and is resolved by :attr:`ApproxScalar.tolerance`, which is what makes ``rel=None`` and
    ``abs=None`` legal to pass explicitly. Defaults are ``rel=1e-6`` and ``abs=1e-12``,
    combined as ``max(rel * abs(expected), abs)`` — unless only ``abs`` was given, in which
    case it is used alone.

    ``nan_ok=True`` makes NaN compare equal to NaN, which nothing else does.

    Dispatch order is pytest's and is not alphabetical: ``Decimal`` first (it is also a
    ``Complex``-adjacent number but needs ``Decimal`` tolerances), then ``Mapping``, then
    numpy (only if numpy is *already imported* — see :func:`_as_numpy_array`), then anything
    sequence-like, then an explicit refusal for unordered collections such as ``set``, and
    finally the scalar case.

    Usage::

        assert 0.1 + 0.2 == approx(0.3)
        assert 0.1 + 0.2 == approx(0.3, rel=1e-6)
        assert 0.1 + 0.2 == approx(0.3, abs=1e-9)
        assert [0.1 + 0.2, 0.3] == approx([0.3, 0.3])
        assert {"a": 0.1 + 0.2} == approx({"a": 0.3})
        assert float("nan") == approx(float("nan"), nan_ok=True)
    """
    if isinstance(expected, Decimal):
        cls: type[ApproxBase] = ApproxDecimal
    elif isinstance(expected, Mapping):
        cls = ApproxMapping
    elif _is_numpy_array(expected):
        expected = _as_numpy_array(expected)
        cls = ApproxNumpy
    elif _is_sequence_like(expected):
        cls = ApproxSequenceLike
    elif isinstance(expected, Collection) and not isinstance(expected, (str, bytes)):
        msg = f"pytest.approx() only supports ordered sequences, but got: {expected!r}"
        raise TypeError(msg)
    else:
        cls = ApproxScalar

    return cls(expected, rel, abs, nan_ok)


def _is_sequence_like(expected: object) -> bool:
    """l. 778-783 — indexable and sized, excluding ``str``/``bytes``."""
    return (
        hasattr(expected, "__getitem__")
        and isinstance(expected, Sized)
        and not isinstance(expected, (str, bytes))
    )


def _is_numpy_array(obj: object) -> bool:
    return _as_numpy_array(obj) is not None


def _as_numpy_array(obj: object) -> Any:
    """*obj* as an ndarray, or ``None`` — l. 793-806.

    ``sys.modules.get("numpy")`` rather than an import: pytest deliberately answers ``None``
    when numpy is **not already imported**, so a suite that never touches numpy never pays
    for importing it and never changes behaviour because something else did. The
    ``np.isscalar`` guard is pytest's, and it is against infinite recursion — numpy scalars
    carry ``__array__``.
    """
    np: Any = sys.modules.get("numpy")
    if np is not None:
        if np.isscalar(obj):
            return None
        elif isinstance(obj, np.ndarray):
            return obj
        elif hasattr(obj, "__array__") or hasattr("obj", "__array_interface__"):
            return np.asarray(obj)
    return None
