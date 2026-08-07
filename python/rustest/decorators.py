"""User facing decorators mirroring the most common pytest helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import inspect
import sys
from typing import TYPE_CHECKING, Any, Final, ParamSpec, TypeVar, overload, cast

P = ParamSpec("P")
R = TypeVar("R")
Q = ParamSpec("Q")
S = TypeVar("S")
TFunc = TypeVar("TFunc", bound=Callable[..., Any])

# Valid fixture scopes
VALID_SCOPES = frozenset(["function", "class", "module", "package", "session"])


def _normalize_param_marks(marks: Any) -> tuple[Any, ...]:
    """One mark, an iterable of marks, or nothing -> a tuple.

    Port of `_pytest/mark/structures.py::ParameterSet.param` (l. 60-73), which does
    ``if isinstance(marks, MarkDecorator): marks = (marks,) else: assert isinstance(marks,
    collections.abc.Collection)``. A bare (uncalled) `mark.slow` is a `BareOrFactoryMark`
    here rather than a `MarkDecorator`, and it is just as legal a value, so the test is
    "does it answer `.name`" rather than an isinstance against one class.
    """
    if marks is None:
        return ()
    if isinstance(getattr(marks, "name", None), str):
        return (marks,)
    if isinstance(marks, (list, tuple)):
        return tuple(cast("Sequence[Any]", marks))
    raise TypeError(f"marks must be a mark or a collection of marks, got {marks!r}")


def _mark_payloads(marks: Sequence[Any]) -> list[dict[str, Any]]:
    """Marks -> the ``{"name", "args", "kwargs"}`` dicts the worker already reads.

    The same shape ``MarkDecorator.__call__`` writes into ``__rustest_marks__``, so
    ``_worker::_spec_from_mark_dict`` consumes a per-parameter mark with no new reader.
    ``_normalize_args`` is deliberately not applied: it evaluates a *string* skipif condition
    against a target function, and a parameter set has no target.
    """
    payloads: list[dict[str, Any]] = []
    for mark in marks:
        name = getattr(mark, "name", None)
        if not isinstance(name, str):
            raise TypeError(f"{mark!r} is not a mark")
        payloads.append(
            {
                "name": name,
                "args": tuple(getattr(mark, "args", ())),
                "kwargs": dict(getattr(mark, "kwargs", {})),
            }
        )
    return payloads


class ParameterSet:
    """Represents a single parameter set for pytest.param().

    This class holds the values for a parametrized test case along with
    optional id and marks metadata.
    """

    def __init__(self, values: tuple[Any, ...], id: str | None = None, marks: Any = None):
        super().__init__()
        self.values = values
        self.id = id
        #: The marks this **one parameter set** carries, normalised to a tuple.
        #:
        #: `_pytest/mark/structures.py::ParameterSet.param` accepts a single mark or an
        #: iterable of them and stores `tuple(marks)`; `Metafunc.parametrize` then hands each
        #: set's marks to the item built from it, so
        #: `pytest.param(x, marks=pytest.mark.xfail(...))` xfails **that case alone**.
        #: rustest stored the attribute and read it nowhere ("Currently not used, but stored
        #: for future support") until Phase 4 Task 1's re-sweep measured the cost: 9 Apex
        #: Member Designer tests reporting `failed` where pytest reports `xfailed`.
        self.marks: tuple[Any, ...] = _normalize_param_marks(marks)

    def __repr__(self) -> str:
        return f"ParameterSet(values={self.values!r}, id={self.id!r})"


@overload
def fixture(
    func: Callable[P, R],
    *,
    scope: str = "function",
    autouse: bool = False,
    name: str | None = None,
    params: Sequence[Any] | None = None,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
) -> Callable[P, R]: ...


@overload
def fixture(
    *,
    scope: str = "function",
    autouse: bool = False,
    name: str | None = None,
    params: Sequence[Any] | None = None,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def fixture(
    func: Callable[P, R] | None = None,
    *,
    scope: str = "function",
    autouse: bool = False,
    name: str | None = None,
    params: Sequence[Any] | None = None,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Mark a function as a fixture with a specific scope.

    Args:
        func: The function to decorate (when used without parentheses)
        scope: The scope of the fixture. One of:
            - "function": New instance for each test function (default)
            - "class": Shared across all test methods in a class
            - "module": Shared across all tests in a module
            - "package": Shared across all tests in a package
            - "session": Shared across all tests in the session
        autouse: If True, the fixture will be automatically used by all tests
            in its scope without needing to be explicitly requested (default: False)
        name: Override the fixture name (default: use the function name)
        params: Optional list of parameter values. The fixture will be called
            once for each parameter, and tests using this fixture will be run
            once for each parameter value. Access the current value via request.param.
        ids: Optional list of string IDs or a callable to generate IDs for each
            parameter value. If not provided, IDs are auto-generated.

    Usage:
        @fixture
        def my_fixture():
            return 42

        @fixture(scope="module")
        def shared_fixture():
            return expensive_setup()

        @fixture(autouse=True)
        def setup_fixture():
            # This fixture will run automatically before each test
            setup_environment()

        @fixture(name="db")
        def _database_fixture():
            # This fixture is available as "db", not "_database_fixture"
            return Database()

        @fixture(params=[1, 2, 3])
        def number(request):
            # This fixture will provide values 1, 2, 3 to tests
            return request.param

        @fixture(params=["mysql", "postgres"], ids=["MySQL", "PostgreSQL"])
        def database(request):
            # Tests will run with both database types
            return create_db(request.param)
    """
    if scope not in VALID_SCOPES:
        valid = ", ".join(sorted(VALID_SCOPES))
        msg = f"Invalid fixture scope '{scope}'. Must be one of: {valid}"
        raise ValueError(msg)

    def decorator(f: Callable[P, R]) -> Callable[P, R]:
        setattr(f, "__rustest_fixture__", True)
        setattr(f, "__rustest_fixture_scope__", scope)
        setattr(f, "__rustest_fixture_autouse__", autouse)
        if name is not None:
            setattr(f, "__rustest_fixture_name__", name)

        # Handle fixture parametrization
        if params is not None:
            # Build parameter cases with IDs
            param_cases = _build_fixture_params(params, ids, _fixture_id_name(f))
            setattr(f, "__rustest_fixture_params__", param_cases)

        return f

    # Support both @fixture and @fixture(scope="...")
    if func is not None:
        return decorator(func)
    return decorator


def _fixture_id_name(func: object) -> str:
    """The name a fixture's params are parametrized under — its own registered name."""
    override = getattr(func, "__rustest_fixture_name__", None)
    if isinstance(override, str):
        return override
    name = getattr(func, "__name__", None)
    return name if isinstance(name, str) else "param"


def _build_fixture_params(
    params: Sequence[Any],
    ids: Sequence[str] | Callable[[Any], str | None] | None,
    fixture_name: str = "param",
) -> list[dict[str, Any]]:
    """Build fixture parameter cases with IDs.

    Args:
        params: The parameter values
        ids: Optional IDs for each parameter value

    Returns:
        A list of dicts with 'id' and 'value' keys
    """
    cases: list[dict[str, Any]] = []
    ids_is_callable = callable(ids)

    if ids is not None and not ids_is_callable:
        if len(ids) != len(params):
            msg = "ids must match the number of params"
            raise ValueError(msg)

    for index, param_value in enumerate(params):
        # Handle ParameterSet objects (from pytest.param())
        param_set_id: str | None = None
        actual_value: Any = param_value
        if isinstance(param_value, ParameterSet):
            param_set_id = param_value.id
            # For fixture params, we expect a single value
            actual_value = (
                param_value.values[0] if len(param_value.values) == 1 else param_value.values
            )

        # A fixture's params bind to the fixture's own name, which is what pytest passes as
        # `argname` when it parametrizes one (`FixtureManager.pytest_generate_tests` calls
        # `metafunc.parametrize(argname, fixturedef.params, ...)`), so the `<argname><index>`
        # fallback reads `flavour0`/`flavour1` rather than `param0`/`param1`.
        case_id = _resolve_case_id(
            param_set_id=param_set_id,
            ids=ids,
            ids_is_callable=ids_is_callable,
            value=actual_value,
            index=index,
            bound=[(fixture_name, actual_value)],
        )

        case: dict[str, Any] = {"id": case_id, "value": actual_value}
        # A fixture's params take marks too -- `pytest.fixture(params=[pytest.param(x,
        # marks=pytest.mark.xfail(...))])`. pytest reaches them through the same
        # `ParameterSet`, because `FixtureManager.pytest_generate_tests` hands
        # `fixturedef.params` straight to `metafunc.parametrize`, and Apex Member Designer's
        # CL scenarios are exactly this shape: 9 tests reporting `failed` where pytest
        # reports `xfailed`.
        if isinstance(param_value, ParameterSet) and param_value.marks:
            case["marks"] = _mark_payloads(param_value.marks)
        cases.append(case)

    return cases


#: `_pytest/compat.py` l. 187-192 — every non-printable ASCII code point, plus the three
#: whitespace escapes, mapped to their backslash form.
_NON_PRINTABLE_ASCII: Final[dict[int, str]] = {
    **{i: f"\\x{i:02x}" for i in range(128) if i not in range(32, 127)},
    ord("\t"): "\\t",
    ord("\r"): "\\r",
    ord("\n"): "\\n",
}


def _ascii_escaped(value: str | bytes) -> str:
    """Port of `_pytest/compat.py::ascii_escaped` (l. 195-215).

    Bytes are decoded with ``backslashreplace`` so every byte survives as an escape rather
    than as whatever UTF-8 it happened to spell; strings go through ``unicode_escape``. The
    translate table then replaces the non-printable ASCII the first step leaves behind.
    """
    if isinstance(value, bytes):
        ret = value.decode("ascii", "backslashreplace")
    else:
        ret = value.encode("unicode_escape").decode("ascii")
    return ret.translate(_NON_PRINTABLE_ASCII)


