"""User facing decorators mirroring the most common pytest helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import inspect
import sys
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, overload, cast

P = ParamSpec("P")
R = TypeVar("R")
Q = ParamSpec("Q")
S = TypeVar("S")
TFunc = TypeVar("TFunc", bound=Callable[..., Any])

# Valid fixture scopes
VALID_SCOPES = frozenset(["function", "class", "module", "package", "session"])


class ParameterSet:
    """Represents a single parameter set for pytest.param().

    This class holds the values for a parametrized test case along with
    optional id and marks metadata.
    """

    def __init__(self, values: tuple[Any, ...], id: str | None = None, marks: Any = None):
        super().__init__()
        self.values = values
        self.id = id
        self.marks = marks  # Currently not used, but stored for future support

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
            param_cases = _build_fixture_params(params, ids)
            setattr(f, "__rustest_fixture_params__", param_cases)

        return f

    # Support both @fixture and @fixture(scope="...")
    if func is not None:
        return decorator(func)
    return decorator


def _build_fixture_params(
    params: Sequence[Any],
    ids: Sequence[str] | Callable[[Any], str | None] | None,
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

        case_id = _resolve_case_id(
            param_set_id=param_set_id,
            ids=ids,
            ids_is_callable=ids_is_callable,
            value=actual_value,
            index=index,
        )

        cases.append({"id": case_id, "value": actual_value})

    return cases


def _generate_param_id(value: Any, index: int) -> str:
    """Generate a readable ID for a parameter value.

    Args:
        value: The parameter value
        index: The index of the parameter

    Returns:
        A string ID for the parameter
    """
    # Try to generate a readable ID from the value
    if value is None:
        return "None"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Truncate long strings
        if len(value) <= 20:
            return value
        return f"{value[:17]}..."
    if isinstance(value, (list, tuple)):
        seq_value = cast(list[Any] | tuple[Any, ...], value)
        if len(seq_value) == 0:
            return "empty"
        # Try to create a short representation
        items = [_generate_param_id(v, 0) for v in seq_value[:3]]
        result = "-".join(items)
        if len(seq_value) > 3:
            result += f"-...({len(seq_value)})"
        return result
    if isinstance(value, dict):
        dict_value = cast(dict[Any, Any], value)
        if len(dict_value) == 0:
            return "empty_dict"
        return f"dict({len(dict_value)})"

    # Fallback to index-based ID
    return f"param{index}"


def _resolve_case_id(
    *,
    param_set_id: str | None,
    ids: Sequence[str] | Callable[[Any], str | None] | None,
    ids_is_callable: bool,
    value: Any,
    index: int,
) -> str:
    """Resolve the ID for a parametrization case.

    Priority: ParameterSet.id > callable ids > explicit ids list > auto-generated.
    """
    if param_set_id is not None:
        return param_set_id
    if ids is None:
        return _generate_param_id(value, index)
    if ids_is_callable:
        generated_id = cast(Callable[[Any], str | None], ids)(value)
        return str(generated_id) if generated_id is not None else _generate_param_id(value, index)
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

            combined.append({"id": combined_id, "values": combined_values})

    return tuple(combined)


def parametrize(
    arg_names: str | Sequence[str],
    values: Sequence[Sequence[object] | Mapping[str, object] | ParameterSet] | None = None,
    *,
    argvalues: Sequence[Sequence[object] | Mapping[str, object] | ParameterSet] | None = None,
    ids: Sequence[str] | Callable[[Any], str | None] | None = None,
    indirect: bool | Sequence[str] | str = False,
) -> Callable[[Callable[Q, S]], Callable[Q, S]]:
    """Parametrise a test function.

    Args:
        arg_names: Parameter name(s) as a string or sequence
        values: Parameter values for each test case (rustest style)
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
    # Support both 'values' (rustest style) and 'argvalues' (pytest style)
    actual_values = argvalues if argvalues is not None else values
    if actual_values is None:
        msg = "parametrize() requires either 'values' or 'argvalues' parameter"
        raise TypeError(msg)

    normalized_names = _normalize_arg_names(arg_names)

    def decorator(func: Callable[Q, S]) -> Callable[Q, S]:
        # Validated inside the decorator so the failure can name the function, as pytest's
        # does. pytest reports it from `Metafunc.parametrize` during collection; rustest
        # reports it at decoration, i.e. at import, and both are a collection error with
        # exit 2 (measured on both runners).
        normalized_indirect = _normalize_indirect(
            indirect, normalized_names, getattr(func, "__name__", "<unknown>")
        )
        new_cases = _build_cases(normalized_names, actual_values, ids)

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


