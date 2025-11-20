"""
Pytest compatibility shim for rustest.

This module provides a pytest-compatible API that translates to rustest
under the hood. It allows users to run existing pytest test suites with
rustest by using: rustest --pytest-compat tests/

Supported pytest features:
- @pytest.fixture() with scopes (function/class/module/session)
- @pytest.mark.* decorators
- @pytest.mark.parametrize()
- @pytest.mark.skip() and @pytest.mark.skipif()
- @pytest.mark.asyncio (from pytest-asyncio plugin)
- pytest.raises()
- pytest.approx()
- Type annotations: pytest.FixtureRequest, pytest.MonkeyPatch, pytest.TmpPathFactory,
  pytest.TmpDirFactory, pytest.ExceptionInfo
- Built-in fixtures: tmp_path, tmp_path_factory, tmpdir, tmpdir_factory, monkeypatch, request

Note: The request fixture is a basic stub with limited functionality. Many attributes
will have default/None values. It's provided for compatibility, not full pytest features.

Not supported (with clear error messages):
- Fixture params (@pytest.fixture(params=[...]))
- Some built-in fixtures (capsys, capfd, caplog, etc.)
- Assertion rewriting
- Other pytest plugins

Usage:
    # Instead of modifying your tests, just run:
    $ rustest --pytest-compat tests/

    # Your existing pytest tests will run with rustest:
    import pytest  # This gets intercepted

    @pytest.fixture
    def database():
        return Database()

    @pytest.mark.parametrize("value", [1, 2, 3])
    def test_values(value):
        assert value > 0
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

# Import rustest's actual implementations
from rustest.decorators import (
    fixture as _rustest_fixture,
    parametrize as _rustest_parametrize,
    skip as _rustest_skip,
    mark as _rustest_mark,
    raises as _rustest_raises,
    ExceptionInfo,
    ParameterSet,
)
from rustest.approx import approx as _rustest_approx
from rustest.builtin_fixtures import (
    CaptureFixture,
    MonkeyPatch,
    TmpPathFactory,
    TmpDirFactory,
    capsys,
    capfd,
)

__all__ = [
    "fixture",
    "parametrize",
    "mark",
    "skip",
    "raises",
    "approx",
    "param",
    "warns",
    "deprecated_call",
    "importorskip",
    "CaptureFixture",
    "FixtureRequest",
    "MonkeyPatch",
    "TmpPathFactory",
    "TmpDirFactory",
    "ExceptionInfo",
    "capsys",
    "capfd",
    # Pytest plugin decorator
    "hookimpl",
]

# Type variable for generic functions
F = TypeVar("F", bound=Callable[..., Any])


class FixtureRequest:
    """
    Pytest-compatible FixtureRequest stub for type annotations.

    This is a minimal implementation to support type hints in fixtures that use
    the request parameter. In pytest, FixtureRequest provides access to the
    requesting test context.

    **IMPORTANT LIMITATIONS:**
    This is a STUB implementation with very limited functionality. Most attributes
    return None or default values. Methods raise NotImplementedError with helpful
    messages.

    **Supported (basic compatibility):**
        - Type annotations: request: pytest.FixtureRequest ✓
        - Attribute access without errors ✓
        - request.scope returns "function" ✓

    **NOT Supported (returns None or raises NotImplementedError):**
        - request.param: Always None
        - request.node, function, cls, module, config: Always None
        - request.fixturename: Always None
        - request.addfinalizer(): Raises NotImplementedError
        - request.getfixturevalue(): Raises NotImplementedError
        - request.applymarker(): Raises NotImplementedError
        - request.raiseerror(): Raises NotImplementedError

    Common pytest.FixtureRequest attributes:
        - param: Parameter value (for parametrized fixtures) - ALWAYS None
        - node: Test node object - ALWAYS None
        - function: Test function - ALWAYS None
        - cls: Test class - ALWAYS None
        - module: Test module - ALWAYS None
        - config: Pytest config - ALWAYS None
        - fixturename: Name of the fixture - ALWAYS None
        - scope: Scope of the fixture - Returns "function"

    Example:
        @pytest.fixture
        def my_fixture(request: pytest.FixtureRequest):
            # Type annotation works ✓
            print(f"Scope: {request.scope}")  # Prints "function" ✓

            # These will be None (not supported)
            if request.param:  # Always None
                return request.param

            return "default_value"
    """

    def __init__(self) -> None:
        """Initialize a FixtureRequest stub with default/None values."""
        super().__init__()
        self.param: Any = None
        self.fixturename: str | None = None
        self.scope: str = "function"
        self.node: Any = None
        self.function: Any = None
        self.cls: Any = None
        self.module: Any = None
        self.config: Any = None

    def addfinalizer(self, finalizer: Callable[[], None]) -> None:
        """
        Add a finalizer to be called after the test.

        NOT SUPPORTED in rustest pytest-compat mode.

        In pytest, this would register a function to be called during teardown.
        Rustest does not support this functionality in compat mode.

        Raises:
            NotImplementedError: Always raised with helpful message

        Workaround:
            Use fixture teardown with yield instead:

                @pytest.fixture
                def my_fixture():
                    resource = setup()
                    yield resource
                    teardown(resource)  # This runs after the test
        """
        msg = (
            "request.addfinalizer() is not supported in rustest pytest-compat mode.\n"
            "\n"
            "Workaround: Use fixture teardown with yield:\n"
            "  @pytest.fixture\n"
            "  def my_fixture():\n"
            "      resource = setup()\n"
            "      yield resource\n"
            "      teardown(resource)  # Runs after test\n"
            "\n"
            "For full pytest features, use pytest directly or migrate to native rustest."
        )
        raise NotImplementedError(msg)

    def getfixturevalue(self, name: str) -> Any:
        """
        Get the value of another fixture by name.

        NOT SUPPORTED in rustest pytest-compat mode.

        In pytest, this dynamically retrieves fixture values. Rustest does not
        support this functionality in compat mode.

        Args:
            name: Name of the fixture to retrieve

        Raises:
            NotImplementedError: Always raised with helpful message

        Workaround:
            Declare the fixture as a parameter instead:

                @pytest.fixture
                def my_fixture(other_fixture):  # Instead of getfixturevalue
                    return other_fixture
        """
        msg = (
            "request.getfixturevalue() is not supported in rustest pytest-compat mode.\n"
            "\n"
            f"Workaround: Declare '{name}' as a fixture parameter:\n"
            "  @pytest.fixture\n"
            f"  def my_fixture({name}):\n"
            f"      # Use {name} directly\n"
            f"      return {name}\n"
            "\n"
            "For full pytest features, use pytest directly or migrate to native rustest."
        )
        raise NotImplementedError(msg)

    def applymarker(self, marker: Any) -> None:
        """
        Apply a marker to the test.

        NOT SUPPORTED in rustest pytest-compat mode.

        Raises:
            NotImplementedError: Always raised with helpful message
        """
        msg = (
            "request.applymarker() is not supported in rustest pytest-compat mode.\n"
            "\n"
            "For full pytest features, use pytest directly or migrate to native rustest."
        )
        raise NotImplementedError(msg)

    def raiseerror(self, msg: str | None) -> None:
        """
        Raise an error with the given message.

        NOT SUPPORTED in rustest pytest-compat mode.

        Raises:
            NotImplementedError: Always raised with helpful message
        """
        error_msg = (
            "request.raiseerror() is not supported in rustest pytest-compat mode.\n"
            "\n"
            "For full pytest features, use pytest directly or migrate to native rustest."
        )
        raise NotImplementedError(error_msg)

    def __repr__(self) -> str:
        return "<FixtureRequest (rustest compat stub - limited functionality)>"


def hookimpl(*args: Any, **kwargs: Any) -> Any:
    """
    Stub for pytest.hookimpl decorator - used by pytest plugins.

    NOT FUNCTIONAL in rustest pytest-compat mode. Returns a no-op decorator
    that simply returns the function unchanged.
    """

    def decorator(func: Any) -> Any:
        return func

    if len(args) == 1 and callable(args[0]) and not kwargs:
        # Called as @hookimpl without parentheses
        return args[0]
    else:
        # Called as @hookimpl(...) with arguments
        return decorator


def fixture(
    func: F | None = None,
    *,
    scope: str = "function",
    params: Any = None,
    autouse: bool = False,
    ids: Any = None,
    name: str | None = None,
) -> F | Callable[[F], F]:
    """
    Pytest-compatible fixture decorator.

    Maps to rustest.fixture with validation for unsupported features.

    Supported:
        - scope: function/class/module/session
        - autouse: True/False
        - name: Override fixture name

    Not supported (will raise NotImplementedError):
        - params: Use @pytest.mark.parametrize on the test instead
        - ids: Not needed without params

    Examples:
        @pytest.fixture
        def simple_fixture():
            return 42

        @pytest.fixture(scope="module")
        def database():
            db = Database()
            yield db
            db.close()

        @pytest.fixture(autouse=True)
        def setup():
            setup_environment()

        @pytest.fixture(name="db")
        def _database_fixture():
            return Database()
    """
    # Validate unsupported parameters
    unsupported = []
    if params is not None:
        unsupported.append("params")
    if ids is not None and params is None:
        # ids without params doesn't make sense anyway
        pass
    elif ids is not None:
        unsupported.append("ids")

    if unsupported:
        features = ", ".join(unsupported)
        msg = (
            f"rustest --pytest-compat mode doesn't support fixture {features}.\n"
            f"\n"
            f"Workarounds:\n"
            f"  - params: Use @pytest.mark.parametrize() on your test function instead\n"
            f"\n"
            f"Note: Built-in fixtures (tmp_path, tmpdir, monkeypatch) are fully supported!\n"
            f"\n"
            f"To use full rustest features, change 'import pytest' to 'from rustest import fixture, mark, ...'."
        )
        raise NotImplementedError(msg)

    # Map to rustest fixture - handle both @pytest.fixture and @pytest.fixture()
    if func is not None:
        # Called as @pytest.fixture (without parentheses)
        return _rustest_fixture(func, scope=scope, autouse=autouse, name=name)
    else:
        # Called as @pytest.fixture(...) (with parentheses)
        return _rustest_fixture(scope=scope, autouse=autouse, name=name)  # type: ignore[return-value]


# Direct mappings - these already have identical signatures
parametrize = _rustest_parametrize
raises = _rustest_raises
approx = _rustest_approx
skip = _rustest_skip


class _PytestMarkCompat:
    """
    Compatibility wrapper for pytest.mark.

    Provides the same interface as pytest.mark by delegating to rustest.mark.

    Examples:
        @pytest.mark.slow
        @pytest.mark.integration
        def test_expensive():
            pass

        @pytest.mark.skipif(sys.platform == "win32", reason="Unix only")
        def test_unix():
            pass
    """

    def __getattr__(self, name: str) -> Any:
        """Delegate all mark.* access to rustest.mark.*"""
        return getattr(_rustest_mark, name)

    # Explicitly expose common marks for better IDE support
    @property
    def parametrize(self) -> Any:
        """Alias for @pytest.mark.parametrize (same as top-level parametrize)."""
        return _rustest_mark.parametrize

    def skip(self, reason: str | None = None) -> Callable[[F], F]:
        """Mark test as skipped.

        This is the @pytest.mark.skip() decorator which should skip the test.
        Maps to rustest's skip() decorator.
        """
        return _rustest_skip(reason=reason)  # type: ignore[return-value]

    @property
    def skipif(self) -> Any:
        """Conditional skip decorator."""
        return _rustest_mark.skipif

    @property
    def xfail(self) -> Any:
        """Mark test as expected to fail."""
        return _rustest_mark.xfail

    @property
    def asyncio(self) -> Any:
        """Mark async test to run with asyncio."""
        return _rustest_mark.asyncio


# Create the mark instance
mark = _PytestMarkCompat()


def param(*values: Any, id: str | None = None, marks: Any = None, **kwargs: Any) -> ParameterSet:
    """
    Create a parameter set for use in @pytest.mark.parametrize.

    This function allows you to specify custom test IDs for individual
    parameter sets:

        @pytest.mark.parametrize("x,y", [
            pytest.param(1, 2, id="small"),
            pytest.param(100, 200, id="large"),
        ])

    Args:
        *values: The parameter values for this test case
        id: Optional custom test ID for this parameter set
        marks: Optional marks to apply (currently ignored with a warning)

    Returns:
        A ParameterSet object that will be handled by parametrize

    Note:
        The 'marks' parameter is accepted but not yet functional.
        Tests with marks will run normally but marks won't be applied.
    """
    if marks is not None:
        import warnings

        warnings.warn(
            "pytest.param() marks are not yet supported in rustest pytest-compat mode. The test will run but marks will be ignored.",
            UserWarning,
            stacklevel=2,
        )

    return ParameterSet(values=values, id=id, marks=marks)


class WarningsChecker:
    """Context manager for capturing and checking warnings.

    This implements pytest.warns() functionality for rustest.
    """

    def __init__(
        self,
        expected_warning: type[Warning] | tuple[type[Warning], ...] | None = None,
        match: str | None = None,
    ):
        super().__init__()
        self.expected_warning = expected_warning
        self.match = match
        self._records: list[Any] = []
        self._catch_warnings: Any = None

    def __enter__(self) -> list[Any]:
        import warnings

        self._catch_warnings = warnings.catch_warnings(record=True)
        self._records = self._catch_warnings.__enter__()
        # Cause all warnings to always be triggered
        warnings.simplefilter("always")
        return self._records

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._catch_warnings is not None:
            self._catch_warnings.__exit__(exc_type, exc_val, exc_tb)

        # If there was an exception, don't check warnings
        if exc_type is not None:
            return

        # If no expected warning specified, just return the records
        if self.expected_warning is None:
            return

        # Check that at least one matching warning was raised
        matching_warnings = []
        for record in self._records:
            # Check warning type
            if isinstance(self.expected_warning, tuple):
                type_matches = issubclass(record.category, self.expected_warning)
            else:
                type_matches = issubclass(record.category, self.expected_warning)

            if not type_matches:
                continue

            # Check message match if specified
            if self.match is not None:
                import re

                message_str = str(record.message)
                if not re.search(self.match, message_str):
                    continue

            matching_warnings.append(record)

        if not matching_warnings:
            # Build error message
            if isinstance(self.expected_warning, tuple):
                expected_str = " or ".join(w.__name__ for w in self.expected_warning)
            else:
                expected_str = self.expected_warning.__name__

            if self.match:
                expected_str += f" matching {self.match!r}"

            if self._records:
                actual = ", ".join(f"{r.category.__name__}({r.message!s})" for r in self._records)
                msg = f"Expected {expected_str} but got: {actual}"
            else:
                msg = f"Expected {expected_str} but no warnings were raised"

            raise AssertionError(msg)


def warns(
    expected_warning: type[Warning] | tuple[type[Warning], ...] | None = None,
    *,
    match: str | None = None,
) -> WarningsChecker:
    """
    Context manager to capture and assert warnings.

    This function can be used as a context manager to check that certain
    warnings are raised during execution.

    Args:
        expected_warning: The expected warning class(es), or None to capture all
        match: Optional regex pattern to match against the warning message

    Returns:
        A context manager that yields a list of captured warnings

    Examples:
        # Check that a DeprecationWarning is raised
        with pytest.warns(DeprecationWarning):
            some_deprecated_function()

        # Check warning message matches pattern
        with pytest.warns(UserWarning, match="must be positive"):
            function_with_warning(-1)

        # Capture all warnings without asserting
        with pytest.warns() as record:
            some_code()
        assert len(record) == 2
    """
    return WarningsChecker(expected_warning, match)


def deprecated_call(*, match: str | None = None) -> WarningsChecker:
    """
    Context manager to check that a deprecation warning is raised.

    This is a convenience wrapper around warns(DeprecationWarning).

    Args:
        match: Optional regex pattern to match against the warning message

    Returns:
        A context manager that yields a list of captured warnings

    Example:
        with pytest.deprecated_call():
            some_deprecated_function()
    """
    return WarningsChecker((DeprecationWarning, PendingDeprecationWarning), match)


def importorskip(
    modname: str,
    minversion: str | None = None,
    reason: str | None = None,
    *,
    exc_type: type[ImportError] = ImportError,
) -> Any:
    """
    Import and return the requested module, or skip the test if unavailable.

    This function attempts to import a module and returns it if successful.
    If the import fails or the version is too old, the current test is skipped.

    Args:
        modname: The name of the module to import
        minversion: Minimum required version string (compared with pkg.__version__)
        reason: Custom reason message to display when skipping
        exc_type: The exception type to catch (default: ImportError)

    Returns:
        The imported module

    Example:
        numpy = pytest.importorskip("numpy")
        pandas = pytest.importorskip("pandas", minversion="1.0")
    """
    import importlib

    __tracebackhide__ = True

    compile(modname, "", "eval")  # Validate module name syntax

    try:
        mod = importlib.import_module(modname)
    except exc_type as exc:
        if reason is None:
            reason = f"could not import {modname!r}: {exc}"
        _rustest_skip(reason=reason)
        raise  # This line won't be reached due to skip, but satisfies type checker

    if minversion is not None:
        mod_version = getattr(mod, "__version__", None)
        if mod_version is None:
            if reason is None:
                reason = f"module {modname!r} has no __version__ attribute"
            _rustest_skip(reason=reason)
        else:
            # Simple version comparison (works for most common cases)
            from packaging.version import Version

            try:
                if Version(mod_version) < Version(minversion):
                    if reason is None:
                        reason = f"module {modname!r} has version {mod_version}, required is {minversion}"
                    _rustest_skip(reason=reason)
            except Exception:
                # Fallback to string comparison if packaging fails
                if mod_version < minversion:
                    if reason is None:
                        reason = f"module {modname!r} has version {mod_version}, required is {minversion}"
                    _rustest_skip(reason=reason)

    return mod


# Module-level version to match pytest
__version__ = "rustest-compat"

# Cache for dynamically generated stub classes
_dynamic_stubs: dict[str, type] = {}


def __getattr__(name: str) -> Any:
    """
    Dynamically provide stub classes for any pytest attribute not explicitly defined.

    This allows pytest plugins (like pytest_asyncio) to import any pytest internal
    without errors, while these remain non-functional stubs.

    This is the recommended Python 3.7+ way to handle "catch-all" module imports.
    """
    # Check if we've already created this stub
    if name in _dynamic_stubs:
        return _dynamic_stubs[name]

    # Don't intercept private attributes or special methods
    if name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    # Create a stub class dynamically
    def stub_init(self: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def stub_repr(self: Any) -> str:
        return f"<{name} (rustest compat stub)>"

    stub_class = type(
        name,
        (),
        {
            "__doc__": (
                f"Dynamically generated stub for pytest.{name}.\n\n"
                f"NOT FUNCTIONAL in rustest pytest-compat mode. This stub exists\n"
                f"to allow pytest plugins to import without errors."
            ),
            "__init__": stub_init,
            "__repr__": stub_repr,
            "__module__": __name__,
        },
    )

    # Cache it so subsequent imports get the same class
    _dynamic_stubs[name] = stub_class
    return stub_class
