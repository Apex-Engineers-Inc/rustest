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

import sys
from typing import Any, Callable, NoReturn, TypeVar, TypedDict, cast

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
    SkipMarkDecorator as _SkipMarkDecorator,
    BareOrFactoryMark as _BareOrFactoryMark,
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
from rustest._warnings import WarningsRecorder as _WarningsRecorder
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
    pytestconfig,
)

__all__ = [
    "fixture",
    "parametrize",
    "mark",
    "skip",
    "xfail",
    "raises",
    "fail",
    "exit",
    "Exit",
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
    "pytestconfig",
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
    """Pytest-compatible Node representing a test or collection node.

    Supports: name, nodeid, get_closest_marker(), add_marker(), keywords, config.
    Not implemented: parent, session (always None).
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
        """Get the closest marker with the given name, or None if not found."""
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
        """Add a marker to this node."""
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
    """Pytest-compatible Config for accessing test configuration.

    Supports: getoption(), getini(), rootpath, inipath, pluginmanager, option.
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
        """Get command-line option value, or default if not found."""
        # Remove leading dashes from option name
        clean_name = name.lstrip("-")

        value = self._options.get(clean_name, default)

        if skip and value == default and clean_name not in self._options:
            # Import skip function from rustest
            from rustest.decorators import skip as skip_test

            skip_test(f"Option '{name}' not found")

        return value

    def getini(self, name: str) -> Any:
        """Get configuration value from pytest.ini/setup.cfg/tox.ini."""
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
    """Pytest-compatible FixtureRequest for fixture parametrization.

    Supports: param, scope, node, config, getfixturevalue().
    Not implemented: function, cls, module, fixturename (always None),
    addfinalizer() (raises NotImplementedError).
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
        """Not supported — use yield-based fixture teardown instead. Always raises NotImplementedError."""
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
        """Get the value of another fixture by name, resolving dependencies recursively."""
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


class Exit(Exception):
    """Port of `_pytest/outcomes.py::Exit` (pytest 8.4.2, l. 69-77).

    ``Exception``, not ``BaseException``, because that is what pytest uses — and the
    difference is observable: a test body wrapping its work in ``except Exception`` swallows
    a ``pytest.exit()`` under pytest too. Reproducing the base class reproduces that.

    The rustest v2 worker therefore lists this in ``_v2_worker.ABORT_EXCEPTIONS`` and
    re-raises it ahead of its own ``except Exception`` handlers, which is exactly how pytest
    gets the same result with a plain ``Exception``: nothing between the test body and
    ``wrap_session`` catches it.
    """

    def __init__(self, msg: str = "unknown reason", returncode: int | None = None) -> None:
        self.msg = msg
        self.returncode = returncode
        super().__init__(msg)


def exit(reason: str = "", returncode: int | None = None) -> NoReturn:  # noqa: A001 - pytest's name
    """Port of `_pytest/outcomes.py::exit` (l. 105-122): ``raise Exit(reason, returncode)``.

    **Defined explicitly so it never reaches this module's catch-all ``__getattr__``.**  That
    fallback manufactures a do-nothing stub class for any unknown public attribute, which is
    right for a plugin merely *importing* a pytest internal and catastrophic for a *called*
    control-flow function: ``pytest.exit("stopping")`` used to construct a stub instance,
    return it, and let the test — and the whole session — carry on green. That is the
    silent-false-green class, and it is what the ``marks/pytest-exit`` conformance case pins.

    Fixing it here rather than by making the stub raise is deliberate: the stub table serves
    every other unknown attribute, and turning it into a trap would change behaviour for
    imports that are legitimately harmless.

    ``returncode`` is accepted and carried on the exception. Under the v2 engine it is not
    yet honoured — the session-stop signal reaches the orchestrator as a worker exit code,
    which cannot carry a payload — so the run exits **2** (pytest's ``INTERRUPTED``, and
    pytest's own answer when ``returncode`` is omitted). Recorded in the
    ``marks/pytest-exit`` waiver.
    """
    raise Exit(reason, returncode)


# pytest exposes the exception class as an attribute of the function it is raised by
# (`_pytest/outcomes.py::_with_exception`), so `except pytest.exit.Exception` works.
exit.Exception = Exit  # pyright: ignore[reportFunctionMemberAccess]


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

        @pytest.mark.skip          # bare, no parentheses -- also ordinary pytest
        def test_never_runs():
            pass
    """

    def __init__(self) -> None:
        super().__init__()

        # `skip` cannot simply be delegated to `_rustest_mark`: the native `mark.skip` only
        # records a mark dict in `__rustest_marks__`, and v1's Rust collector reads skips
        # from the `__rustest_skip__` attribute alone (src/discovery.rs::collect_tests).
        # The compat surface therefore keeps its own routing to `skip_decorator`, wrapped in
        # the same bare-or-factory discrimination as the rest -- being a plain *method* is
        # what made the bare `@pytest.mark.skip` replace the test with a closure (#136).
        def _skip_factory(reason: str | None = None) -> _SkipMarkDecorator:
            return _SkipMarkDecorator("skip", (), {"reason": reason})

        self._skip = _BareOrFactoryMark(
            "skip",
            _skip_factory,
            bare=_SkipMarkDecorator("skip", (), {"reason": None}),
        )

    def __getattr__(self, name: str) -> Any:
        """Delegate all mark.* access to rustest.mark.*"""
        return getattr(_rustest_mark, name)

    # Explicitly expose common marks for better IDE support
    @property
    def parametrize(self) -> Any:
        """Alias for @pytest.mark.parametrize (same as top-level parametrize)."""
        return _rustest_mark.parametrize

    @property
    def skip(self) -> Any:
        """Mark test as skipped, bare (``@pytest.mark.skip``) or called with a reason.

        Maps to rustest's ``skip_decorator()``.
        """
        return self._skip

    @property
    def skipif(self) -> Any:
        """Conditional skip decorator. Bare, it is an unconditional skip, as in pytest."""
        return _rustest_mark.skipif

    @property
    def xfail(self) -> Any:
        """Mark test as expected to fail. Usable bare or called."""
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
        ``marks`` is applied to **that parameter set alone**, as in pytest
        (`_pytest/mark/structures.py::ParameterSet.param`). It was accepted and ignored
        (with a warning) until Phase 4 Task 1.
    """
    return ParameterSet(values=values, id=id, marks=marks)


class WarningsChecker(_WarningsRecorder):
    """``pytest.warns()`` — a :class:`rustest._warnings.WarningsRecorder` that also asserts.

    **Subclassing the recorder is pytest's own arrangement** (`_pytest/recwarn.py` l. 258:
    ``class WarningsChecker(WarningsRecorder)``) and it is what makes ``warns`` and
    ``recwarn`` the same object with the same API. Phase 4 Task 1c unified them; before it,
    ``warns`` carried a private ``catch_warnings`` of its own and ``recwarn`` did not exist,
    so "a recorded warning" had two independent definitions and only one of them had a
    ``.list``, a ``.pop()`` or a ``.clear()``.

    One consequence is visible to callers and is the pytest-correct direction:
    ``with warns(...) as rec`` now binds the **recorder**, not the raw list. Everything the
    list supported still works (``len(rec)``, ``rec[0].message``, iteration), and
    ``rec.list`` / ``rec.pop(SomeWarning)`` work in addition.

    The assertion half below is rustest's own and is deliberately left as it was: its
    messages are pinned by the corpus and by seventeen real suites, and pytest's
    ``WarningsChecker.__exit__`` additionally *re-emits* unmatched warnings, which is a
    behaviour no ledgered suite depends on.
    """

    def __init__(
        self,
        expected_warning: type[Warning] | tuple[type[Warning], ...] | None = None,
        match: str | None = None,
    ):
        super().__init__()
        self.expected_warning = expected_warning
        self.match = match

    @property
    def _records(self) -> list[Any]:
        """The recorder's list, under the name this class's assertion half already used."""
        return cast("list[Any]", self.list)

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        records = list(self._records)
        super().__exit__(exc_type, exc_val, exc_tb)

        # If there was an exception, don't check warnings
        if exc_type is not None:
            return

        # If no expected warning specified, just return the records
        if self.expected_warning is None:
            return

        # Check that at least one matching warning was raised
        matching_warnings: list[Any] = []
        for record in records:
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

            if records:
                actual = ", ".join(f"{r.category.__name__}({r.message!s})" for r in records)
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
    exc_type: type[ImportError] | None = None,
) -> Any:
    """Port of `_pytest/outcomes.py::importorskip` (pytest 8.4.2, l. 208-317).

    Rewritten from a paraphrase into a port in Phase 4 Task 1c, because the paraphrase's
    one structural difference cost two whole suites. **The skip it raises must be a
    module-level skip.** pytest's is ``Skipped(reason, allow_module_level=True)`` (l. 285 and
    l. 313); rustest called its own ``skip()``, which defaults the flag to ``False``, so the
    overwhelmingly common shape::

        np = pytest.importorskip("numpy")   # at module scope

    raised a *non*-module-level skip during collection.  That is a collection error, and a
    collection error aborts the session -- Pillow and FastAPI both ended at 0 tests
    collected (Task 1b sweep, §5 M7).

    Four other differences from the paraphrase, all of them pytest's behaviour:

    * ``exc_type`` defaults to ``None``, not ``ImportError``.  ``None`` means "catch
      ``ImportError`` **and** warn if what we caught was not a ``ModuleNotFoundError``" --
      pytest's #11523 deprecation, where a module that exists but raises on import looks
      identical to one that is absent.  Passing ``exc_type=ImportError`` explicitly is how a
      caller silences that, and it could not be expressed at all while ``None`` was
      unrepresentable.
    * the warning is raised **outside** the ``catch_warnings`` block, as pytest is careful to
      do (l. 258-259, 299-300): raising it inside would have it swallowed by the very
      ``simplefilter("ignore")`` that is there to suppress ``ImportWarning`` from namespace
      directories.
    * the module comes from ``sys.modules[modname]`` after ``__import__``, not from
      ``importlib.import_module``'s return value.  For a dotted name those differ:
      ``__import__("a.b")`` returns ``a``, and pytest's ``sys.modules`` lookup is what makes
      ``pytest.importorskip("os.path")`` return ``os.path``.  (``import_module`` also returns
      the leaf, so this direction is unchanged -- it is written pytest's way so the two
      cannot drift.)
    * a ``minversion`` miss and a missing ``__version__`` are **one** branch with one message
      (``module 'x' has __version__ None, required is: '1.0'``), and it ignores ``reason``.
      The paraphrase had three branches, two messages, and let ``reason`` override them.

    ``packaging.version.Version`` is imported lazily inside the ``minversion`` branch, as
    pytest does (l. 309-310), so the common no-version call pays nothing for it.
    """
    import warnings

    __tracebackhide__ = True
    compile(modname, "", "eval")  # to catch syntaxerrors

    if exc_type is None:
        exc_type = ImportError
        warn_on_import_error = True
    else:
        warn_on_import_error = False

    skipped: _rustest_Skipped | None = None
    warning: Warning | None = None

    with warnings.catch_warnings():
        # Ignore ImportWarnings raised by a directory that shares the module's name but has
        # no `__init__.py` (pytest l. 274-277).
        warnings.simplefilter("ignore")

        try:
            __import__(modname)
        except exc_type as exc:
            if reason is None:
                reason = f"could not import {modname!r}: {exc}"
            skipped = _rustest_Skipped(reason, allow_module_level=True)

            if warn_on_import_error and not isinstance(exc, ModuleNotFoundError):
                lines = [
                    "",
                    f"Module '{modname}' was found, but when imported by pytest it raised:",
                    f"    {exc!r}",
                    "In pytest 9.1 this warning will become an error by default.",
                    "You can fix the underlying problem, or alternatively overwrite this"
                    + " behavior and silence this warning by passing exc_type=ImportError"
                    + " explicitly.",
                    "See https://docs.pytest.org/en/stable/deprecations.html"
                    + "#pytest-importorskip-default-behavior-regarding-importerror",
                ]
                warning = DeprecationWarning("\n".join(lines))

    if warning:
        warnings.warn(warning, stacklevel=2)
    if skipped:
        raise skipped

    mod = sys.modules[modname]
    if minversion is None:
        return mod
    verattr = getattr(mod, "__version__", None)
    # Imported lazily to improve start-up time, exactly as pytest does.
    from packaging.version import Version

    if verattr is None or Version(verattr) < Version(minversion):
        raise _rustest_Skipped(
            f"module {modname!r} has __version__ {verattr!r}, required is: {minversion!r}",
            allow_module_level=True,
        )
    return mod


def install_pytest_stubs() -> None:
    """
    Install _pytest stub modules for compatibility with projects that import from _pytest.

    This allows common imports like:
      from _pytest import monkeypatch
      from _pytest.config import Config
      from _pytest.outcomes import Failed, Skipped
    to work without ModuleNotFoundError, while showing deprecation warnings.

    This should only be called when --pytest-compat mode is explicitly enabled.
    """
    import sys

    # Check if pytest is already imported (meaning pytest is the runner, not rustest)
    _pytest_is_real = "_pytest" in sys.modules and hasattr(sys.modules.get("_pytest"), "__path__")

    if not _pytest_is_real:
        # Install our stub modules only if pytest is not running
        try:
            from rustest import _pytest_stub

            sys.modules["_pytest"] = _pytest_stub
            sys.modules["_pytest.monkeypatch"] = _pytest_stub.monkeypatch
            sys.modules["_pytest.config"] = _pytest_stub.config
            sys.modules["_pytest.outcomes"] = _pytest_stub.outcomes
            sys.modules["_pytest.nodes"] = _pytest_stub.nodes
            sys.modules["_pytest.mark"] = _pytest_stub.mark
            sys.modules["_pytest.mark.structures"] = _pytest_stub.mark.structures
            sys.modules["_pytest.assertion"] = _pytest_stub.assertion
            sys.modules["_pytest.assertion.rewrite"] = _pytest_stub.assertion.rewrite
            sys.modules["_pytest.main"] = _pytest_stub.main
        except ImportError as e:
            # _pytest_stub not available (shouldn't happen in normal operation)
            import warnings

            warnings.warn(
                (
                    f"Failed to install _pytest stub modules: {e}. "
                    "Tests that import from _pytest will fail. "
                    "This is an internal rustest error - please report it."
                ),
                RuntimeWarning,
                stacklevel=2,
            )


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
