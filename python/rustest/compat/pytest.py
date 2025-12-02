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

# pyright: reportMissingImports=false

from __future__ import annotations

from typing import Any, Callable, TypeVar, TypedDict, cast

try:
    from rustest import rust as _rust_bridge
except (
    Exception
):  # pragma: no cover - rust module not available when running unit tests without extension
    _rust_bridge = None

# Import rustest's actual implementations
from rustest.decorators import (
    fixture as _rustest_fixture,
    parametrize as _rustest_parametrize,
    skip_decorator as _rustest_skip_decorator,
    mark as _rustest_mark,
    raises as _rustest_raises,
    fail as _rustest_fail,
    Failed as _rustest_Failed,
    Skipped as _rustest_Skipped,
    XFailed as _rustest_XFailed,
    xfail as _rustest_xfail,
    skip as _rustest_skip_function,
    ExceptionInfo,
    ParameterSet,
)
from rustest.approx import approx as _rustest_approx
from rustest.builtin_fixtures import (
    Cache,
    CaptureFixture,
    LogCaptureFixture,
    MonkeyPatch,
    TmpPathFactory,
    TmpDirFactory,
    cache,
    caplog,
    capsys,
    capfd,
)

__all__ = [
    "fixture",
    "parametrize",
    "mark",
    "skip",
    "xfail",
    "raises",
    "fail",
    "Failed",
    "Skipped",
    "XFailed",
    "approx",
    "param",
    "warns",
    "deprecated_call",
    "importorskip",
    "Cache",
    "CaptureFixture",
    "LogCaptureFixture",
    "FixtureRequest",
    "Node",
    "Config",
    "MonkeyPatch",
    "TmpPathFactory",
    "TmpDirFactory",
    "ExceptionInfo",
    "cache",
    "caplog",
    "capsys",
    "capfd",
    # Pytest plugin decorator
    "hookimpl",
]

# Type variable for generic functions
F = TypeVar("F", bound=Callable[..., Any])