def _idval_from_value(value: Any) -> str | None:
    """Port of `_pytest/python.py::IdMaker._idval_from_value` (l. 989-1007).

    ``None`` means "this type has no id of its own", and the caller falls back to
    ``<argname><index>``. That fallback is the rule rustest used to be missing: it invented a
    value-derived name for containers (``dict(1)``, ``empty_dict``, ``1-2``) and
    ``param<index>`` for everything else, where pytest says ``x0``/``v0``. 915 of click's ids
    and 95 of jinja2's differed on nothing else.

    The order is pytest's and matters twice over: ``bool`` is an ``int`` so it never reaches
    the enum branch, and ``str``/``bytes`` are tested before everything because a ``str`` has
    no ``__name__`` but plenty of objects do.
    """
    import enum
    import re

    if isinstance(value, (str, bytes)):
        return _ascii_escaped(value)
    if value is None or isinstance(value, (float, int, bool, complex)):
        return str(value)
    if isinstance(value, re.Pattern):
        return _ascii_escaped(cast("re.Pattern[str]", value).pattern)
    if isinstance(value, enum.Enum):
        return str(value)
    name = getattr(value, "__name__", None)
    if isinstance(name, str):
        # A class, a function, a module -- anything that names itself.
        return name
    return None


def _generate_param_id(value: Any, index: int, argname: str = "param") -> str:
    """One id component: the value's own spelling, or ``<argname><index>``.

    Port of `_pytest/python.py::IdMaker._idval` minus the two hooks rustest has no equivalent
    for (``ids=`` is applied by :func:`_resolve_case_id`, and there is no
    ``pytest_make_parametrize_id``). The fallback is
    ``IdMaker._idval_from_argname`` (l. 1023-1027): ``str(argname) + str(idx)``.
    """
    idval = _idval_from_value(value)
    return idval if idval is not None else f"{argname}{index}"


def _resolve_case_id(
    *,
    param_set_id: str | None,
    ids: Sequence[str] | Callable[[Any], str | None] | None,
    ids_is_callable: bool,
    value: Any,
    index: int,
    bound: Sequence[tuple[str, Any]] = (),
) -> str:
    """Resolve the ID for a parametrization case.

    Priority: ParameterSet.id > callable ids > explicit ids list > auto-generated.
    """
    if param_set_id is not None:
        return param_set_id
    if ids is None:
        # `"-".join(self._idval(val, argname, idx) for val, argname in zip(values, argnames))`
        # -- `IdMaker._resolve_ids` l. 945-948. One component per **argname**, which is what
        # makes the fallback `<argname><index>` rather than a single `param<index>` for the
        # whole set.
        return "-".join(_generate_param_id(item, index, name) for name, item in bound)
    if ids_is_callable:
        # **Per value, not per tuple.** `_pytest/python.py::IdMaker._idval` calls the
        # `ids` callable with each individual argvalue and joins the results with `-`;
        # rustest called it once with the whole tuple, so
        # `ids=lambda v: f"<{v}>"` over `("a,b", [(1, "x")])` produced
        # `[<(1, 'x')>]` where pytest produces `[<1>-<x>]`. A value the callable answers
        # `None` for falls back to that *component's* generated id, again per value
        # (`if id is None: ... else: return _idvalset(...)`).
        maker = cast(Callable[[Any], str | None], ids)
        parts: list[str] = []
        for name, item in bound:
            # `IdMaker._idval_from_function` (l. 968-987): the callable's answer REPLACES the
            # value and is then fed back through `_idval_from_value` -- it is not used raw.
            #
            #     generated_id = self.idfn(val)
            #     if generated_id is not None:
            #         val = generated_id
            #     return self._idval_from_value(val)
            #
            # So the returned string goes through `ascii_escaped`, exactly like a `str` the
            # user parametrized directly, and a returned NON-string is spelled by the same
            # rules as any other value (an `int` -> `str(int)`, an object with a `__name__` ->
            # that name, anything else -> None -> the `<argname><index>` fallback).
            #
            # rustest used `str(generated)` verbatim, which is identical for printable ASCII
            # and different for everything else -- humanize's `ids=` callables return
            # localised strings, and its five remaining id-pair divergences were all and only
            # this (MECHANISM M10, `conformance/real/humanize.toml`).
            # ...and when the callable's answer has NO spelling of its own,
            # `IdMaker._idval` (l. 1009-1021) falls back to the ORIGINAL value's spelling
            # before it reaches `<argname><index>`: `_idval_from_function` returns None, and
            # the next arm re-asks `_idval_from_value(val)` with `val` still bound to the
            # argvalue. Probed on pytest 8.4.2: `ids=lambda v: [1, 2]` over `[1, 2]` collects
            # `[1]` and `[2]`, not `[value0]`/`[value1]`. Written the short way first, and
            # this differential is what caught it.
            generated = maker(item)
            idval = None if generated is None else _idval_from_value(generated)
            if idval is None:
                idval = _idval_from_value(item)
            parts.append(idval if idval is not None else f"{name}{index}")
        return "-".join(parts)
    return cast(Sequence[str], ids)[index]