def _build_cases(
    names: tuple[str, ...],
    values: Sequence[Sequence[object] | Mapping[str, object] | ParameterSet],
    ids: Sequence[str] | Callable[[Any], str | None] | None,
) -> tuple[dict[str, object], ...]:
    case_payloads: list[dict[str, object]] = []

    # Handle callable ids (e.g., ids=str)
    ids_is_callable = callable(ids)

    if ids is not None and not ids_is_callable:
        if len(ids) != len(values):
            msg = "ids must match the number of value sets"
            raise ValueError(msg)

    for index, case in enumerate(values):
        # Handle ParameterSet objects (from pytest.param())
        param_set_id: str | None = None
        actual_case: Any = case
        if isinstance(case, ParameterSet):
            param_set_id = case.id
            actual_case = case.values  # Extract the actual values
            # If it's a single value tuple, unwrap it for consistency
            if len(actual_case) == 1:
                actual_case = actual_case[0]

        # Mappings are only treated as parameter mappings when there are multiple parameters
        # For single parameters, dicts/mappings are treated as values
        data: dict[str, Any]
        if isinstance(actual_case, Mapping) and len(names) > 1:
            data = {name: actual_case[name] for name in names}
        elif isinstance(actual_case, (tuple, list)):
            seq_case = cast(tuple[Any, ...] | list[Any], actual_case)
            if len(seq_case) == len(names):
                # Tuples and lists are unpacked to match parameter names (pytest convention)
                # This handles both single and multiple parameters
                data = {name: seq_case[pos] for pos, name in enumerate(names)}
            else:
                # Length mismatch
                if len(names) == 1:
                    data = {names[0]: actual_case}
                else:
                    raise ValueError("Parametrized value does not match argument names")
        else:
            # Everything else is treated as a single value
            # This includes: primitives, dicts (single param), objects
            if len(names) == 1:
                data = {names[0]: actual_case}
            else:
                raise ValueError("Parametrized value does not match argument names")

        case_id = _resolve_case_id(
            param_set_id=param_set_id,
            ids=ids,
            ids_is_callable=ids_is_callable,
            value=actual_case,
            index=index,
        )

        case_payloads.append({"id": case_id, "values": data})
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
    that ``_v2_worker::_spec_from_pytestmark`` reads, and pytest's uncalled ``MarkDecorator``
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
        # `_v2_worker::_conditions`, which is the behaviour real pytest shows.
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
    ``pytest_runtestloop`` (and `src/v2/execute.rs::stage`) raise ``Interrupted`` before the
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
        # (`_v2_worker.py::_marked_loop_scope`); rejecting it here would move the same
        # complaint to import time, i.e. to a collection error at exit 2, and the corpus
        # differential would diverge on a shape that is otherwise byte-identical.
        #
        # KNOWN DIVERGENCE, and it is the name check above rather than anything below:
        # rejecting a typo'd scope at DECORATION makes it a collection error, so
        # `@mark.asyncio(loop_scope="sesion")` costs the whole file. Measured -- pytest:
        # `1 passed, 1 error`, exit 1 (the healthy sibling still runs); rustest: `1 error`,
        # exit 2 (it does not). Kept because failing at the definition is the better error
        # for a typo and because this signature is shipped v1 surface with its own tests;
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

    **One documented divergence.** pytest's :attr:`traceback` is a ``Traceback`` — a
    sequence of ``TracebackEntry`` objects with ``.lineno``/``.frame``/``.statement`` — while
    rustest's is the raw ``types.TracebackType``, the same object as :attr:`tb`.  Modelling
    ``Traceback`` means modelling ``TracebackEntry``, ``Source`` and ``Frame``; nothing in
    the four real-world suites reads it (jinja2's ``tests/test_debug.py`` uses ``.tb``), so
    the alias is kept and recorded instead of half-built.  ``__repr__``'s ``tblen`` is
    computed by walking ``tb_next``, so it agrees with pytest's number.
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
        # would otherwise fight over the line (see `test_v2_config_oracle.py` for the same
        # note). Fixed properly by #134 in Phase 4 Task 2.
        unfilled = ".typename can only be used after the context manager exits"
        assert self._excinfo is not None, unfilled
        return self.type.__name__

    @property
    def traceback(self) -> Any:
        """The raw traceback — see the class docstring for how this differs from pytest's."""
        return self.tb

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
        if self._excinfo is None:
            return "<ExceptionInfo for raises contextmanager>"
        length = 0
        entry = self._excinfo[2]
        while entry is not None:
            length += 1
            entry = entry.tb_next
        try:
            rendered = repr(self._excinfo[1])
        except Exception:  # pragma: no cover - saferepr's job in pytest
            rendered = f"<unrepresentable {type(self._excinfo[1]).__name__}>"
        return f"<{type(self).__name__} {rendered} tblen={length}>"


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
            expected = self.expected_exceptions
            if not expected:
                raise AssertionError("DID NOT RAISE any exception")
            if len(expected) > 1:
                raise AssertionError(f"DID NOT RAISE any of {expected!r}")
            raise AssertionError(f"DID NOT RAISE {next(iter(expected))!r}")

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


class Failed(Exception):
    """Exception raised by fail() to mark a test as failed."""

    pass


def fail(reason: str = "", pytrace: bool = True) -> None:
    """Explicitly fail the current test with the given message.

    This function immediately raises an exception to fail the test,
    similar to pytest.fail(). It's useful for conditional test failures
    where a simple assert is not sufficient.

    Args:
        reason: The failure message to display
        pytrace: If False, hide the Python traceback (not implemented in rustest,
                 kept for pytest compatibility)

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
    raise Failed(reason)


class Skipped(Exception):
    """Exception raised by skip() to dynamically skip a test."""

    pass


def skip(reason: str = "", allow_module_level: bool = False) -> None:
    """Skip the current test or module dynamically.

    This function raises an exception to skip the test at runtime,
    similar to pytest.skip(). It's useful for conditional test skipping
    based on runtime conditions.

    Args:
        reason: The reason why the test is being skipped
        allow_module_level: If True, allow calling skip() at module level
                           (not fully implemented in rustest)

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
    raise Skipped(reason)


class XFailed(Exception):
    """Exception raised by xfail() to mark a test as expected to fail."""

    pass


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
