"""User facing decorators mirroring the most common pytest helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import inspect
import sys
from typing import Any, ParamSpec, TypeVar, overload, cast

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

        # Generate case ID
        # Priority: ParameterSet id > ids parameter > auto-generated
        if param_set_id is not None:
            case_id = param_set_id
        elif ids is None:
            # Auto-generate ID based on value representation
            case_id = _generate_param_id(actual_value, index)
        elif ids_is_callable:
            generated_id = ids(actual_value)
            case_id = (
                str(generated_id)
                if generated_id is not None
                else _generate_param_id(actual_value, index)
            )
        else:
            case_id = ids[index]

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
        indirect: Controls which parameters should be resolved as fixtures:
            - False (default): All parameters are direct values
            - True: All parameters are passed to fixtures with matching names
            - ["param1", "param2"]: Only specified parameters are passed to fixtures
            - "param1": Single parameter passed to fixture

            When a parameter is indirect, its value is treated as a fixture name,
            and that fixture is resolved and its value used for the test.

            Example:
                @fixture
                def my_data():
                    return {"value": 42}

                @parametrize("data", ["my_data"], indirect=True)
                def test_example(data):
                    assert data["value"] == 42
    """
    # Support both 'values' (rustest style) and 'argvalues' (pytest style)
    actual_values = argvalues if argvalues is not None else values
    if actual_values is None:
        msg = "parametrize() requires either 'values' or 'argvalues' parameter"
        raise TypeError(msg)

    normalized_names = _normalize_arg_names(arg_names)
    normalized_indirect = _normalize_indirect(indirect, normalized_names)

    def decorator(func: Callable[Q, S]) -> Callable[Q, S]:
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
    indirect: bool | Sequence[str] | str, param_names: tuple[str, ...]
) -> list[str]:
    """Normalize the indirect parameter to a list of parameter names.

    Args:
        indirect: The indirect value from parametrize
        param_names: All parameter names from the parametrization

    Returns:
        A list of parameter names that should be treated as indirect (fixture references)

    Raises:
        ValueError: If an indirect parameter name is not in param_names
    """
    if indirect is False:
        return []
    if indirect is True:
        return list(param_names)
    if isinstance(indirect, str):
        if indirect not in param_names:
            msg = f"indirect parameter '{indirect}' not found in parametrize argument names {param_names}"
            raise ValueError(msg)
        return [indirect]
    # It's a sequence of strings
    indirect_list = list(indirect)
    for param in indirect_list:
        if param not in param_names:
            msg = f"indirect parameter '{param}' not found in parametrize argument names {param_names}"
            raise ValueError(msg)
    return indirect_list


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

        # Generate case ID
        # Priority: ParameterSet id > ids parameter > auto-generated
        if param_set_id is not None:
            case_id = param_set_id
        elif ids is None:
            case_id = f"case_{index}"
        elif ids_is_callable:
            # Call the function on the case value to get the ID
            generated_id = ids(actual_case)
            case_id = str(generated_id) if generated_id is not None else f"case_{index}"
        else:
            case_id = ids[index]

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
    """

    def asyncio(
        self,
        func: Callable[..., Any] | None = None,
        *,
        loop_scope: str | None = None,
        timeout: float | None = None,
    ) -> Callable[..., Any]:
        """Mark an async test function to be executed with asyncio.

        This decorator allows you to write async test functions that will be
        automatically executed in an asyncio event loop. The loop_scope parameter
        controls the scope of the event loop used for execution.

        Args:
            func: The function to decorate (when used without parentheses)
            loop_scope: The scope of the event loop. One of:
                - None: Auto-detect based on fixture dependencies (default, recommended)
                - "function": New loop for each test function
                - "class": Shared loop across all test methods in a class
                - "module": Shared loop across all tests in a module
                - "session": Shared loop across all tests in the session
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
        import inspect

        valid_scopes = {"function", "class", "module", "session"}
        if loop_scope is not None and loop_scope not in valid_scopes:
            valid = ", ".join(sorted(valid_scopes))
            msg = f"Invalid loop_scope '{loop_scope}'. Must be one of: {valid}"
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

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            # Only include loop_scope in kwargs if explicitly specified
            # This allows Rust's smart detection to work when loop_scope is None
            mark_kwargs: dict[str, Any] = {}
            if loop_scope is not None:
                mark_kwargs["loop_scope"] = loop_scope
            if timeout is not None:
                mark_kwargs["timeout"] = timeout

            # Handle class decoration - apply mark to all async methods
            if inspect.isclass(f):
                # Apply the mark to the class itself
                mark_decorator = MarkDecorator("asyncio", (), mark_kwargs)
                marked_class = mark_decorator(f)

                # Apply the mark to all async methods in the class as well
                for name, method in inspect.getmembers(
                    marked_class, predicate=inspect.iscoroutinefunction
                ):
                    # Apply the mark to the method so it carries loop_scope metadata
                    marked_method = MarkDecorator("asyncio", (), mark_kwargs)(method)
                    setattr(marked_class, name, marked_method)
                return marked_class

            # For both async and sync functions, just apply the mark
            # The Rust layer will handle event loop management based on loop_scope
            mark_decorator = MarkDecorator("asyncio", (), mark_kwargs)
            return mark_decorator(f)

        # Support both @mark.asyncio and @mark.asyncio(loop_scope="...")
        if func is not None:
            return decorator(func)
        return decorator

    def skipif(
        self,
        condition: bool | str,
        reason: str | None = None,
        *,
        _kw_reason: str | None = None,
    ) -> MarkDecorator:
        """Skip test if condition is true.

        Args:
            condition: Boolean or string condition to evaluate
            reason: Explanation for why the test is skipped (positional or keyword)

        Usage:
            # Both forms are supported (pytest compatibility):
            @mark.skipif(sys.platform == "win32", reason="Not supported on Windows")
            @mark.skipif(sys.platform == "win32", "Not supported on Windows")
            def test_unix_only():
                pass
        """
        # Support both positional and keyword-only 'reason' for pytest compatibility
        # Some older pytest code uses: skipif(condition, reason) with positional
        # Modern pytest uses: skipif(condition, reason="...") with keyword-only
        actual_reason = _kw_reason if _kw_reason is not None else reason
        return MarkDecorator("skipif", (condition,), {"reason": actual_reason})

    def xfail(
        self,
        condition: bool | str | None = None,
        *,
        reason: str | None = None,
        raises: type[BaseException] | tuple[type[BaseException], ...] | None = None,
        run: bool = True,
        strict: bool = False,
    ) -> MarkDecorator:
        """Mark test as expected to fail.

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
        """
        kwargs = {
            "reason": reason,
            "raises": raises,
            "run": run,
            "strict": strict,
        }
        args = () if condition is None else (condition,)
        return MarkDecorator("xfail", args, kwargs)

    def usefixtures(self, *names: str) -> MarkDecorator:
        """Use fixtures without explicitly requesting them as parameters.

        Args:
            *names: Names of fixtures to use

        Usage:
            @mark.usefixtures("setup_db", "cleanup")
            def test_with_fixtures():
                pass
        """
        return MarkDecorator("usefixtures", names, {})

    def __getattr__(self, name: str) -> Any:
        """Create a mark decorator for the given name."""
        # Return a callable that can be used as @mark.name or @mark.name(args)
        if name == "parametrize":
            return self._create_parametrize_mark()
        return self._create_mark(name)

    def _create_mark(self, name: str) -> Any:
        """Create a MarkDecorator that can be called with or without arguments."""

        class _MarkDecoratorFactory:
            """Factory that allows @mark.name or @mark.name(args)."""

            def __init__(self, mark_name: str) -> None:
                super().__init__()
                self.mark_name = mark_name

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                # If called with a single argument that's a function, it's @mark.name
                if (
                    len(args) == 1
                    and not kwargs
                    and callable(args[0])
                    and hasattr(args[0], "__name__")
                ):
                    decorator = MarkDecorator(self.mark_name, (), {})
                    return decorator(args[0])
                # Otherwise it's @mark.name(args) - return a decorator
                return MarkDecorator(self.mark_name, args, kwargs)

        return _MarkDecoratorFactory(name)

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