def skip_decorator(reason: str | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Skip a test or fixture (decorator form).

    This is the decorator version used as @skip(reason="...") or via @mark.skip.
    For the function version that raises Skipped, see skip() function.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        setattr(func, "__rustest_skip__", reason or "skipped via rustest.skip")
        return func

    return decorator


def _cross_product_cases(
    existing: tuple[dict[str, object], ...],
    new: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Create cross-product of two sets of parametrization cases.

    When multiple @parametrize decorators are applied, this creates the
    cartesian product of all parameter combinations, matching pytest behavior.

    Args:
        existing: Existing parametrization cases from previous decorators
        new: New parametrization cases from current decorator

    Returns:
        Combined cases representing all combinations

    Example:
        existing = [{"id": "a1", "values": {"a": 1}}, {"id": "a2", "values": {"a": 2}}]
        new = [{"id": "b1", "values": {"b": 10}}, {"id": "b2", "values": {"b": 20}}]
        result = [
            {"id": "a1-b1", "values": {"a": 1, "b": 10}},
            {"id": "a1-b2", "values": {"a": 1, "b": 20}},
            {"id": "a2-b1", "values": {"a": 2, "b": 10}},
            {"id": "a2-b2", "values": {"a": 2, "b": 20}},
        ]
    """
    combined: list[dict[str, object]] = []

    for existing_case in existing:
        for new_case in new:
            # Merge the parameter values from both cases
            combined_values = {}
            combined_values.update(existing_case["values"])  # type: ignore[arg-type]
            combined_values.update(new_case["values"])  # type: ignore[arg-type]

            # Combine the IDs with a hyphen separator
            combined_id = f"{existing_case['id']}-{new_case['id']}"

            merged: dict[str, object] = {"id": combined_id, "values": combined_values}
            # Per-parameter marks survive a cross product and **accumulate**: a case built
            # from an xfailed outer value and a slow inner value carries both, which is what
            # pytest produces (each `parametrize` contributes its set's marks to the item).
            marks = [
                *cast("Sequence[Any]", existing_case.get("marks", ())),
                *cast("Sequence[Any]", new_case.get("marks", ())),
            ]
            if marks:
                merged["marks"] = marks
            combined.append(merged)

    return tuple(combined)


def parametrize(
    arg_names: str | Sequence[str] | None = None,
    values: Sequence[Sequence[object] | Mapping[str, object] | ParameterSet] | None = None,
    *,
    argnames: str | Sequence[str] | None = None,
    argvalues: Sequence[Sequence[object] | Mapping[str, object] | ParameterSet] | None = None,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
    indirect: bool | Sequence[str] | str = False,
) -> Callable[[Callable[Q, S]], Callable[Q, S]]:
    """Parametrise a test function.

    Args:
        arg_names: Parameter name(s) as a string or sequence (rustest style)
        values: Parameter values for each test case (rustest style)
        argnames: Parameter name(s) (pytest style, alias for ``arg_names``). pytest's own
            signature is ``parametrize(argnames, argvalues, ...)``
            (`_pytest/python.py::Metafunc.parametrize` l. 1163-1167), and a suite that spells
            **either** parameter as a keyword is spelling pytest's name, not rustest's.
            The ``argvalues`` half of that pair was already accepted; this is the other half,
            and its absence cost the whole of FastAPI in the Task 1b sweep -- three of its
            modules write ``@pytest.mark.parametrize(argnames="path,expected", ...)``, each
            raised ``TypeError`` at import, and a collection error aborts the session
            (MECHANISM M8, 3 289 tests lost to one missing alias).
        argvalues: Parameter values for each test case (pytest style, alias for values)
        ids: Test IDs - either a list of strings or a callable
        indirect: Which parameters are routed **through a fixture of the same name**
            instead of being handed to the test directly:
            - False (default): every parameter is a direct value
            - True: every parameter is routed
            - ["param1", "param2"]: only those parameters are routed

            A routed parameter's value reaches the fixture as ``request.param``; what the
            test receives is the fixture's return value. Ids are still generated from the
            *parameter*, so the node id is unchanged by making a name indirect.

            This is pytest's meaning (`_pytest/python.py::Metafunc._resolve_args_directness`,
            l. 1417-1454). **It is not what rustest's `indirect=` used to mean** — before
            Phase 4 the value was read as *the name of a fixture to resolve*, a
            rustest-only feature that no pytest suite could use and that cost Apex Member
            Designer 120 of the 129 failures in its `tests/test_startup` subtree.

            Note that a **string is a Sequence**, so `indirect="data"` is iterated
            character by character exactly as it is in pytest, and fails with
            ``indirect fixture 'd' doesn't exist``. Pass ``["data"]`` or ``True``.

            Example:
                @fixture
                def doubled(request):
                    return request.param * 2

                @parametrize("doubled", [3, 5], indirect=True)
                def test_example(doubled):
                    assert doubled in (6, 10)
    """
    # Support both the rustest spellings and pytest's. Positional use is unaffected: the
    # first two parameters keep their rustest names and their positions, so
    # `parametrize("a,b", values)` and `parametrize(argnames="a,b", argvalues=values)` are
    # the same call. The keyword wins over the positional when both are given, which cannot
    # happen from a pytest suite and is a caller error either way.
    actual_names = argnames if argnames is not None else arg_names
    actual_values = argvalues if argvalues is not None else values
    if actual_names is None:
        msg = "parametrize() requires either 'arg_names' or 'argnames' parameter"
        raise TypeError(msg)
    if actual_values is None:
        msg = "parametrize() requires either 'values' or 'argvalues' parameter"
        raise TypeError(msg)

    # `ParameterSet._parse_parametrize_args` (l. 165-177): a **str** `argnames` naming
    # exactly one parameter is the only shape that wraps each argvalue as a single
    # value. A sequence -- even `("x",)` -- never does. See `_build_cases`.
    normalized_names = _normalize_arg_names(actual_names)
    force_tuple = isinstance(actual_names, str) and len(normalized_names) == 1

    def decorator(func: Callable[Q, S]) -> Callable[Q, S]:
        # Validated inside the decorator so the failure can name the function, as pytest's
        # does. pytest reports it from `Metafunc.parametrize` during collection; rustest
        # reports it at decoration, i.e. at import, and both are a collection error with
        # exit 2 (measured on both runners).
        normalized_indirect = _normalize_indirect(
            indirect, normalized_names, getattr(func, "__name__", "<unknown>")
        )
        new_cases = _build_cases(normalized_names, actual_values, ids, force_tuple=force_tuple)

        # Check if there are already parametrizations from previous decorators
        existing_cases = getattr(func, "__rustest_parametrization__", None)

        if existing_cases:
            # Create cross-product of existing and new cases
            combined_cases = _cross_product_cases(existing_cases, new_cases)
            setattr(func, "__rustest_parametrization__", combined_cases)
        else:
            # First parametrize decorator
            setattr(func, "__rustest_parametrization__", new_cases)

        # Handle indirect params - merge with existing
        if normalized_indirect:
            existing_indirect = getattr(func, "__rustest_parametrization_indirect__", [])
            combined_indirect = list(existing_indirect) + normalized_indirect
            setattr(func, "__rustest_parametrization_indirect__", combined_indirect)

        return func

    return decorator


def _normalize_arg_names(arg_names: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(arg_names, str):
        parts = [part.strip() for part in arg_names.split(",") if part.strip()]
        if not parts:
            msg = "parametrize() expected at least one argument name"
            raise ValueError(msg)
        return tuple(parts)
    return tuple(arg_names)


def _normalize_indirect(
    indirect: bool | Sequence[str] | str,
    param_names: tuple[str, ...],
    func_name: str,
) -> list[str]:
    """Which parametrized names are routed through a same-named fixture.

    Port of `_pytest/python.py::Metafunc._resolve_args_directness` (pytest 8.4.2,
    l. 1417-1454), including the two shapes that are easy to get wrong and were **measured**
    on the oracle before being written here:

    * a ``bool`` decides for *every* name at once — ``indirect=True`` routes all of them;
    * anything else is iterated as a ``Sequence``, and **a ``str`` is a Sequence**. So
      ``indirect="doubled"`` is not the one-name shorthand rustest used to accept: pytest
      iterates the characters and fails with ``indirect fixture 'd' doesn't exist``. Probed:
      ``pytest -q`` on that shape reports exactly that, one collection error, exit 2.

    ``bool`` is checked before ``Sequence`` because it has to be — the order is pytest's and
    reversing it would make ``True`` an unindexable Sequence.
    """
    if isinstance(indirect, bool):
        return list(param_names) if indirect else []
    # The annotation says `Sequence[str] | str`, so the type checker calls this branch
    # unreachable; it is not, because `indirect` is user input and pytest's own `else:` is
    # the message a caller who passed `indirect=3` gets.
    if not isinstance(indirect, Sequence):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = (
            f"In {func_name}: expected Sequence or boolean"
            + f" for indirect, got {type(indirect).__name__}"  # pyright: ignore[reportUnreachable]
        )
        raise ValueError(msg)  # pyright: ignore[reportUnreachable]
    routed: list[str] = []
    for arg in indirect:
        if arg not in param_names:
            raise ValueError(f"In {func_name}: indirect fixture '{arg}' doesn't exist")
        routed.append(arg)
    return routed


def _extract_from(case: object, force_tuple: bool) -> Any:
    """The values *case* contributes -- port of `ParameterSet.extract_from`.

    `_pytest/mark/structures.py` l. 137-161, all three arms:

    * an existing ``ParameterSet`` (i.e. ``pytest.param(...)``) is returned as it stands,
      so its own ``values`` tuple is authoritative;
    * with ``force_tuple`` the whole object becomes **one** value -- ``cls.param(x)``, whose
      ``values`` is ``(x,)``;
    * otherwise the object **is** the values sequence -- ``cls(parameterset, marks=[],
      id=None)``, taken verbatim.

    Returning the raw object in the last arm rather than ``tuple(...)``-ing it is
    deliberate and observable: pytest calls ``len()`` on it and ``zip()``s it, so a value
    that is neither sized nor iterable raises the same ``TypeError`` here that it does
    there, rather than being quietly repackaged into something that "works".
    """
    if isinstance(case, ParameterSet):
        return case.values
    if force_tuple:
        return (case,)
    return case


def _build_cases(
    names: tuple[str, ...],
    values: Sequence[Sequence[object] | Mapping[str, object] | ParameterSet],
    ids: Sequence[str] | Callable[[Any], str | None] | None,
    *,
    force_tuple: bool,
) -> tuple[dict[str, object], ...]:
    """Bind each argvalue to *names* -- port of `ParameterSet._for_parametrize`.

    `_pytest/mark/structures.py` l. 188-227.  **``force_tuple`` is the whole mechanism**,
    and it is decided by the caller (:func:`parametrize`) because pytest decides it from the
    *spelling* of ``argnames``, which is information this function no longer has:
    ``_parse_parametrize_args`` (l. 165-177) sets it to ``len(argnames) == 1`` **only when
    ``argnames`` is a ``str``**, and to ``False`` for any sequence -- so ``("x",)`` and
    ``"x"`` are not the same declaration.

    What it fixes (MECHANISM M2 of the Task 1b sweep, and that sweep's priority item because
    it is *usually silent*): rustest used to decide unpacking by comparing lengths, so a
    **length-1 sequence under a single argname** -- ``@parametrize("value", [[42], [7]])`` --
    was unpacked, and the test received ``42`` where pytest hands it ``[42]``.  The test
    still runs, and usually still passes, having tested something other than what its author
    wrote.  It announced itself in four suites only because the wrong value happened to be
    un-iterable (Member Designer's ``LineString``), un-``len``-able (attrs), or unequal to
    the assertion (werkzeug's ``test_range_validates_ranges``, whose three length-1 sets
    failed while its one length-2 set passed -- four predictions, four hits); in attrs'
    ``test_setattr`` it changed nothing but the **node id**, both cases still passing.

    Two consequences of porting it exactly that look like regressions and are not:

    * ``@parametrize(["x"], [[1, 2]])`` is now an **error** ("the number of names (1) must be
      equal to the number of values (2)"), because a sequence ``argnames`` does not force a
      tuple. That is pytest's answer to the same input.
    * a ``Mapping`` argvalue no longer gets a name-keyed lookup. rustest used to bind
      ``@parametrize("a,b", [{"a": 1, "b": 2}])`` as ``a=1, b=2``; pytest binds the dict's
      **keys** positionally (``len({"a": 1, "b": 2}) == 2``, and ``zip`` over a dict yields
      its keys), i.e. ``a="a", b="b"``. The old behaviour was a rustest-only reading of a
      shape pytest already accepts and reads differently -- the same silent-wrong-value class
      M2 belongs to. Nothing in this repo, its docs or the seventeen-suite corpus uses it. A
      single-name mapping -- ``@parametrize("value", [{"a": 1}])`` -- is unaffected:
      ``force_tuple`` makes the dict one value, exactly as before.
    """
    case_payloads: list[dict[str, object]] = []

    # Handle callable ids (e.g., ids=str)
    ids_is_callable = callable(ids)

    if ids is not None and not ids_is_callable:
        if len(ids) != len(values):
            msg = "ids must match the number of value sets"
            raise ValueError(msg)

    for index, case in enumerate(values):
        param_set_id = case.id if isinstance(case, ParameterSet) else None
        case_values = _extract_from(case, force_tuple)

        # `_for_parametrize` l. 201-217: every parameter set must carry exactly as many
        # values as there are names, and pytest reports the mismatch with `fail(...,
        # pytrace=False)` -- a collection error naming both counts and both lists.
        # Reproduced message-for-message minus the nodeid prefix, which rustest reports
        # separately (this raises at decoration, i.e. at import, where the traceback already
        # names the function).
        if len(case_values) != len(names):
            raise ValueError(
                'in "parametrize" the number of names '
                + f"({len(names)}):\n  {list(names)}\n"
                + f"must be equal to the number of values ({len(case_values)}):\n"
                + f"  {case_values}"
            )
        data: dict[str, Any] = dict(zip(names, case_values))

        # `zip(parameterset.values, self.argnames)`: pytest builds the id from the *bound*
        # pairs, in argname order, which is what `data` already holds.
        case_id = _resolve_case_id(
            param_set_id=param_set_id,
            ids=ids,
            ids_is_callable=ids_is_callable,
            value=case_values,
            index=index,
            bound=[(name, data[name]) for name in names],
        )

        payload: dict[str, object] = {"id": case_id, "values": data}
        if isinstance(case, ParameterSet) and case.marks:
            payload["marks"] = _mark_payloads(case.marks)
        case_payloads.append(payload)
    return tuple(case_payloads)


class MarkDecorator:
    """A decorator for applying a mark to a test function."""

    def __init__(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        super().__init__()
        self.name = name
        self.args = args
        self.kwargs = kwargs

    def __call__(self, func: TFunc) -> TFunc:
        """Apply this mark to the given function."""
        # Get existing marks or create a new list
        existing_marks: list[dict[str, Any]] = getattr(func, "__rustest_marks__", [])

        # Add this mark to the list
        mark_data = {
            "name": self.name,
            "args": self._normalize_args(func),
            "kwargs": self.kwargs,
        }
        existing_marks.append(mark_data)

        # Store the marks list on the function
        setattr(func, "__rustest_marks__", existing_marks)
        return func

    def _normalize_args(self, target: Callable[..., Any]) -> tuple[Any, ...]:
        if self.name != "skipif" or not self.args:
            return self.args

        evaluated = _evaluate_skipif_condition(self.args[0], target)
        return (evaluated, *self.args[1:])

    def __repr__(self) -> str:
        return f"Mark({self.name!r}, {self.args!r}, {self.kwargs!r})"


class AsyncioMarkDecorator(MarkDecorator):
    """``mark.asyncio``'s decorator, which also reaches a decorated class's async methods.

    Identical to :class:`MarkDecorator` except for the class branch: v1 propagated the mark
    onto every coroutine method so a method read *without* its owner still carries
    ``loop_scope``, and several shipped tests pin that.  It is a :class:`MarkDecorator`
    subclass rather than a closure precisely so that the object ``mark.asyncio(...)``
    returns is **also a mark value** — ``pytestmark = pytest.mark.asyncio(loop_scope="x")``
    reads ``.name``/``.args``/``.kwargs`` off it, and a closure answers none of them.
    """

    def __call__(self, func: TFunc) -> TFunc:
        marked = super().__call__(func)
        if not inspect.isclass(marked):
            return marked
        for name, method in inspect.getmembers(marked, predicate=inspect.iscoroutinefunction):
            marked_method = MarkDecorator(self.name, self.args, self.kwargs)(method)
            setattr(marked, name, marked_method)
        return cast(TFunc, marked)


class SkipMarkDecorator(MarkDecorator):
    """``pytest.mark.skip``'s decorator, which is **also** a legal ``pytestmark`` value.

    The compat surface could not delegate ``skip`` to the native ``mark.skip``, because the
    v1 Rust collector read skips from the ``__rustest_skip__`` attribute alone (the deleted
    `src/discovery.rs::collect_tests`) and the native mark only records a dict in
    ``__rustest_marks__``. Routing it to :func:`skip_decorator` instead solved that and
    created a second problem, found in Phase 4 Task 1's review: the *called* form
    ``pytest.mark.skip(reason="...")`` returned that function's inner **closure**, which
    answers no ``.name``/``.args``/``.kwargs``, so ``pytestmark = pytest.mark.skip(reason=…)``
    — matplotlib's shape — refused the whole module with ``malformed pytestmark entry``.

    Same shape as :class:`AsyncioMarkDecorator`: a real ``MarkDecorator`` (so it is a mark
    value) whose ``__call__`` writes ``__rustest_skip__`` *instead of*
    ``MarkDecorator.__call__``'s ``__rustest_marks__`` entry.

    That attribute began as a v1 requirement and is **still load-bearing**, which is why it
    did not leave with the engine: ``_worker::_mark_specs`` reads both sources, so this is
    now simply which of the two channels a compat ``skip`` decoration uses — writing both
    would report the same skip twice. Any future removal has to move the write, not delete
    it.
    """

    def __call__(self, func: TFunc) -> TFunc:
        # `__rustest_skip__` **only** -- deliberately not `MarkDecorator.__call__`'s
        # `__rustest_marks__` entry as well. `_worker::_mark_specs` reads both sources and
        # would then report the same skip twice. The object is still a legal `pytestmark`
        # *value* because it is a `MarkDecorator` and answers `.name`/`.args`/`.kwargs`;
        # what changes here is only what decorating writes.
        reason = self.kwargs.get("reason")
        setattr(func, "__rustest_skip__", reason or "skipped via rustest.skip")
        return func


def _mark_decoration_target(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Any, Any] | None:
    """Is this call a *decoration*, or a decorator-*factory* call?

    A verbatim port of pytest's own rule, ``_pytest/mark/structures.py::MarkDecorator.__call__``
    (pytest 8.4.2)::

        if args and not kwargs:
            func = args[0]
            is_class = inspect.isclass(func)
            unwrapped_func = func
            if isinstance(func, (staticmethod, classmethod)):
                unwrapped_func = func.__func__
            if len(args) == 1 and (istestfunc(unwrapped_func) or is_class):
                store_mark(unwrapped_func, self.mark, stacklevel=3)
                return func
        return self.with_args(*args, **kwargs)

    with ``istestfunc(func) = callable(func) and getattr(func, "__name__", "<lambda>") !=
    "<lambda>"``.

    Returns ``(unwrapped, original)`` for a decoration — the mark is stored on *unwrapped*
    but *original* is what the decorator expression must evaluate to, so a ``staticmethod``
    keeps its descriptor — or ``None`` for a factory call.

    Two consequences are deliberate, both confirmed by running pytest 8.4.2:

    * a **lambda** positional is *not* a decoration, so ``@mark.slow(lambda: 1)`` is a
      factory call carrying the lambda as a condition and the test collects and passes;
    * a **named callable** positional *is* a decoration even when the user meant it as a
      condition, so ``@mark.xfail(some_named_function)`` marks and returns that function and
      then calls it with the test — a collection ``TypeError``. That is pytest's behaviour
      and rustest reproduces it rather than diverging in the "helpful" direction.
    """
    if not args or kwargs or len(args) != 1:
        return None
    original: Any = args[0]
    # `is_class` is computed on the ORIGINAL and before unwrapping, as pytest does.
    is_class = inspect.isclass(original)
    unwrapped: Any = original
    if isinstance(original, (staticmethod, classmethod)):
        unwrapped = cast(Any, original).__func__
    is_testfunc = callable(unwrapped) and getattr(unwrapped, "__name__", "<lambda>") != "<lambda>"
    if is_testfunc or is_class:
        return unwrapped, cast(Any, original)
    return None


class BareOrFactoryMark:
    """One ``mark.<name>`` surface that is *both* a decorator and a decorator factory.

    pytest's ``pytest.mark.xfail`` is a single ``MarkDecorator`` object whose ``__call__``
    decides, per call, whether it was used bare (``@pytest.mark.xfail``) or as a factory
    (``@pytest.mark.xfail(reason=...)``). rustest used to model the standard marks as plain
    *methods* instead, which cannot make that distinction: the bare form applied the method
    itself, so the test function arrived as ``reason``/``condition`` and the module attribute
    became a closure (#136) or a :class:`MarkDecorator` (#137) — in the latter case the test
    silently disappeared from collection under both engines.

    ``name``/``args``/``kwargs`` are exposed because the *uncalled* object is a legitimate
    mark in its own right: ``pytestmark = pytest.mark.xfail`` puts it straight into the list
    that ``_worker::_spec_from_pytestmark`` reads, and pytest's uncalled ``MarkDecorator``
    answers exactly these three with the same empty values.
    """

    def __init__(
        self,
        name: str,
        factory: Callable[..., Any],
        bare: Callable[[Any], Any] | None = None,
    ) -> None:
        super().__init__()
        self.name = name
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}
        self._factory = factory
        # pytest's bare form stores `Mark(name, (), {})` — empty args *and* empty kwargs.
        # Empty args is what makes a bare `skipif`/`xfail` unconditional in
        # `_worker::_conditions`, which is the behaviour real pytest shows.
        self._bare: Callable[[Any], Any] = bare if bare is not None else MarkDecorator(name, (), {})

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        target = _mark_decoration_target(args, kwargs)
        if target is None:
            return self._factory(*args, **kwargs)
        unwrapped, original = target
        _ = self._bare(unwrapped)
        return original

    def __repr__(self) -> str:
        return f"<mark.{self.name} (bare or factory)>"


if TYPE_CHECKING:
    # Static-only signatures for the three dual-purpose marks.
    #
    # This is **pytest's own pattern**, ported: `_pytest/mark/structures.py` l. 476-557
    # declares `_SkipMarkDecorator`, `_SkipifMarkDecorator`, `_XfailMarkDecorator`,
    # `_ParametrizeMarkDecorator`, `_UsefixturesMarkDecorator` and
    # `_FilterwarningsMarkDecorator` inside `if TYPE_CHECKING:` and then re-declares
    # `MarkGenerator.skipif` / `.xfail` / `.usefixtures` as those types.
    #
    # The reason is exactly the one rustest has: at runtime the attribute must be an object
    # that decides *per call* whether it was used bare or as a factory
    # (:class:`BareOrFactoryMark`), which forces `__call__` to be `(*args: Any, **kwargs:
    # Any) -> Any`.  That signature type-checks every call, including
    # `@mark.skipif(reason=3)`.  Declaring the narrow shape here restores the argument
    # checking without changing a byte of runtime behaviour -- a type checker reads these,
    # the interpreter never sees them.
    #
    # The bare overload is what makes the *uncalled* form type-check too:
    # `@mark.xfail` applied directly to a function returns the function.

    class _SkipifMark(BareOrFactoryMark):
        @overload
        def __call__(self, arg: TFunc, /) -> TFunc: ...
        @overload
        def __call__(
            self,
            condition: bool | str,
            reason: str | None = ...,
            *,
            _kw_reason: str | None = ...,
        ) -> MarkDecorator: ...
        def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

    class _XfailMark(BareOrFactoryMark):
        @overload
        def __call__(self, arg: TFunc, /) -> TFunc: ...
        @overload
        def __call__(
            self,
            condition: bool | str | None = ...,
            *,
            reason: str | None = ...,
            raises: type[BaseException] | tuple[type[BaseException], ...] | None = ...,
            run: bool = ...,
            strict: bool = ...,
        ) -> MarkDecorator: ...
        def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

    class _UsefixturesMark(BareOrFactoryMark):
        @overload
        def __call__(self, arg: TFunc, /) -> TFunc: ...
        @overload
        def __call__(self, *names: str) -> MarkDecorator: ...
        def __call__(self, *args: Any, **kwargs: Any) -> Any: ...

    class _AsyncioMark(BareOrFactoryMark):
        @overload
        def __call__(self, arg: TFunc, /) -> TFunc: ...
        @overload
        def __call__(
            self,
            *,
            loop_scope: str | None = ...,
            scope: str | None = ...,
            timeout: float | None = ...,
        ) -> MarkDecorator: ...
        def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


class MarkGenerator:
    """Namespace for dynamically creating marks like pytest.mark.

    Usage:
        @mark.slow
        @mark.integration
        @mark.timeout(seconds=30)

    Standard marks:
        @mark.skipif(condition, *, reason="...")
        @mark.xfail(condition=None, *, reason=None, raises=None, run=True, strict=False)
        @mark.usefixtures("fixture1", "fixture2")
        @mark.asyncio(loop_scope="function")

    Every one of these may also be used **bare** — ``@mark.xfail`` with no parentheses is
    ordinary pytest and means "apply this mark with its defaults". All four are therefore
    :class:`BareOrFactoryMark` instances rather than methods, because a method cannot tell a
    decoration from a factory call and silently ate the test function when used bare
    (defects #136 and #137).

    ``asyncio`` joined them in Phase 4 for the *other* half of the same defect: a method is
    not a mark **value**, so ``pytestmark = pytest.mark.asyncio`` — four modules of Apex
    Member Designer — refused the whole file with ``malformed pytestmark entry ... <bound
    method MarkGenerator.asyncio ...> is not a mark``, and because pytest's
    ``pytest_runtestloop`` (and `src/engine/execute.rs::stage`) raise ``Interrupted`` before the
    first item, four such files meant *nothing* in that 6 132-test suite ran. Its factory
    now returns an :class:`AsyncioMarkDecorator`, which is a mark value too.
    """

    if TYPE_CHECKING:
        # Declared for the type checker only, exactly as `_pytest/mark/structures.py`
        # l. 551-557 does it. The runtime attributes are the `BareOrFactoryMark` instances
        # `__init__` binds below; these annotations narrow their `__call__` and nothing else.
        skipif: _SkipifMark
        xfail: _XfailMark
        usefixtures: _UsefixturesMark
        asyncio: _AsyncioMark

    def __init__(self) -> None:
        super().__init__()
        # Bound here rather than declared as methods: see BareOrFactoryMark's docstring.
        # Instance attributes win over `__getattr__`, so `mark.xfail` finds these first.
        self.skipif = cast("_SkipifMark", BareOrFactoryMark("skipif", self._skipif))
        self.xfail = cast("_XfailMark", BareOrFactoryMark("xfail", self._xfail))
        # `bare=` is the class-aware decorator, so `@mark.asyncio` on a class still reaches
        # its coroutine methods the way `@mark.asyncio(loop_scope=...)` always has.
        self.asyncio = cast(
            "_AsyncioMark",
            BareOrFactoryMark(
                "asyncio", self._asyncio, bare=AsyncioMarkDecorator("asyncio", (), {})
            ),
        )
        self.usefixtures = cast(
            "_UsefixturesMark", BareOrFactoryMark("usefixtures", self._usefixtures)
        )

    def _asyncio(
        self,
        *,
        loop_scope: str | None = None,
        scope: str | None = None,
        timeout: float | None = None,
    ) -> MarkDecorator:
        """The factory half of ``mark.asyncio`` — see :class:`BareOrFactoryMark`.

        Returns an :class:`AsyncioMarkDecorator`, which is both the decorator this used to
        return and a legal ``pytestmark`` **value**.

        This decorator allows you to write async test functions that will be
        automatically executed in an asyncio event loop. The loop_scope parameter
        controls the scope of the event loop used for execution.

        Args:
            loop_scope: The scope of the event loop. One of:
                - None: fall back to the ``asyncio_default_test_loop_scope`` ini
                  (pytest-asyncio's default for that is ``"function"``)
                - "function": New loop for each test function
                - "class": Shared loop across all test methods in a class
                - "module": Shared loop across all tests in a module
                - "package"/"session": Shared loop across the worker
            scope: pytest-asyncio's **deprecated** spelling of ``loop_scope``
                (`pytest_asyncio/plugin.py::_get_marked_loop_scope` l. 771-777). Accepted so
                a suite written against pytest-asyncio < 1.0 keeps working; passing both is
                an error, raised where the oracle raises it -- at setup, with the oracle's
                message -- rather than here, so the two runners agree on the outcome and the
                exit code.
            timeout: Optional timeout in seconds for the test. If the test takes
                longer than this, it will be cancelled with asyncio.TimeoutError.
                This works correctly with parallel test execution - each test has
                its own independent timeout. Default is None (no timeout).
                Must be a positive number if specified.

        Usage:
            @mark.asyncio
            async def test_async_function():
                result = await some_async_operation()
                assert result == expected

            @mark.asyncio(loop_scope="module")
            async def test_with_module_loop():
                await another_async_operation()

            @mark.asyncio(timeout=5.0)
            async def test_with_timeout():
                # This test will fail if it takes longer than 5 seconds
                await slow_operation()

        Note:
            When loop_scope is not specified (None), rustest automatically detects
            the appropriate loop scope based on your fixture dependencies. If you
            use a session-scoped async fixture, tests will automatically share the
            session loop. This is the recommended default for most use cases.
        """
        # WHAT IS CHECKED HERE, AND WHAT DELIBERATELY IS NOT.
        #
        # Checked: that each scope NAME is one of the five. "package" joins the four v1
        # accepted, because `_pytest/scope.py` has five members and pytest-asyncio validates
        # against all of them (`_validate_scope` l. 237-245).
        #
        # NOT checked: `loop_scope` and `scope` being passed TOGETHER. That is the deprecated
        # alias's one real conflict, and pytest-asyncio raises it from
        # `_get_marked_loop_scope` (l. 771-774) at *setup*, so the run reports a per-test
        # `error` and exits 1. The worker reproduces it there
        # (`_worker.py::_marked_loop_scope`); rejecting it here would move the same
        # complaint to import time, i.e. to a collection error at exit 2, and the corpus
        # differential would diverge on a shape that is otherwise byte-identical.
        #
        # KNOWN DIVERGENCE, and it is the name check above rather than anything below:
        # rejecting a typo'd scope at DECORATION makes it a collection error, so
        # `@mark.asyncio(loop_scope="sesion")` costs the whole file. Measured -- pytest:
        # `1 passed, 1 error`, exit 1 (the healthy sibling still runs); rustest: `1 error`,
        # exit 2 (it does not). Kept because failing at the definition is the better error
        # for a typo and because this signature is shipped public surface with its own tests;
        # recorded in the Phase 3 Task 1 report alongside the unknown-keyword holdout, which
        # has the same root.
        valid_scopes = {"function", "class", "module", "package", "session"}
        if loop_scope is not None and loop_scope not in valid_scopes:
            valid = ", ".join(sorted(valid_scopes))
            msg = f"Invalid loop_scope '{loop_scope}'. Must be one of: {valid}"
            raise ValueError(msg)
        if scope is not None and scope not in valid_scopes:
            valid = ", ".join(sorted(valid_scopes))
            msg = f"Invalid scope '{scope}'. Must be one of: {valid}"
            raise ValueError(msg)

        # Validate timeout
        if timeout is not None:
            # Runtime check for invalid types (e.g., user passes string)
            if not isinstance(timeout, (int, float)):  # pyright: ignore[reportUnnecessaryIsInstance]
                msg = f"timeout must be a number, got {type(timeout).__name__}"
                raise TypeError(msg)
            if timeout <= 0:
                msg = f"timeout must be positive, got {timeout}"
                raise ValueError(msg)

        # Only include loop_scope in kwargs if explicitly specified — that is what lets the
        # Rust side's smart detection run when the user did not pin a scope.
        mark_kwargs: dict[str, Any] = {}
        if loop_scope is not None:
            mark_kwargs["loop_scope"] = loop_scope
        if scope is not None:
            mark_kwargs["scope"] = scope
        if timeout is not None:
            mark_kwargs["timeout"] = timeout
        return AsyncioMarkDecorator("asyncio", (), mark_kwargs)

    def _skipif(
        self,
        condition: bool | str,
        reason: str | None = None,
        *,
        _kw_reason: str | None = None,
    ) -> MarkDecorator:
        """The factory half of ``mark.skipif`` — see :class:`BareOrFactoryMark`.

        Args:
            condition: Boolean or string condition to evaluate
            reason: Explanation for why the test is skipped (positional or keyword)

        Usage:
            # Both forms are supported (pytest compatibility):
            @mark.skipif(sys.platform == "win32", reason="Not supported on Windows")
            @mark.skipif(sys.platform == "win32", "Not supported on Windows")
            def test_unix_only():
                pass

            # And bare, which pytest treats as an *unconditional* skip
            # (_pytest/skipping.py::evaluate_skip_marks l. 177-179):
            @mark.skipif
            def test_never_runs():
                pass
        """
        # Support both positional and keyword-only 'reason' for pytest compatibility
        # Some older pytest code uses: skipif(condition, reason) with positional
        # Modern pytest uses: skipif(condition, reason="...") with keyword-only
        actual_reason = _kw_reason if _kw_reason is not None else reason
        return MarkDecorator("skipif", (condition,), {"reason": actual_reason})

    def _xfail(
        self,
        condition: bool | str | None = None,
        *,
        reason: str | None = None,
        raises: type[BaseException] | tuple[type[BaseException], ...] | None = None,
        run: bool = True,
        strict: bool = False,
    ) -> MarkDecorator:
        """The factory half of ``mark.xfail`` — see :class:`BareOrFactoryMark`.

        Args:
            condition: Optional condition - if False, mark is ignored
            reason: Explanation for why the test is expected to fail
            raises: Expected exception type(s)
            run: Whether to run the test (False means skip it)
            strict: If True, passing test will fail the suite

        Usage:
            @mark.xfail(reason="Known bug in backend")
            def test_known_bug():
                assert False

            @mark.xfail(sys.platform == "win32", reason="Not implemented on Windows")
            def test_feature():
                pass

            # And bare, which pytest treats as an unconditional xfail:
            @mark.xfail
            def test_known_broken():
                assert False
        """
        kwargs = {
            "reason": reason,
            "raises": raises,
            "run": run,
            "strict": strict,
        }
        args = () if condition is None else (condition,)
        return MarkDecorator("xfail", args, kwargs)

    def _usefixtures(self, *names: str) -> MarkDecorator:
        """The factory half of ``mark.usefixtures`` — see :class:`BareOrFactoryMark`.

        Args:
            *names: Names of fixtures to use

        Usage:
            @mark.usefixtures("setup_db", "cleanup")
            def test_with_fixtures():
                pass

        Bare ``@mark.usefixtures`` names no fixtures and so has no effect, which is what
        pytest does with it (it warns and runs the test). Before the bare form was handled
        it returned a ``MarkDecorator`` in the test's place and the test vanished.
        """
        return MarkDecorator("usefixtures", names, {})

    def __getattr__(self, name: str) -> Any:
        """Create a mark decorator for the given name."""
        # Return a callable that can be used as @mark.name or @mark.name(args)
        if name == "parametrize":
            return self._create_parametrize_mark()
        return self._create_mark(name)

    def _create_mark(self, name: str) -> BareOrFactoryMark:
        """A custom mark usable as ``@mark.name`` or ``@mark.name(args)``.

        Custom marks always handled the bare form; what changed with #136/#137 is that the
        discrimination is now pytest's exact rule rather than an approximation of it. The
        old test — ``callable(args[0]) and hasattr(args[0], "__name__")`` — accepted a
        **lambda** (whose ``__name__`` is ``"<lambda>"``), so ``@mark.slow(lambda: 1)``
        decorated the lambda and then called it with the test function instead of storing it
        as a mark argument the way pytest does. It also missed ``staticmethod``/
        ``classmethod`` unwrapping and bare classes without ``__name__`` lookups.
        """
        return BareOrFactoryMark(name, lambda *args, **kwargs: MarkDecorator(name, args, kwargs))

    def _create_parametrize_mark(self) -> Callable[..., Any]:
        """Create a decorator matching top-level parametrize behaviour."""

        def _parametrize_mark(*args: Any, **kwargs: Any) -> Any:
            if len(args) == 1 and callable(args[0]) and not kwargs:
                msg = "@mark.parametrize must be called with arguments"
                raise TypeError(msg)
            return parametrize(*args, **kwargs)

        return _parametrize_mark


# Create a singleton instance
mark = MarkGenerator()


def _evaluate_skipif_condition(condition: Any, target: Callable[..., Any]) -> Any:
    if not isinstance(condition, str):
        return condition

    # Recreate pytest's evaluation order: use the function's globals first and fall
    # back to the module where the function is defined. This lets expressions reuse
    # constants or helper flags defined next to the tests.
    globals_ns = getattr(target, "__globals__", None)
    if globals_ns is None:
        module_name = getattr(target, "__module__", None)
        if module_name is not None:
            module = sys.modules.get(module_name)
            if module is not None:
                globals_ns = vars(module)
    if globals_ns is None:
        globals_ns = {}

    locals_ns: dict[str, Any] = {}
    if inspect.isclass(target):
        locals_ns = dict(vars(target))

    try:
        return bool(eval(condition, globals_ns, locals_ns))
    except Exception as exc:  # pragma: no cover - defensive
        message = (
            "Failed to evaluate skipif condition "
            + f"'{condition}': {exc}. Fix the expression or guard it with try/except."
        )
        raise RuntimeError(message) from exc


_NO_EXC_INFO: tuple[type[BaseException], BaseException, Any] | None = None


def _stringify_exception(exc: BaseException) -> str:
    """Port of `_pytest/_code/code.py::stringify_exception` (pytest 8.4.2, l. 465-490).

    ``match=`` is compared against **this**, not against ``str(exc)``: PEP-678 ``__notes__``
    are part of the searched text, which is why a suite that adds a note and matches on it
    passes under pytest.  (The exception-group branch is not modelled — see
    :class:`RaisesContext`.)
    """
    notes = cast("list[str]", getattr(exc, "__notes__", []))
    return "\n".join([str(exc), *notes])


def _match_pattern(match: Any) -> Any:
    """Port of `_pytest/raises.py::_match_pattern` (l. 314-316).

    A pattern compiled with no flags is reported by its source text, so the failure message
    reads the same whether the user passed ``"x"`` or ``re.compile("x")``.
    """
    import re

    # `_REGEX_NO_FLAGS = re.compile(r"").flags` — pytest l. 69.
    if isinstance(match, re.Pattern):
        pattern = cast("re.Pattern[str]", match)
        if pattern.flags == re.compile(r"").flags:
            return pattern.pattern
    return cast(Any, match)


class ExceptionInfo:
    """Information about an exception caught by :func:`raises`.

    Port of the read surface of `_pytest/_code/code.py::ExceptionInfo` (pytest 8.4.2,
    l. 604-781): :attr:`type`, :attr:`value`, :attr:`tb`, :attr:`typename`,
    :attr:`traceback`, :meth:`exconly`, :meth:`errisinstance`, :meth:`match` and pytest's
    ``__repr__``.  Like pytest's, an instance may be **unfilled** — that is what
    ``RaisesContext.__enter__`` hands to the ``as`` target before the block has run, and it
    is why the accessors are properties with pytest's exact assertion messages rather than
    the plain attributes rustest used to set in ``__init__``.

    :attr:`traceback` is a real :class:`rustest._code.Traceback` as of Phase 4 Task 1c — a
    ``list`` subclass of ``TracebackEntry``, so ``len()``, indexing and iteration all work.
    It used to be an alias for the raw ``types.TracebackType``, on the recorded ground that
    modelling it meant modelling ``TracebackEntry``, ``Source`` and ``Frame`` and that no
    suite then in the corpus read it. The seventeen-suite sweep found one that does
    (MECHANISM M3): werkzeug's ``test_import_string_provides_traceback`` iterates it and
    joins ``str(line)``, and got ``TypeError: 'traceback' object is not iterable``. So the
    three classes were built after all.  It is **settable**, because pytest's own callers
    assign a filtered traceback back onto the info object
    (`_pytest/python.py::importtestmodule` does exactly that) — and that setter is why
    :meth:`__repr__`'s ``tblen`` is ``len(self.traceback)`` rather than a walk of the raw
    ``tb_next`` chain: after a ``cut``/``filter`` the two numbers differ, and pytest reports
    the filtered one.  See :meth:`__repr__`.
    """

    def __init__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        exc_tb: Any = None,
    ) -> None:
        super().__init__()
        self._excinfo: tuple[type[BaseException], BaseException, Any] | None = (
            None if exc_type is None or exc_value is None else (exc_type, exc_value, exc_tb)
        )
        #: Cache for :attr:`traceback`, and the slot its setter writes.
        self._traceback: Any = None

    @classmethod
    def for_later(cls) -> ExceptionInfo:
        """An unfilled instance — pytest's `ExceptionInfo.for_later` (l. 586-589)."""
        return cls()

    def fill_unfilled(self, exc_info: tuple[type[BaseException], BaseException, Any]) -> None:
        """Fill an instance made by :meth:`for_later` (pytest l. 591-594)."""
        assert self._excinfo is None, "ExceptionInfo was already filled"
        self._excinfo = exc_info

    @property
    def type(self) -> type[BaseException]:
        """The exception class."""
        assert self._excinfo is not None, ".type can only be used after the context manager exits"
        return self._excinfo[0]

    @property
    def value(self) -> BaseException:
        """The exception value."""
        assert self._excinfo is not None, ".value can only be used after the context manager exits"
        return self._excinfo[1]

    @property
    def tb(self) -> Any:
        """The exception raw traceback."""
        assert self._excinfo is not None, ".tb can only be used after the context manager exits"
        return self._excinfo[2]

    @property
    def typename(self) -> str:
        """The type name of the exception."""
        # Message built first so the repo's ruff (0.14) and the pre-commit-pinned ruff
        # (0.8) format this identically -- they wrap `assert cond, msg` differently and
        # would otherwise fight over the line (see `test_config_oracle.py` for the same
        # note). Fixed properly by #134 in Phase 4 Task 2.
        unfilled = ".typename can only be used after the context manager exits"
        assert self._excinfo is not None, unfilled
        return self.type.__name__

    @property
    def traceback(self) -> Any:
        """pytest's ``Traceback`` over :attr:`tb` — `_code/code.py::ExceptionInfo.traceback`.

        Built lazily and cached, as pytest's is (``self._traceback``), so repeated reads
        return the same list and an assignment to it survives.
        """
        if self._traceback is None:
            from rustest._code import Traceback

            self._traceback = Traceback(self.tb)
        return self._traceback

    @traceback.setter
    def traceback(self, value: Any) -> None:
        self._traceback = value

    def exconly(self, tryshort: bool = False) -> str:
        """The exception rendered as ``traceback.format_exception_only`` renders it.

        pytest strips a leading ``"AssertionError: "`` under ``tryshort`` only when its
        ``from_exc_info`` classifier armed ``_striptext``, which happens for rewritten
        asserts.  rustest reports those through `_assertion.py` rather than through
        ``ExceptionInfo``, so nothing arms it here and ``tryshort`` is accepted and inert.
        """
        import traceback as _traceback

        _ = tryshort
        return "".join(_traceback.format_exception_only(self.type, self.value)).rstrip()

    def errisinstance(self, exc: type[BaseException] | tuple[type[BaseException], ...]) -> bool:
        """``isinstance(excinfo.value, exc)`` (pytest l. 677-682)."""
        return isinstance(self.value, exc)

    def match(self, regexp: Any) -> bool:
        """Search the stringified exception for *regexp*, or raise ``AssertionError``.

        Port of pytest l. 768-781, message for message: matching is done with
        :func:`re.search` over :func:`_stringify_exception`, and the "did you mean to
        ``re.escape()``" hint fires on an exact-equality miss.
        """
        __tracebackhide__ = True
        import re

        value = _stringify_exception(self.value)
        msg = f"Regex pattern did not match.\n Regex: {regexp!r}\n Input: {value!r}"
        if regexp == value:
            msg += "\n Did you mean to `re.escape()` the regex?"
        assert re.search(regexp, value), msg
        return True

    def __repr__(self) -> str:
        """Port of `_pytest/_code/code.py::ExceptionInfo.__repr__` (l. 639-642).

        ``tblen`` is ``len(self.traceback)`` — the **Traceback**, not a walk of ``tb_next``.
        The distinction is the whole reason :attr:`traceback` has a setter: pytest's own
        callers assign a *filtered* traceback back onto the info object, and after that
        assignment ``repr(excinfo)`` must report the filtered length. Walking the raw
        ``tb_next`` chain reported the original length forever, so a ``cut``/``filter`` was
        invisible in the one place a reader looks to check it took effect.
        """
        if self._excinfo is None:
            return "<ExceptionInfo for raises contextmanager>"
        try:
            rendered = repr(self._excinfo[1])
        except Exception:  # pragma: no cover - saferepr's job in pytest
            rendered = f"<unrepresentable {type(self._excinfo[1]).__name__}>"
        return f"<{type(self).__name__} {rendered} tblen={len(self.traceback)}>"


def _parse_exc(exc: object) -> type[BaseException]:
    """Port of `_pytest/raises.py::AbstractRaises._parse_exc` (l. 437-472), message for message.

    The three wordings are pytest's, including its own "unclear if the Type/ValueError
    distinction is even helpful here" split: a **class** that is not a `BaseException`
    subclass is a ``ValueError``, an **instance** and anything else are ``TypeError``.
    The ``ExceptionGroup`` generic-alias branch is not modelled (see
    :class:`RaisesContext`'s "not modelled" list).
    """
    if isinstance(exc, type) and issubclass(exc, BaseException):
        return exc
    msg = "expected exception must be a BaseException type, not "
    if isinstance(exc, type):
        raise ValueError(msg + f"{exc.__name__!r}")
    if isinstance(exc, BaseException):
        raise TypeError(msg + f"an exception instance ({type(exc).__name__})")
    raise TypeError(msg + repr(type(exc).__name__))


class RaisesContext:
    """Context manager for asserting that code raises a specific exception.

    Port of `_pytest/raises.py::RaisesExc` (pytest 8.4.2, l. 543-732), restricted to the
    surface real suites use.  What it reproduces, each against that file:

    * ``__enter__`` returns an **unfilled** :class:`ExceptionInfo` (l. 694-697), not the
      context object.  Before Phase 4 rustest returned ``self``, so ``.tb``, ``.typename``
      and ``.match()`` were simply missing from the ``as`` target.
    * the type check runs first and a **mismatch propagates the original exception**
      untouched, whatever ``match``/``check`` say (``_just_propagate``, l. 668-672);
    * ``match`` is searched over :func:`_stringify_exception` (l. 496-531);
    * ``check`` is called last, and only if type and match passed (l. 481-493);
    * the three ``DID NOT RAISE`` wordings (l. 707-713).

    **Not modelled:** ``RaisesGroup``/exception-group matching, the ``re.error`` and
    empty-``match`` warnings pytest emits at construction, and the ``_diff_text`` rendering
    pytest uses when ``match`` is a fully escaped ``^...$`` literal.  A failed assertion is
    an ``AssertionError`` here and a ``_pytest.outcomes.Failed`` (a ``BaseException``) in
    pytest; both are reported as ``failed`` with identical text.

    Usage:
        with raises(ValueError):
            int("not a number")

        with raises(ValueError, match="invalid literal"):
            int("not a number")

        with raises((ValueError, TypeError)):
            some_function()

        # Access the caught exception
        with raises(ValueError) as exc_info:
            raise ValueError("oops")
        assert "oops" in str(exc_info.value)
    """

    def __init__(
        self,
        exc_type: type[BaseException] | tuple[type[BaseException], ...] | None = None,
        *,
        match: Any = None,
        check: Callable[[BaseException], bool] | None = None,
    ) -> None:
        super().__init__()
        if isinstance(exc_type, tuple):
            expected = exc_type
        elif exc_type is None:
            expected = ()
        else:
            expected = (exc_type,)
        if not expected and match is None and check is None:
            raise ValueError("You must specify at least one parameter to match on.")
        # Validation happens **at construction**, as pytest's does, so a typo is reported
        # where it was written rather than at the end of the block -- or, for a bad regex,
        # not at all until something raises.
        expected = tuple(_parse_exc(entry) for entry in expected)
        if isinstance(match, str):
            import re as _re

            try:
                _ = _re.compile(match)
            except _re.error as exc:
                # `fail(...)`, i.e. `Failed`, exactly as `AbstractRaises.__init__` does
                # (`_pytest/raises.py` l. 393-400).
                raise Failed(f"Invalid regex pattern provided to 'match': {exc}") from None
        self.expected_exceptions: tuple[type[BaseException], ...] = expected
        self.exc_type = exc_type
        self.match_pattern = match
        self.check = check
        self.excinfo: ExceptionInfo | None = None

    def __repr__(self) -> str:
        """pytest's `RaisesExc.__repr__` shape (l. 677-689), under rustest's class name."""
        parameters: list[str] = []
        if self.expected_exceptions:
            if len(self.expected_exceptions) == 1:
                parameters.append(self.expected_exceptions[0].__name__)
            else:
                names = ", ".join(exc.__name__ for exc in self.expected_exceptions)
                parameters.append(f"({names})")
        if self.match_pattern is not None:
            parameters.append(f"match={_match_pattern(self.match_pattern)!r}")
        if self.check is not None:
            parameters.append(f"check={self.check!r}")
        return f"{type(self).__name__}({', '.join(parameters)})"

    def __enter__(self) -> ExceptionInfo:
        self.excinfo = ExceptionInfo.for_later()
        return self.excinfo

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        __tracebackhide__ = True
        # No exception was raised — pytest's three wordings, `raises.py` l. 707-713.
        if exc_type is None:
            # `Failed`, not `AssertionError`, because pytest's three wordings all come out of
            # `fail()` (`_pytest/raises.py` l. 707-713) and `raises.Exception is
            # fail.Exception`. It matters beyond taxonomy: `Failed` is a `BaseException`, so a
            # test body that wraps the block in `except Exception` cannot swallow the failure.
            expected = self.expected_exceptions
            if not expected:
                raise Failed("DID NOT RAISE any exception")
            if len(expected) > 1:
                raise Failed(f"DID NOT RAISE any of {expected!r}")
            raise Failed(f"DID NOT RAISE {next(iter(expected))!r}")

        assert exc_val is not None, "exc_val must not be None when exc_type is not None"

        # Type first, and a mismatch propagates rather than reporting a mismatch: pytest
        # sets `_just_propagate` in `matches` (l. 668-672) precisely for this.
        if self.expected_exceptions and not issubclass(exc_type, self.expected_exceptions):
            return False

        import re

        if self.match_pattern is not None:
            text = _stringify_exception(exc_val)
            if not re.search(self.match_pattern, text):
                msg = (
                    "Regex pattern did not match.\n"
                    + f" Regex: {_match_pattern(self.match_pattern)!r}\n"
                    + f" Input: {text!r}"
                )
                if _match_pattern(self.match_pattern) == text:
                    msg += "\n Did you mean to `re.escape()` the regex?"
                raise AssertionError(msg)

        if self.check is not None and not self.check(exc_val):
            raise AssertionError(f"check {self.check!r} did not return True")

        if self.excinfo is None:  # pragma: no cover - `raises(...).__exit__` without enter
            self.excinfo = ExceptionInfo.for_later()
        self.excinfo.fill_unfilled((exc_type, exc_val, exc_tb))
        return True

    @property
    def value(self) -> BaseException:
        """Access the caught exception value.

        Kept from before Phase 4 — ``RaisesContext`` is exported API and this is what a
        caller who held on to the *context object* rather than the ``as`` target reads.
        pytest has no analogue (its ``__enter__`` result is the only handle), so the
        pre-existing ``AttributeError`` wording is preserved rather than replaced by
        :class:`ExceptionInfo`'s assertion.
        """
        excinfo = self.excinfo
        if excinfo is None:
            raise AttributeError("No exception was caught")
        try:
            return excinfo.value
        except AssertionError:
            raise AttributeError("No exception was caught") from None

    @property
    def type(self) -> type[BaseException]:
        """Access the caught exception type — see :attr:`value`."""
        excinfo = self.excinfo
        if excinfo is None:
            raise AttributeError("No exception was caught")
        try:
            return excinfo.type
        except AssertionError:
            raise AttributeError("No exception was caught") from None


@overload
def raises(
    expected_exception: type[BaseException] | tuple[type[BaseException], ...] | None = ...,
    *,
    match: Any = ...,
    check: Callable[[BaseException], bool] | None = ...,
) -> RaisesContext: ...


@overload
def raises(
    expected_exception: type[BaseException] | tuple[type[BaseException], ...],
    func: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> ExceptionInfo: ...


def raises(
    expected_exception: type[BaseException] | tuple[type[BaseException], ...] | None = None,
    *args: Any,
    **kwargs: Any,
) -> RaisesContext | ExceptionInfo:
    """Assert that a code block — or a call — raises a specific exception.

    Port of `_pytest/raises.py::raises` (pytest 8.4.2, l. 104-300).  **Two forms**, and the
    presence of a positional ``func`` is what selects between them:

    * ``raises(Exc)`` / ``raises(Exc, match=...)`` / ``raises(match=...)`` returns a
      :class:`RaisesContext` to be used with ``with``;
    * ``raises(Exc, func, *args, **kwargs)`` — pytest's **legacy callable form** — calls
      ``func(*args, **kwargs)`` immediately and returns the :class:`ExceptionInfo`.  Note
      that in this form ``**kwargs`` are forwarded to ``func``: ``raises(E, f, match="x")``
      passes ``match="x"`` to ``f`` (pytest l. 296, and its docstring l. 128-131).  jinja2
      uses this form 41 times, which is why rustest's keyword-only signature cost that
      suite 50 tests.

    Args:
        expected_exception: The expected exception type(s), or ``None`` to match on
            ``match``/``check`` alone.
        *args: Empty for the context-manager form; ``(func, *func_args)`` for the callable
            form.
        **kwargs: ``match``/``check`` for the context-manager form; forwarded to ``func``
            for the callable form.

    Raises:
        AssertionError: If no exception is raised, or the message/check does not match.
        TypeError: On an unknown keyword in the context-manager form, or a non-callable
            ``func``.
        ValueError: On a falsy ``expected_exception`` in the callable form.

    Usage:
        with raises(ValueError):
            int("not a number")

        with raises(ValueError, match="invalid literal"):
            int("not a number")

        excinfo = raises(ValueError, int, "not a number")
        assert "invalid literal" in str(excinfo.value)
    """
    __tracebackhide__ = True

    if not args:
        # pytest lists *every* keyword, not only the offending one (l. 276-280).
        if set(kwargs) - {"match", "check", "expected_exception"}:
            msg = "Unexpected keyword arguments passed to pytest.raises: "
            msg += ", ".join(sorted(kwargs))
            msg += "\nUse context-manager form instead?"
            raise TypeError(msg)
        if expected_exception is None:
            return RaisesContext(**kwargs)
        return RaisesContext(expected_exception, **kwargs)

    if not expected_exception:
        raise ValueError(
            "Expected an exception type or a tuple of exception types, but got "
            + f"`{expected_exception!r}`. Raising exceptions is already understood as "
            + "failing the test, so you don't need any special code to say 'this should "
            + "never raise an exception'."
        )
    func = args[0]
    if not callable(func):
        raise TypeError(f"{func!r} object (type: {type(func)}) must be callable")
    with RaisesContext(expected_exception) as excinfo:
        _ = func(*args[1:], **kwargs)
    try:
        return excinfo
    finally:
        del excinfo


class OutcomeException(BaseException):
    """Port of `_pytest/outcomes.py::OutcomeException` (pytest 8.4.2, l. 17-38).

    **The shared base of every "this is not an ordinary error, it is an outcome" signal**, and
    a ``BaseException`` on purpose. pytest's whole outcome protocol rests on that choice: a
    test body that wraps a call in ``try: ... except Exception:`` — which is ordinary,
    defensive, everywhere — must not be able to swallow the runner's own control flow.

    rustest arrived here in two steps, and the second is the Phase 4 final polish wave.
    Phase 4 Task 1's review moved :class:`Failed` off ``Exception`` after measuring the cost
    (a ``raises`` block that did not raise reported **passed** under rustest and **failed**
    under pytest; the reviewer's three-test repro was pytest 3 failed, rustest 3 passed).
    :class:`Skipped` and :class:`XFailed` were left behind on ``Exception`` with the note that
    "nothing in the corpus turns on a test body's ``except Exception`` swallowing a skip" —
    true of the corpus, and not a property of the class. The same three-line body swallows a
    ``pytest.skip()`` just as happily, and then the test *passes*, which is the worst
    available answer: a skip that silently becomes a green.

    ``msg``/``pytrace`` and the "expected string as 'msg' parameter, got '...' instead.
    Perhaps you meant to use a mark?" ``TypeError`` are pytest's, message for message — that
    guard exists because ``pytest.skip(SomeMark)`` is a real mistake people make and the
    resulting failure is otherwise unreadable. ``__repr__``/``__str__`` return the message
    itself so a report prints the reason, not ``Skipped('reason')``.
    """

    def __init__(self, msg: str | None = None, pytrace: bool = True) -> None:
        # Annotated `str | None`, checked anyway: the argument comes from user test code
        # and `pytest.skip(some_mark)` is precisely the mistake the message names.
        if msg is not None and not isinstance(msg, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            error_msg = (
                "{} expected string as 'msg' parameter, got '{}' instead.\n"
                "Perhaps you meant to use a mark?"
            )
            raise TypeError(error_msg.format(type(self).__name__, type(msg).__name__))
        super().__init__(msg)
        self.msg = msg
        self.pytrace = pytrace

    def __repr__(self) -> str:
        if self.msg is not None:
            return self.msg
        return f"<{type(self).__name__} instance>"

    __str__ = __repr__


class Failed(OutcomeException):
    """Exception raised by fail() to mark a test as failed.

    Port of `_pytest/outcomes.py::Failed` (l. 79-83): ``class Failed(OutcomeException)``, so
    a ``BaseException``. See :class:`OutcomeException` for why that matters.

    The worker catches ``BaseException`` at every test-body boundary
    (:func:`_worker._phases` and friends), so nothing else had to move.

    **pytest's ``__module__ = "builtins"`` hack is deliberately NOT copied** (here or on
    :class:`Skipped`). It is cosmetic for pytest — it shortens the name in a report — and it
    is not cosmetic here: the **v1** engine classifies outcomes by string-matching the
    rendered traceback (`src/execution.rs::is_skip_exception` l. 649-657 looks for
    ``"rustest.decorators.Skipped"`` first), and an exception whose ``__module__`` is
    ``builtins`` renders with no module prefix at all. Taking the hack would silently move v1
    onto its own fallback branch for every skip, to buy a shorter name.
    """


# `_pytest/raises.py` l. 303-311 sets the same alias: what `raises` raises when the block did
# not, so a plugin (or a test) can name it without importing the outcome module.
raises.Exception = Failed  # type: ignore[attr-defined]


def fail(reason: str = "", pytrace: bool = True) -> None:
    """Explicitly fail the current test with the given message.

    This function immediately raises an exception to fail the test,
    similar to pytest.fail(). It's useful for conditional test failures
    where a simple assert is not sufficient.

    Args:
        reason: The failure message to display
        pytrace: If False, report the message alone instead of a traceback, as
                 `_pytest/nodes.py::Node._repr_failure_py` (l. 481-484) does for a
                 ``fail.Exception`` that asked for no traceback. It reaches the report
                 through :attr:`Failed.pytrace`; the flag was accepted and dropped on
                 the floor until the Phase 4 convergence wave, so a module-level
                 ``fail(..., pytrace=False)`` printed the import plumbing it exists to
                 suppress. Currently honoured at **collection** (see
                 ``_worker.py::_module_outcome_error_message``); a failing test *body*
                 still renders its traceback either way.

    Raises:
        Failed: Always raised to fail the test

    Usage:
        def test_validation():
            data = load_data()
            if not is_valid(data):
                fail("Data validation failed")

        def test_conditional():
            if some_condition:
                fail("Condition should not be true")
            assert something_else

        # With detailed message
        def test_complex():
            result = complex_operation()
            if result.status == "error":
                fail(f"Operation failed: {result.error_message}")
    """
    __tracebackhide__ = True
    raise Failed(reason, pytrace=pytrace)


class Skipped(OutcomeException):
    """Exception raised by skip() to dynamically skip a test.

    Port of `_pytest/outcomes.py::Skipped` (pytest 8.4.2, l. 41-61)'s **data** half: the
    three attributes anything downstream reads -- ``msg``, ``allow_module_level`` and the
    private ``_use_item_location`` -- with pytest's own defaults and ordering.

    ``allow_module_level`` is the load-bearing one and it is not decoration.  pytest's
    `_pytest/python.py::importtestmodule` (l. 534-542) catches ``skip.Exception`` around the
    module import and branches on exactly this attribute: set, the module is **skipped**
    (nothing collected, no error); unset, it is a collection error with a message telling
    the author to pass the flag.  rustest carried the flag on ``skip()``'s signature and
    then dropped it on the floor, so ``pytest.skip(..., allow_module_level=True)`` at module
    scope was reported as an unhandled exception during collection -- and since a collection
    error aborts the session, Pillow's six such modules cost all 4 036 of its tests.

    **A ``BaseException`` as of the Phase 4 final polish wave**, via
    :class:`OutcomeException`. It was an ``Exception`` on the recorded ground that "nothing in
    the corpus turns on a test body's ``except Exception`` swallowing a skip" — which was a
    statement about the corpus, not about the class. ``try: ... except Exception: pass``
    around a call that skips turned the skip into a **pass**, and pytest's own answer to that
    hazard is precisely this base class. pytest's ``__module__ = "builtins"`` hack (l. 43-44)
    is **not** copied — see :class:`Failed` for why, which is v1's string-matched skip
    detection in `src/execution.rs`.
    """

    def __init__(
        self,
        msg: str | None = None,
        pytrace: bool = True,
        allow_module_level: bool = False,
        *,
        _use_item_location: bool = False,
    ) -> None:
        super().__init__(msg=msg, pytrace=pytrace)
        self.allow_module_level = allow_module_level
        # If true, the skip location is reported as the item's location, instead of the
        # place that raises the exception/calls skip().
        self._use_item_location = _use_item_location


def skip(reason: str = "", allow_module_level: bool = False) -> None:
    """Skip the current test or module dynamically.

    This function raises an exception to skip the test at runtime,
    similar to pytest.skip(). It's useful for conditional test skipping
    based on runtime conditions.

    Args:
        reason: The reason why the test is being skipped
        allow_module_level: If True, this call is legal at *module* scope and skips the
                           whole module: nothing in it is collected, and it is reported as
                           one skip with no node id -- pytest's own answer
                           (`_pytest/python.py::importtestmodule` l. 534-542). Without it,
                           a module-scope call is a collection error carrying pytest's
                           "pass `allow_module_level=True`" message.

    Raises:
        Skipped: Always raised to skip the test

    Usage:
        def test_requires_linux():
            import sys
            if sys.platform != "linux":
                skip("Only runs on Linux")
            # Test code here

        def test_conditional_skip():
            import subprocess
            result = subprocess.run(["which", "docker"], capture_output=True)
            if result.returncode != 0:
                skip("Docker not available")
            # Docker tests here
    """
    __tracebackhide__ = True
    raise Skipped(reason, allow_module_level=allow_module_level)


class XFailed(Failed):
    """Exception raised by xfail() to mark a test as expected to fail.

    ``class XFailed(Failed)`` is pytest's declaration (`_pytest/outcomes.py` l. 186-187), and
    rustest's derived straight from ``Exception`` until the Phase 4 final polish wave. Two
    consequences, both real:

    * it is now a ``BaseException``, so ``except Exception`` in a test body cannot turn a
      dynamic ``pytest.xfail()`` into a pass — the same hazard :class:`OutcomeException`
      describes;
    * the **order** of the worker's classification tables becomes load-bearing rather than
      accidentally irrelevant. ``_worker`` checks ``XFAILED_EXCEPTIONS`` *before*
      ``FAILED_EXCEPTIONS`` and its comment already said so ("relying on that accident would
      break the day the hierarchy is aligned"). This is that day, and the order was already
      right; :func:`_worker.report_for_phase` routes by ``isinstance`` in that order and
      is re-verified by the outcome-classifier tests.
    """


def xfail(reason: str = "") -> None:
    """Mark the current test as expected to fail dynamically.

    This function raises an exception to mark the test as an expected failure
    at runtime, similar to pytest.xfail(). The test will still run but its
    failure won't count against the test suite.

    Args:
        reason: The reason why the test is expected to fail

    Raises:
        XFailed: Always raised to mark the test as xfail

    Usage:
        def test_known_bug():
            import sys
            if sys.version_info < (3, 11):
                xfail("Known bug in Python < 3.11")
            # Test code that fails on older Python

        def test_experimental_feature():
            if not feature_complete():
                xfail("Feature not yet complete")
            # Test code here
    """
    __tracebackhide__ = True
    raise XFailed(reason)


# Each outcome helper carries the class it raises, so `except pytest.fail.Exception` names it
# without importing an internal module. Port of `_pytest/outcomes.py::_with_exception`
# (pytest 8.4.2, l. 92-98) and its three decorated uses -- `@_with_exception(Skipped)` on
# `skip` (l. 125), `@_with_exception(Failed)` on `fail` (l. 162), `@_with_exception(XFailed)`
# on `xfail` (l. 184). pytest builds it with a decorator and a `_WithException` Protocol
# purely so mypy accepts an attribute on a function; three assignments are the same object
# graph, and `rustest.compat.pytest` re-exports these very functions, so setting it here sets
# it on `pytest.fail` too.
#
# **The blast radius of not having it is total.** `psutil/tests/__init__.py` does
# `except pytest.fail.Exception:` at *import* time inside a helper's body -- reaching the
# attribute is enough, the exception never has to be raised -- so every one of psutil's 19
# test modules failed to import, collection aborted, and all 713 tests were lost to three
# missing assignments (Task 1b sweep, §5 M6).
#
# `exit.Exception` is set on `rustest.compat.pytest.exit`, next to that function, because
# `Exit` is defined there; the four together are pytest's complete set.
fail.Exception = Failed  # type: ignore[attr-defined]
skip.Exception = Skipped  # type: ignore[attr-defined]
xfail.Exception = XFailed  # type: ignore[attr-defined]
