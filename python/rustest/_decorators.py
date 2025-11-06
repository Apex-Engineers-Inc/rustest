"""User facing decorators mirroring the most common pytest helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., object])

# Valid fixture scopes
VALID_SCOPES = frozenset(["function", "class", "module", "session"])


def fixture(
    func: F | None = None,
    *,
    scope: str = "function",
) -> F | Callable[[F], F]:
    """Mark a function as a fixture with a specific scope.

    Args:
        func: The function to decorate (when used without parentheses)
        scope: The scope of the fixture. One of:
            - "function": New instance for each test function (default)
            - "class": Shared across all test methods in a class
            - "module": Shared across all tests in a module
            - "session": Shared across all tests in the session

    Usage:
        @fixture
        def my_fixture():
            return 42

        @fixture(scope="module")
        def shared_fixture():
            return expensive_setup()
    """
    if scope not in VALID_SCOPES:
        valid = ", ".join(sorted(VALID_SCOPES))
        msg = f"Invalid fixture scope '{scope}'. Must be one of: {valid}"
        raise ValueError(msg)

    def decorator(f: F) -> F:
        setattr(f, "__rustest_fixture__", True)
        setattr(f, "__rustest_fixture_scope__", scope)
        return f

    # Support both @fixture and @fixture(scope="...")
    if func is not None:
        return decorator(func)
    return decorator


def skip(reason: str | None = None) -> Callable[[F], F]:
    """Skip a test or fixture."""

    def decorator(func: F) -> F:
        setattr(func, "__rustest_skip__", reason or "skipped via rustest.skip")
        return func

    return decorator


def parametrize(
    arg_names: str | Sequence[str],
    values: Sequence[Sequence[object] | Mapping[str, object]],
    *,
    ids: Sequence[str] | None = None,
) -> Callable[[F], F]:
    """Parametrise a test function."""

    normalized_names = _normalize_arg_names(arg_names)

    def decorator(func: F) -> F:
        cases = _build_cases(normalized_names, values, ids)
        setattr(func, "__rustest_parametrization__", cases)
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


def _build_cases(
    names: tuple[str, ...],
    values: Sequence[Sequence[object] | Mapping[str, object]],
    ids: Sequence[str] | None,
) -> tuple[dict[str, object], ...]:
    case_payloads: list[dict[str, object]] = []
    if ids is not None and len(ids) != len(values):
        msg = "ids must match the number of value sets"
        raise ValueError(msg)

    for index, case in enumerate(values):
        # Mappings are only treated as parameter mappings when there are multiple parameters
        # For single parameters, dicts/mappings are treated as values
        if isinstance(case, Mapping) and len(names) > 1:
            data = {name: case[name] for name in names}
        elif isinstance(case, tuple) and len(case) == len(names):
            # Tuples are unpacked to match parameter names (pytest convention)
            # This handles both single and multiple parameters
            data = {name: case[pos] for pos, name in enumerate(names)}
        else:
            # Everything else is treated as a single value
            # This includes: primitives, lists (even if len==names), dicts (single param), objects
            if len(names) == 1:
                data = {names[0]: case}
            else:
                raise ValueError("Parametrized value does not match argument names")
        case_id = ids[index] if ids is not None else f"case_{index}"
        case_payloads.append({"id": case_id, "values": data})
    return tuple(case_payloads)


class MarkDecorator:
    """A decorator for applying a mark to a test function."""

    def __init__(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        super().__init__()
        self.name = name
        self.args = args
        self.kwargs = kwargs

    def __call__(self, func: F) -> F:
        """Apply this mark to the given function."""
        # Get existing marks or create a new list
        existing_marks: list[dict[str, Any]] = getattr(func, "__rustest_marks__", [])

        # Add this mark to the list
        mark_data = {
            "name": self.name,
            "args": self.args,
            "kwargs": self.kwargs,
        }
        existing_marks.append(mark_data)

        # Store the marks list on the function
        setattr(func, "__rustest_marks__", existing_marks)
        return func

    def __repr__(self) -> str:
        return f"Mark({self.name!r}, {self.args!r}, {self.kwargs!r})"


class MarkGenerator:
    """Namespace for dynamically creating marks like pytest.mark.

    Usage:
        @mark.slow
        @mark.integration
        @mark.timeout(seconds=30)
    """

    def __getattr__(self, name: str) -> Any:
        """Create a mark decorator for the given name."""
        # Return a callable that can be used as @mark.name or @mark.name(args)
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


# Create a singleton instance
mark = MarkGenerator()


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