class ExceptionInfo:
    """Information about an exception caught by raises().

    Attributes:
        type: The exception type
        value: The exception instance
        traceback: The exception traceback
    """

    def __init__(
        self, exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any
    ) -> None:
        super().__init__()
        self.type = exc_type
        self.value = exc_value
        self.traceback = exc_tb

    def __repr__(self) -> str:
        return f"<ExceptionInfo {self.type.__name__}({self.value!r})>"


class RaisesContext:
    """Context manager for asserting that code raises a specific exception.

    This mimics pytest.raises() behavior, supporting:
    - Single or tuple of exception types
    - Optional regex matching of exception messages
    - Access to caught exception information

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
        exc_type: type[BaseException] | tuple[type[BaseException], ...],
        *,
        match: str | None = None,
    ) -> None:
        super().__init__()
        self.exc_type = exc_type
        self.match_pattern = match
        self.excinfo: ExceptionInfo | None = None

    def __enter__(self) -> RaisesContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        # No exception was raised
        if exc_type is None:
            exc_name = self._format_exc_name()
            msg = f"DID NOT RAISE {exc_name}"
            raise AssertionError(msg)

        # At this point, we know an exception was raised, so exc_val cannot be None
        assert exc_val is not None, "exc_val must not be None when exc_type is not None"

        # Check if the exception type matches
        if not issubclass(exc_type, self.exc_type):
            # Unexpected exception type - let it propagate
            return False

        # Store the exception information
        self.excinfo = ExceptionInfo(exc_type, exc_val, exc_tb)

        # Check if the message matches the pattern (if provided)
        if self.match_pattern is not None:
            import re

            exc_message = str(exc_val)
            if not re.search(self.match_pattern, exc_message):
                msg = (
                    f"Pattern {self.match_pattern!r} does not match "
                    f"{exc_message!r}. Exception: {exc_type.__name__}: {exc_message}"
                )
                raise AssertionError(msg)

        # Suppress the exception (it was expected)
        return True

    def _format_exc_name(self) -> str:
        """Format the expected exception name(s) for error messages."""
        if isinstance(self.exc_type, tuple):
            names = " or ".join(exc.__name__ for exc in self.exc_type)
            return names
        return self.exc_type.__name__

    @property
    def value(self) -> BaseException:
        """Access the caught exception value."""
        if self.excinfo is None:
            msg = "No exception was caught"
            raise AttributeError(msg)
        return self.excinfo.value

    @property
    def type(self) -> type[BaseException]:
        """Access the caught exception type."""
        if self.excinfo is None:
            msg = "No exception was caught"
            raise AttributeError(msg)
        return self.excinfo.type


def raises(
    exc_type: type[BaseException] | tuple[type[BaseException], ...],
    *,
    match: str | None = None,
) -> RaisesContext:
    """Assert that code raises a specific exception.

    Args:
        exc_type: The expected exception type(s). Can be a single type or tuple of types.
        match: Optional regex pattern to match against the exception message.

    Returns:
        A context manager that catches and validates the exception.

    Raises:
        AssertionError: If no exception is raised, or if the message doesn't match.

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
    return RaisesContext(exc_type, match=match)


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
