"""Builtin fixtures that mirror a subset of pytest's default fixtures."""

# pyright: reportMissingImports=false

from __future__ import annotations

import importlib
import itertools
import os
import shutil
import sys
import tempfile
from collections.abc import Generator, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Iterator, NamedTuple, cast

from .decorators import fixture


class CaptureResult(NamedTuple):
    """Result of capturing stdout and stderr."""

    out: str
    err: str


py: ModuleType | None
try:  # pragma: no cover - optional dependency at runtime
    import py as _py_module
except Exception:  # pragma: no cover - import error reported at fixture usage time
    py = None
else:
    py = _py_module

if TYPE_CHECKING:
    try:  # pragma: no cover - typing-only import
        from py import path as _py_path  # type: ignore[reportMissingModuleSource]
    except ImportError:
        PyPathLocal = Any
    else:
        PyPathLocal = _py_path.local

else:  # pragma: no cover - imported only for typing
    PyPathLocal = Any


class _NotSet:
    """Sentinel value for tracking missing attributes/items."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<NOTSET>"


_NOT_SET = _NotSet()


class MonkeyPatch:
    """Lightweight re-implementation of :class:`pytest.MonkeyPatch`."""

    def __init__(self) -> None:
        super().__init__()
        self._setattrs: list[tuple[object, str, object | _NotSet]] = []
        self._setitems: list[tuple[MutableMapping[Any, Any], Any, object | _NotSet]] = []
        self._environ: list[tuple[str, str | _NotSet]] = []
        self._syspath_prepend: list[str] = []
        self._cwd_original: str | None = None

    @classmethod
    @contextmanager
    def context(cls) -> Generator[MonkeyPatch, None, None]:
        patch = cls()
        try:
            yield patch
        finally:
            patch.undo()

    def setattr(
        self,
        target: object | str,
        name: object | str = _NOT_SET,
        value: object = _NOT_SET,
        *,
        raising: bool = True,
    ) -> None:
        if value is _NOT_SET:
            if not isinstance(target, str):
                raise TypeError("use setattr(target, name, value) or setattr('module.attr', value)")
            module_path, attr_name = target.rsplit(".", 1)
            module = importlib.import_module(module_path)
            obj = module
            attr_value = name
            if attr_value is _NOT_SET:
                raise TypeError("value must be provided when using dotted path syntax")
            attr_name = attr_name
        else:
            if not isinstance(name, str):
                raise TypeError("attribute name must be a string")
            obj = target
            attr_name = name
            attr_value = value

        original = getattr(obj, attr_name, _NOT_SET)
        if original is _NOT_SET and raising:
            raise AttributeError(f"{attr_name!r} not found for patching")

        setattr(obj, attr_name, attr_value)
        self._setattrs.append((obj, attr_name, original))

    def delattr(
        self, target: object | str, name: str | _NotSet = _NOT_SET, *, raising: bool = True
    ) -> None:
        if isinstance(target, str) and name is _NOT_SET:
            module_path, attr_name = target.rsplit(".", 1)
            module = importlib.import_module(module_path)
            obj = module
            attr_name = attr_name
        else:
            if not isinstance(name, str):
                raise TypeError("attribute name must be a string")
            obj = target
            attr_name = name

        original = getattr(obj, attr_name, _NOT_SET)
        if original is _NOT_SET:
            if raising:
                raise AttributeError(f"{attr_name!r} not found for deletion")
            return

        delattr(obj, attr_name)
        self._setattrs.append((obj, attr_name, original))

    def setitem(self, mapping: MutableMapping[Any, Any], key: Any, value: Any) -> None:
        original = mapping.get(key, _NOT_SET)
        mapping[key] = value
        self._setitems.append((mapping, key, original))

    def delitem(self, mapping: MutableMapping[Any, Any], key: Any, *, raising: bool = True) -> None:
        if key not in mapping:
            if raising:
                raise KeyError(key)
            self._setitems.append((mapping, key, _NOT_SET))
            return

        original = mapping[key]
        del mapping[key]
        self._setitems.append((mapping, key, original))

    def setenv(self, name: str, value: Any, prepend: str | None = None) -> None:
        str_value = str(value)
        if prepend and name in os.environ:
            str_value = f"{str_value}{prepend}{os.environ[name]}"
        original = os.environ.get(name)
        os.environ[name] = str_value
        stored_original: str | _NotSet = original if original is not None else _NOT_SET
        self._environ.append((name, stored_original))

    def delenv(self, name: str, *, raising: bool = True) -> None:
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            self._environ.append((name, _NOT_SET))
            return

        original = os.environ.pop(name)
        self._environ.append((name, original))

    def syspath_prepend(self, path: os.PathLike[str] | str) -> None:
        str_path = os.fspath(path)
        if str_path in sys.path:
            return
        sys.path.insert(0, str_path)
        self._syspath_prepend.append(str_path)

    def chdir(self, path: os.PathLike[str] | str) -> None:
        if self._cwd_original is None:
            self._cwd_original = os.getcwd()
        os.chdir(os.fspath(path))

    def undo(self) -> None:
        for obj, attr_name, original in reversed(self._setattrs):
            if original is _NOT_SET:
                try:
                    delattr(obj, attr_name)
                except AttributeError:  # pragma: no cover - defensive
                    pass
            else:
                setattr(obj, attr_name, original)
        self._setattrs.clear()

        for mapping, key, original in reversed(self._setitems):
            if original is _NOT_SET:
                mapping.pop(key, None)
            else:
                mapping[key] = original
        self._setitems.clear()

        for name, original in reversed(self._environ):
            if original is _NOT_SET:
                os.environ.pop(name, None)
            else:
                os.environ[name] = cast(str, original)
        self._environ.clear()

        while self._syspath_prepend:
            str_path = self._syspath_prepend.pop()
            try:
                sys.path.remove(str_path)
            except ValueError:  # pragma: no cover - path already removed externally
                pass

        if self._cwd_original is not None:
            os.chdir(self._cwd_original)
            self._cwd_original = None


class TmpPathFactory:
    """Create temporary directories using :class:`pathlib.Path`."""

    def __init__(self, prefix: str = "tmp_path") -> None:
        super().__init__()
        self._base = Path(tempfile.mkdtemp(prefix=f"rustest-{prefix}-"))
        self._counter = itertools.count()
        self._created: list[Path] = []

    def mktemp(self, basename: str, *, numbered: bool = True) -> Path:
        if not basename:
            raise ValueError("basename must be a non-empty string")
        if numbered:
            suffix = next(self._counter)
            name = f"{basename}{suffix}"
        else:
            name = basename
        path = self._base / name
        path.mkdir(parents=True, exist_ok=False)
        self._created.append(path)
        return path

    def getbasetemp(self) -> Path:
        return self._base

    def cleanup(self) -> None:
        for path in reversed(self._created):
            shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(self._base, ignore_errors=True)
        self._created.clear()


class TmpDirFactory:
    """Wrapper that exposes ``py.path.local`` directories."""

    def __init__(self, path_factory: TmpPathFactory) -> None:
        super().__init__()
        self._factory = path_factory

    def mktemp(self, basename: str, *, numbered: bool = True) -> Any:
        if py is None:  # pragma: no cover - exercised only when dependency missing
            raise RuntimeError("py library is required for tmpdir fixtures")
        path = self._factory.mktemp(basename, numbered=numbered)
        return py.path.local(path)

    def getbasetemp(self) -> Any:
        if py is None:  # pragma: no cover - exercised only when dependency missing
            raise RuntimeError("py library is required for tmpdir fixtures")
        return py.path.local(self._factory.getbasetemp())

    def cleanup(self) -> None:
        self._factory.cleanup()


@fixture(scope="session")
def tmp_path_factory() -> Iterator[TmpPathFactory]:
    factory = TmpPathFactory("tmp_path")
    try:
        yield factory
    finally:
        factory.cleanup()


@fixture(scope="function")
def tmp_path(tmp_path_factory: TmpPathFactory) -> Iterator[Path]:
    path = tmp_path_factory.mktemp("tmp_path")
    yield path


@fixture(scope="session")
def tmpdir_factory() -> Iterator[TmpDirFactory]:
    factory = TmpDirFactory(TmpPathFactory("tmpdir"))
    try:
        yield factory
    finally:
        factory.cleanup()


@fixture(scope="function")
def tmpdir(tmpdir_factory: TmpDirFactory) -> Iterator[Any]:
    yield tmpdir_factory.mktemp("tmpdir")


@fixture(scope="function")
def monkeypatch() -> Iterator[MonkeyPatch]:
    patch = MonkeyPatch()
    try:
        yield patch
    finally:
        patch.undo()


@fixture(scope="function")
def request() -> Any:
    """Pytest-compatible request fixture for fixture parametrization.

    This fixture provides access to the current fixture parameter value via
    request.param when using parametrized fixtures.

    **Supported:**
        - request.param: Current parameter value for parametrized fixtures
        - request.scope: Returns "function"
        - Type annotations: request: pytest.FixtureRequest

    **Not supported (returns None or raises NotImplementedError):**
        - request.node, function, cls, module, config
        - request.fixturename
        - Methods: addfinalizer(), getfixturevalue()

    Example:
        @fixture(params=[1, 2, 3])
        def number(request):
            return request.param

        @fixture(params=["mysql", "postgres"], ids=["MySQL", "PostgreSQL"])
        def database(request):
            return create_db(request.param)
    """
    # NOTE: This fixture is not directly called in normal usage.
    # Instead, the Rust execution engine creates FixtureRequest objects
    # with the appropriate param value and injects them directly.
    # This fixture definition exists for fallback and API compatibility.
    from rustest.compat.pytest import FixtureRequest

    return FixtureRequest()


class CaptureFixture:
    """Fixture to capture stdout and stderr.

    This implements pytest's capsys fixture functionality.
    """

    def __init__(self) -> None:
        import io

        super().__init__()
        self._capture_out: list[str] = []
        self._capture_err: list[str] = []
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._capturing = False
        self._stdout_buffer: io.StringIO = io.StringIO()
        self._stderr_buffer: io.StringIO = io.StringIO()

    def start_capture(self) -> None:
        """Start capturing stdout and stderr."""
        import io

        self._stdout_buffer = io.StringIO()
        self._stderr_buffer = io.StringIO()
        sys.stdout = self._stdout_buffer
        sys.stderr = self._stderr_buffer
        self._capturing = True

    def stop_capture(self) -> None:
        """Stop capturing and restore original streams."""
        if self._capturing:
            sys.stdout = self._original_stdout
            sys.stderr = self._original_stderr
            self._capturing = False

    def readouterr(self) -> CaptureResult:
        """Read and reset the captured output.

        Returns:
            A CaptureResult with out and err attributes containing the captured output.
        """
        if not self._capturing:
            return CaptureResult("", "")

        out = self._stdout_buffer.getvalue()
        err = self._stderr_buffer.getvalue()

        # Reset the buffers
        import io

        self._stdout_buffer = io.StringIO()
        self._stderr_buffer = io.StringIO()
        sys.stdout = self._stdout_buffer
        sys.stderr = self._stderr_buffer

        return CaptureResult(out, err)

    def __enter__(self) -> "CaptureFixture":
        self.start_capture()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop_capture()


@fixture
def capsys() -> Generator[CaptureFixture, None, None]:
    """
    Enable text capturing of stdout and stderr.

    The captured output is made available via capsys.readouterr() which
    returns a (out, err) tuple. out and err are strings containing the
    captured output.

    Example:
        def test_output(capsys):
            print("hello")
            captured = capsys.readouterr()
            assert captured.out == "hello\\n"
    """
    capture = CaptureFixture()
    capture.start_capture()
    try:
        yield capture
    finally:
        capture.stop_capture()


@fixture
def capfd() -> Generator[CaptureFixture, None, None]:
    """
    Enable text capturing of stdout and stderr at file descriptor level.

    Note: This is currently an alias for capsys in rustest.
    The captured output is made available via capfd.readouterr().
    """
    # For simplicity, capfd is implemented the same as capsys
    # A true file descriptor capture would require more complex handling
    capture = CaptureFixture()
    capture.start_capture()
    try:
        yield capture
    finally:
        capture.stop_capture()


class LogRecord(NamedTuple):
    """A captured log record."""

    name: str
    levelno: int
    levelname: str
    message: str
    pathname: str
    lineno: int
    exc_info: Any


class LogCaptureFixture:
    """Fixture to capture logging output.

    This implements pytest's caplog fixture functionality for capturing
    and inspecting log messages during test execution.
    """

    def __init__(self) -> None:
        import logging

        super().__init__()
        self._records: list[logging.LogRecord] = []
        self._handler: logging.Handler | None = None
        self._old_level: int | None = None
        self._logger = logging.getLogger()

    def start_capture(self) -> None:
        """Start capturing log messages."""
        import logging

        class ListHandler(logging.Handler):
            """Handler that collects log records in a list."""

            def __init__(self, records: list[logging.LogRecord]) -> None:
                super().__init__()
                self.records = records

            def emit(self, record: logging.LogRecord) -> None:
                self.records.append(record)

        self._handler = ListHandler(self._records)
        self._handler.setLevel(logging.DEBUG)
        self._logger.addHandler(self._handler)
        self._old_level = self._logger.level
        # Set to DEBUG to capture all messages
        self._logger.setLevel(logging.DEBUG)

    def stop_capture(self) -> None:
        """Stop capturing log messages."""
        if self._handler is not None:
            self._logger.removeHandler(self._handler)
            if self._old_level is not None:
                self._logger.setLevel(self._old_level)
            self._handler = None

    @property
    def records(self) -> list[Any]:
        """Access to the captured log records.

        Returns:
            A list of logging.LogRecord objects.
        """
        return self._records

    @property
    def record_tuples(self) -> list[tuple[str, int, str]]:
        """Get captured log records as tuples of (name, level, message).

        Returns:
            A list of tuples with (logger_name, level, message).
        """
        return [(r.name, r.levelno, r.getMessage()) for r in self._records]

    @property
    def messages(self) -> list[str]:
        """Get captured log messages as strings.

        Returns:
            A list of log message strings.
        """
        return [r.getMessage() for r in self._records]

    @property
    def text(self) -> str:
        """Get all captured log messages as a single text string.

        Returns:
            All log messages joined with newlines.
        """
        return "\n".join(self.messages)

    def clear(self) -> None:
        """Clear all captured log records."""
        self._records.clear()

    def set_level(self, level: int | str, logger: str | None = None) -> None:
        """Set the minimum log level to capture.

        Args:
            level: The log level (e.g., logging.INFO, "INFO", 20)
            logger: Optional logger name to set level for (default: root logger)
        """
        import logging

        if isinstance(level, str):
            level = getattr(logging, level.upper())

        if logger is None:
            target_logger = self._logger
        else:
            target_logger = logging.getLogger(logger)

        target_logger.setLevel(level)

    @contextmanager
    def at_level(
        self, level: int | str, logger: str | None = None
    ) -> Generator["LogCaptureFixture", None, None]:
        """Context manager to temporarily set the log level.

        Args:
            level: The log level to set
            logger: Optional logger name (default: root logger)

        Usage:
            with caplog.at_level(logging.INFO):
                # Only INFO and above will be captured here
                logging.debug("not captured")
                logging.info("captured")
        """
        import logging

        if isinstance(level, str):
            level = getattr(logging, level.upper())

        if logger is None:
            target_logger = self._logger
        else:
            target_logger = logging.getLogger(logger)

        old_level = target_logger.level
        target_logger.setLevel(level)
        try:
            yield self
        finally:
            target_logger.setLevel(old_level)

    def __enter__(self) -> "LogCaptureFixture":
        self.start_capture()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop_capture()


@fixture
def caplog() -> Generator[LogCaptureFixture, None, None]:
    """
    Enable capturing of logging output.

    The captured logging is made available via the fixture's attributes:
    - caplog.records: List of logging.LogRecord objects
    - caplog.record_tuples: List of (name, level, message) tuples
    - caplog.messages: List of message strings
    - caplog.text: All messages as a single string

    Example:
        def test_logging(caplog):
            import logging
            logging.info("hello")
            assert "hello" in caplog.text
            assert caplog.records[0].levelname == "INFO"

        def test_logging_level(caplog):
            import logging
            with caplog.at_level(logging.WARNING):
                logging.info("not captured")
                logging.warning("captured")
            assert len(caplog.records) == 1
    """
    capture = LogCaptureFixture()
    capture.start_capture()
    try:
        yield capture
    finally:
        capture.stop_capture()


class Cache:
    """Cache fixture for storing values between test runs.

    This implements pytest's cache fixture functionality for persisting
    data across test sessions. Data is stored in .rustest_cache/ directory.
    """

    def __init__(self, cache_dir: Path) -> None:
        super().__init__()
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "cache.json"
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load cache data from disk."""
        if self._cache_file.exists():
            try:
                import json

                with open(self._cache_file) as f:
                    self._data = json.load(f)
            except Exception:
                # If cache is corrupted, start fresh
                self._data = {}

    def _save(self) -> None:
        """Save cache data to disk."""
        try:
            import json

            with open(self._cache_file, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            # Silently ignore save errors
            pass

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the cache.

        Args:
            key: The cache key (should use "/" as separator, e.g., "myapp/version")
            default: Default value if key not found

        Returns:
            The cached value or default if not found
        """
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in the cache.

        Args:
            key: The cache key (should use "/" as separator, e.g., "myapp/version")
            value: The value to cache (must be JSON-serializable)
        """
        self._data[key] = value
        self._save()

    def mkdir(self, name: str) -> Path:
        """Create and return a directory inside the cache directory.

        Args:
            name: Name of the directory to create

        Returns:
            Path to the created directory
        """
        dir_path = self._cache_dir / name
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def makedir(self, name: str) -> Any:
        """Create and return a py.path.local directory inside cache.

        This is for pytest compatibility (uses py.path instead of pathlib).

        Args:
            name: Name of the directory to create

        Returns:
            py.path.local object for the directory
        """
        if py is None:  # pragma: no cover
            raise RuntimeError("py library is required for makedir()")
        dir_path = self.mkdir(name)
        return py.path.local(dir_path)

    def __contains__(self, key: str) -> bool:
        """Check if a key exists in the cache."""
        return key in self._data

    def __getitem__(self, key: str) -> Any:
        """Get a value from the cache (dict-style access)."""
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        """Set a value in the cache (dict-style access)."""
        self.set(key, value)


@fixture(scope="session")
def cache() -> Cache:
    """
    Provide access to a cache object that can persist between test sessions.

    The cache stores data in .rustest_cache/ directory and survives across
    test runs. This is useful for storing expensive computation results,
    version information, or implementing features like --lf (last-failed).

    The cache provides dict-like access and key/value methods:
    - cache.get(key, default=None): Get a value
    - cache.set(key, value): Set a value
    - cache[key]: Dict-style get
    - cache[key] = value: Dict-style set
    - key in cache: Check if key exists

    Cache keys should use "/" as separator (e.g., "myapp/version").

    Example:
        def test_expensive_computation(cache):
            result = cache.get("myapp/result")
            if result is None:
                result = expensive_computation()
                cache.set("myapp/result", result)
            assert result > 0

        def test_cache_version(cache):
            version = cache.get("myapp/version", "1.0.0")
            assert version >= "1.0.0"
    """
    # Find a suitable cache directory
    # Try current directory first, fall back to temp
    try:
        cache_dir = Path.cwd() / ".rustest_cache"
    except Exception:
        cache_dir = Path(tempfile.gettempdir()) / ".rustest_cache"

    return Cache(cache_dir)


__all__ = [
    "Cache",
    "CaptureFixture",
    "LogCaptureFixture",
    "MonkeyPatch",
    "TmpDirFactory",
    "TmpPathFactory",
    "cache",
    "caplog",
    "capsys",
    "capfd",
    "monkeypatch",
    "tmpdir",
    "tmpdir_factory",
    "tmp_path",
    "tmp_path_factory",
    "request",
]