class MarkerDict(TypedDict):
    """Type definition for marker dictionaries."""

    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class Node:
    """
    Pytest-compatible Node object representing a test or collection node.

    This provides basic node information for compatibility with pytest fixtures
    that access request.node.

    **Supported:**
        - node.name: Test/node name
        - node.nodeid: Full test identifier
        - node.get_closest_marker(name): Get marker by name
        - node.add_marker(marker): Add marker to node
        - node.keywords: Dictionary of keywords/markers

    **Limited Support:**
        - node.parent: Always None (not implemented)
        - node.session: Always None (not implemented)
        - node.config: Returns associated Config object if available

    Example:
        def test_example(request):
            assert request.node.name == "test_example"
            marker = request.node.get_closest_marker("skip")
            if marker:
                pytest.skip(marker.kwargs.get("reason", ""))
    """

    def __init__(
        self,
        name: str = "",
        nodeid: str = "",
        markers: list[MarkerDict] | None = None,
        config: Any = None,
    ) -> None:
        """Initialize a Node.

        Args:
            name: Name of the test/node
            nodeid: Full identifier for the test (e.g., "tests/test_foo.py::test_bar")
            markers: List of marker dictionaries
            config: Associated Config object
        """
        super().__init__()
        self.name: str = name
        self.nodeid: str = nodeid
        self._markers: list[MarkerDict] = markers or []
        self.config: Any = config
        self.parent: Any = None
        self.session: Any = None
        # Keywords dict for pytest compatibility
        self.keywords: dict[str, Any] = {}
        # Add markers to keywords
        for marker in self._markers:
            if "name" in marker:
                self.keywords[marker["name"]] = True

    def get_closest_marker(self, name: str) -> Any:
        """Get the closest marker with the given name.

        Args:
            name: Name of the marker to retrieve

        Returns:
            A marker object with args and kwargs attributes, or None if not found

        Example:
            skip_marker = request.node.get_closest_marker("skip")
            if skip_marker:
                reason = skip_marker.kwargs.get("reason", "")
        """
        # Find the first marker with the given name
        for marker in reversed(self._markers):  # Start from most recently added
            if marker.get("name") == name:
                # Return a simple object with args and kwargs attributes
                return _MarkerInfo(
                    name=name,
                    args=marker.get("args", ()),
                    kwargs=marker.get("kwargs", {}),
                )
        return None

    def add_marker(self, marker: Any, append: bool = True) -> None:
        """Add a marker to this node.

        Args:
            marker: Marker to add (can be string name or marker object)
            append: If True, append to markers list; if False, prepend

        Example:
            request.node.add_marker("slow")
            request.node.add_marker(pytest.mark.xfail(reason="known bug"))
        """
        marker_dict: MarkerDict

        # Handle string markers
        if isinstance(marker, str):
            marker_dict = {"name": marker, "args": (), "kwargs": {}}
        # Handle ParameterSet/MarkDecorator objects
        elif hasattr(marker, "__rustest_marks__"):
            # This is a decorated object with marks
            marks: list[Any] = getattr(marker, "__rustest_marks__", [])
            for mark in marks:
                if append:
                    self._markers.append(mark)
                else:
                    self._markers.insert(0, mark)
                # Add to keywords
                if "name" in mark and isinstance(mark.get("name"), str):
                    name_str: str = mark["name"]
                    self.keywords[name_str] = True
            return
        # Handle mark objects with name/args/kwargs
        elif hasattr(marker, "name"):
            marker_dict = {
                "name": str(marker.name),
                "args": getattr(marker, "args", ()),
                "kwargs": getattr(marker, "kwargs", {}),
            }
        # Handle dict markers directly
        elif isinstance(marker, dict):
            # Validate and normalize the dict
            # Type ignores needed for untyped dict from external sources
            marker_dict = {
                "name": str(marker.get("name", "")),  # type: ignore[arg-type]
                "args": cast(tuple[Any, ...], marker.get("args", ())),  # type: ignore[reportUnknownMemberType]
                "kwargs": cast(dict[str, Any], marker.get("kwargs", {})),  # type: ignore[reportUnknownMemberType]
            }
        else:
            # Unknown marker type - try to extract what we can
            marker_dict = {"name": str(marker), "args": (), "kwargs": {}}

        if append:
            self._markers.append(marker_dict)
        else:
            self._markers.insert(0, marker_dict)

        # Add to keywords
        name = marker_dict["name"]
        if name:  # name is now guaranteed to be str
            self.keywords[name] = True

    def listextrakeywords(self) -> set[str]:
        """Return a set of extra keywords/markers for this node.

        Returns:
            Set of marker/keyword names
        """
        return set(self.keywords.keys())


class _MarkerInfo:
    """Simple marker info object returned by get_closest_marker()."""

    def __init__(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        super().__init__()
        self.name = name
        self.args = args
        self.kwargs = kwargs

    def __repr__(self) -> str:
        return f"Mark(name={self.name!r}, args={self.args!r}, kwargs={self.kwargs!r})"


class Config:
    """
    Pytest-compatible Config object for accessing test configuration.

    This provides basic configuration access for compatibility with pytest
    fixtures that access request.config.

    **Supported:**
        - config.getoption(name, default=None): Get command-line option value
        - config.getini(name): Get configuration value from pytest.ini/setup.cfg/tox.ini
        - config.rootpath: Root directory path (always returns current directory)
        - config.inipath: Path to config file (always None in rustest)

    **Limited Support:**
        - config.pluginmanager: Stub PluginManager (minimal functionality)
        - config.option: Namespace with option values

    **Not Supported:**
        - Advanced plugin configuration
        - Hook specifications

    Example:
        def test_example(request):
            verbose = request.config.getoption("verbose", default=0)
            if verbose > 1:
                print("Running in verbose mode")
    """

    def __init__(
        self, options: dict[str, Any] | None = None, ini_values: dict[str, Any] | None = None
    ) -> None:
        """Initialize a Config.

        Args:
            options: Dictionary of command-line options
            ini_values: Dictionary of ini configuration values
        """
        super().__init__()
        self._options: dict[str, Any] = options or {}
        self._ini_values: dict[str, Any] = ini_values or {}

        # Create option namespace for compatibility
        self.option = _OptionNamespace(self._options)

        # Stub pluginmanager
        self.pluginmanager = _PluginManagerStub()

        # Paths
        from pathlib import Path

        self.rootpath: Path = Path.cwd()
        self.inipath: Path | None = None

    def getoption(self, name: str, default: Any = None, skip: bool = False) -> Any:
        """Get command-line option value.

        Args:
            name: Option name (e.g., "verbose", "capture", "tb")
            default: Default value if option not found
            skip: If True and option not found, skip the test

        Returns:
            Option value or default

        Example:
            verbose = request.config.getoption("verbose", default=0)
        """
        # Remove leading dashes from option name
        clean_name = name.lstrip("-")

        value = self._options.get(clean_name, default)

        if skip and value == default and clean_name not in self._options:
            # Import skip function from rustest
            from rustest.decorators import skip as skip_test

            skip_test(f"Option '{name}' not found")

        return value

    def getini(self, name: str) -> Any:
        """Get configuration value from pytest.ini/setup.cfg/tox.ini.

        Args:
            name: Configuration option name

        Returns:
            Configuration value (default empty string/list if not found)

        Example:
            testpaths = request.config.getini("testpaths")
        """
        value = self._ini_values.get(name)

        # Return appropriate default based on common ini values
        if value is None:
            # Common list-type ini values
            if name in {
                "testpaths",
                "python_files",
                "python_classes",
                "python_functions",
                "markers",
                "filterwarnings",
            }:
                return []
            # Common string-type ini values
            return ""

        return value

    def addinivalue_line(self, name: str, line: str) -> None:
        """Add a line to an ini-file option.

        This is a no-op in rustest for compatibility.

        Args:
            name: Option name
            line: Line to add
        """
        # No-op for compatibility
        pass


class _OptionNamespace:
    """Namespace object for accessing options as attributes."""

    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__()
        self._options = options

    def __getattr__(self, name: str) -> Any:
        return self._options.get(name)

    def __repr__(self) -> str:
        return f"Namespace({self._options})"


class _PluginManagerStub:
    """Stub PluginManager for basic compatibility."""

    def __init__(self) -> None:
        super().__init__()
        self._plugins: list[Any] = []

    def get_plugin(self, name: str) -> Any:
        """Get plugin by name (always returns None)."""
        return None

    def hasplugin(self, name: str) -> bool:
        """Check if plugin is registered (always returns False)."""
        return False

    def register(self, plugin: Any, name: str | None = None) -> None:
        """Register a plugin (no-op for compatibility)."""
        pass

    def __repr__(self) -> str:
        return "<PluginManager (stub)>"


class FixtureRequest:
    """
    Pytest-compatible FixtureRequest for fixture parametrization.

    This implementation provides access to fixture parameter values via
    request.param for parametrized fixtures.

    **Supported:**
        - Type annotations: request: pytest.FixtureRequest
        - request.param: Current parameter value for parametrized fixtures
        - request.scope: Fixture scope (default: "function")
        - request.node: Test node object with marker access
        - request.config: Configuration object with option access

    **Limited Support:**
        - request.node.get_closest_marker(name): Get marker by name
        - request.node.add_marker(marker): Add marker to node
        - request.config.getoption(name): Get command-line option
        - request.config.getini(name): Get ini configuration value

    **NOT Supported (returns None or raises NotImplementedError):**
        - request.function, cls, module: Always None
        - request.fixturename: Always None
        - request.addfinalizer(): Raises NotImplementedError
        - request.getfixturevalue(): Raises NotImplementedError

    Common pytest.FixtureRequest attributes:
        - param: Parameter value (for parametrized fixtures) - SUPPORTED
        - node: Test node object - SUPPORTED (basic functionality)
        - config: Pytest config - SUPPORTED (basic functionality)
        - function: Test function - Always None
        - cls: Test class - Always None
        - module: Test module - Always None
        - fixturename: Name of the fixture - Always None
        - scope: Scope of the fixture - Returns "function"

    Example:
        @pytest.fixture(params=[1, 2, 3])
        def number(request: pytest.FixtureRequest):
            # Access parameter value
            return request.param

        @pytest.fixture
        def conditional_fixture(request):
            # Check for markers
            marker = request.node.get_closest_marker("slow")
            if marker:
                pytest.skip("Skipping slow test")

            # Access configuration
            verbose = request.config.getoption("verbose", default=0)
            if verbose > 1:
                print(f"Test: {request.node.name}")

            return "fixture_value"
    """

    def __init__(
        self,
        param: Any = None,
        node_name: str = "",
        nodeid: str | None = None,
        node_markers: list[MarkerDict] | None = None,
        config_options: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a FixtureRequest.

        Args:
            param: The parameter value for parametrized fixtures
            node_name: Name of the current test node
            nodeid: Fully-qualified identifier for the current test node
            node_markers: List of markers applied to the node
            config_options: Dictionary of configuration options
        """
        super().__init__()
        self.param: Any = param
        self.fixturename: str | None = None
        self.scope: str = "function"

        # Create Config and Node objects
        self.config: Config = Config(options=config_options)
        node_identifier = nodeid or node_name
        self.node: Node = Node(
            name=node_name,
            nodeid=node_identifier,
            markers=node_markers,
            config=self.config,
        )

        # These remain unsupported
        self.function: Any = None
        self.cls: Any = None
        self.module: Any = None

        # Cache for executed fixtures (per-test)
        self._executed_fixtures: dict[str, Any] = {}

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

        This method dynamically loads and executes fixtures at runtime by name.
        Fixture dependencies are resolved recursively, and results are cached
        per test execution.

        Args:
            name: Name of the fixture to retrieve

        Returns:
            The fixture value

        Raises:
            ValueError: If the fixture is not found
            NotImplementedError: If the fixture is async (not yet supported)

        Example:
            @pytest.fixture
            def user():
                return {"name": "Alice"}

            def test_dynamic(request):
                user = request.getfixturevalue("user")
                assert user["name"] == "Alice"
        """
        # Check cache first
        if name in self._executed_fixtures:
            return self._executed_fixtures[name]

        if _rust_bridge is not None:
            try:
                return _rust_bridge.getfixturevalue(name)
            except (AttributeError, RuntimeError) as exc:
                # When not running under rustest, fall back to Python resolver
                message = str(exc)
                if (
                    "active rustest test" not in message
                    and "only run while rustest is executing a test" not in message
                ):
                    raise
                # Continue to fallback path below so users still get a value when
                # calling request.getfixturevalue() in environments where the Rust
                # extension is not active (e.g., plain pytest).

        # Import and use the fixture registry fallback
        from rustest.fixture_registry import resolve_fixture

        try:
            # Resolve the fixture (handles dependencies and caching)
            result = resolve_fixture(
                name,
                self._executed_fixtures,
                request_obj=self,
            )
            return result
        except ValueError as e:
            # Fixture not found
            raise ValueError(f"fixture '{name}' not found") from e
        except NotImplementedError:
            # Async fixture
            raise

    def applymarker(self, marker: Any) -> None:
        """
        Apply a marker to the test.

        Supports skip, skipif, and xfail markers. Other markers are stored but ignored.

        Args:
            marker: Marker to apply (can be string name or marker object)

        Raises:
            Skipped: If skip or skipif marker is applied and condition is met

        Example:
            def test_dynamic_skip(request):
                if not has_required_library():
                    request.applymarker(pytest.mark.skip(reason="Library not available"))
        """
        # First, check if this is a skip decorator function (from pytest.mark.skip)
        # These are created by skip_decorator() and have __rustest_skip__ attribute
        if callable(marker) and hasattr(marker, "__name__") and marker.__name__ == "decorator":
            # This might be a skip decorator - try to apply it to a dummy function
            # to extract the skip reason
            def dummy():
                pass

            try:
                decorated = marker(dummy)
                if hasattr(decorated, "__rustest_skip__"):
                    # This is a skip decorator - extract the reason and skip
                    reason = getattr(decorated, "__rustest_skip__", "")
                    _rustest_skip_function(reason=reason)
                    return
            except (_rustest_Skipped, _rustest_XFailed, _rustest_Failed):
                # Re-raise test control exceptions
                raise
            except Exception:
                # Swallow other exceptions (e.g., if marker() fails)
                pass

        # Add the marker to the node
        self.node.add_marker(marker)

        # Handle MarkDecorator objects (have name, args, kwargs attributes)
        if hasattr(marker, "name"):
            marker_name = str(getattr(marker, "name"))

            if marker_name == "skip":
                # Extract reason from marker
                reason = getattr(marker, "kwargs", {}).get("reason", "")
                _rustest_skip_function(reason=reason)

            elif marker_name == "skipif":
                # Extract condition from args
                args = getattr(marker, "args", ())
                if args and len(args) > 0:
                    condition = args[0]
                    if condition:
                        # Condition is met, skip the test
                        reason = getattr(marker, "kwargs", {}).get("reason", "")
                        _rustest_skip_function(reason=reason)

            elif marker_name == "xfail":
                # Store xfail marker for potential later handling
                # For now, just add it to the node - the test will run normally
                pass

            # Other markers (slow, integration, etc.) are just stored on the node
            # No action needed - they're for pytest plugins which rustest doesn't support

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

    Maps to rustest.fixture with full support for fixture parametrization.

    Supported:
        - scope: function/class/module/session
        - autouse: True/False
        - name: Override fixture name
        - params: List of parameter values for fixture parametrization
        - ids: Custom IDs for each parameter value

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

        @pytest.fixture(params=[1, 2, 3])
        def number(request):
            return request.param

        @pytest.fixture(params=["mysql", "postgres"], ids=["MySQL", "PostgreSQL"])
        def database_type(request):
            return request.param
    """
    # Map to rustest fixture - handle both @pytest.fixture and @pytest.fixture()
    if func is not None:
        # Called as @pytest.fixture (without parentheses)
        return _rustest_fixture(
            func, scope=scope, autouse=autouse, name=name, params=params, ids=ids
        )
    else:
        # Called as @pytest.fixture(...) (with parentheses)
        return _rustest_fixture(scope=scope, autouse=autouse, name=name, params=params, ids=ids)  # type: ignore[return-value]


# Direct mappings - these already have identical signatures
parametrize = _rustest_parametrize
raises = _rustest_raises
approx = _rustest_approx
skip = _rustest_skip_function  # pytest.skip() function (raises Skipped)
fail = _rustest_fail
Failed = _rustest_Failed
Skipped = _rustest_Skipped
XFailed = _rustest_XFailed
xfail = _rustest_xfail


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
        Maps to rustest's skip_decorator().
        """
        return _rustest_skip_decorator(reason=reason)  # type: ignore[return-value]

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
        matching_warnings: list[Any] = []
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
        _rustest_skip_function(reason=reason)
        raise  # This line won't be reached due to skip, but satisfies type checker

    if minversion is not None:
        mod_version = getattr(mod, "__version__", None)
        if mod_version is None:
            if reason is None:
                reason = f"module {modname!r} has no __version__ attribute"
            _rustest_skip_function(reason=reason)
        else:
            # Simple version comparison (works for most common cases)
            from packaging.version import Version

            try:
                if Version(mod_version) < Version(minversion):
                    if reason is None:
                        reason = f"module {modname!r} has version {mod_version}, required is {minversion}"
                    _rustest_skip_function(reason=reason)
            except Exception:
                # Fallback to string comparison if packaging fails
                if mod_version < minversion:
                    if reason is None:
                        reason = f"module {modname!r} has version {mod_version}, required is {minversion}"
                    _rustest_skip_function(reason=reason)

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
