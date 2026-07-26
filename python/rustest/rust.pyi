"""Type stubs for the rustest Rust extension module."""

from __future__ import annotations

from typing import Sequence

# Event classes
class SuiteStartedEvent:
    """Event emitted when test suite starts."""

    total_files: int
    total_tests: int
    timestamp: float

class SuiteCompletedEvent:
    """Event emitted when test suite completes."""

    passed: int
    failed: int
    skipped: int
    errors: int
    duration: float
    timestamp: float

class FileStartedEvent:
    """Event emitted when a test file starts."""

    file_path: str
    total_tests: int
    timestamp: float

class FileCompletedEvent:
    """Event emitted when a test file completes."""

    file_path: str
    passed: int
    failed: int
    skipped: int
    duration: float
    timestamp: float

class TestCompletedEvent:
    """Event emitted when a test completes."""

    test_id: str
    file_path: str
    test_name: str
    status: str
    duration: float
    message: str | None
    timestamp: float

class CollectionErrorEvent:
    """Event emitted when a collection error occurs."""

    path: str
    message: str
    timestamp: float

class CollectionStartedEvent:
    """Event emitted when test collection starts."""

    timestamp: float

class CollectionProgressEvent:
    """Event emitted when a file is collected during test discovery."""

    file_path: str
    tests_collected: int
    files_collected: int
    timestamp: float

class CollectionCompletedEvent:
    """Event emitted when test collection completes."""

    total_files: int
    total_tests: int
    duration: float
    timestamp: float

class PyTestResult:
    """Individual test result from the Rust extension."""

    name: str
    path: str
    status: str
    duration: float
    message: str | None
    stdout: str | None
    stderr: str | None

class CollectionError:
    """Error that occurred during test collection (e.g., syntax error, import error)."""

    path: str
    message: str

class PyRunReport:
    """Test run report from the Rust extension."""

    total: int
    passed: int
    failed: int
    skipped: int
    duration: float
    results: list[PyTestResult]
    collection_errors: list[CollectionError]

def run(
    paths: Sequence[str],
    pattern: str | None = ...,
    mark_expr: str | None = ...,
    workers: int | None = ...,
    capture_output: bool = ...,
    enable_codeblocks: bool = ...,
    last_failed_mode: str = ...,
    fail_fast: bool = ...,
    pytest_compat: bool = ...,
    verbose: bool = ...,
    ascii: bool = ...,
    no_color: bool = ...,
    event_callback: object | None = ...,
) -> PyRunReport:
    """Execute tests and return a report."""
    ...

def getfixturevalue(name: str) -> object:
    """Resolve a fixture through the active test resolver."""
    ...

def v2_resolve_config(invocation_dir: str, args: Sequence[str]) -> str:
    """Resolve the v2 rootdir + ini configuration, returned as a JSON object string.

    Internal debug surface for the v2 core (see ``src/v2/py.rs``). ``invocation_dir`` must
    be absolute. The JSON object has the keys ``rootdir`` (absolute posix path),
    ``config_file`` (absolute posix path or ``null``), ``testpaths``, ``python_files``,
    ``python_classes``, ``python_functions``, ``norecursedirs``, ``addopts`` and
    ``markers``. Raises ``ValueError`` for a relative ``invocation_dir`` or an unusable
    config file.
    """
    ...

def v2_collect(
    invocation_dir: str,
    args: Sequence[str],
    python_executable: str,
    workers: int,
) -> str:
    """Collect tests with the v2 engine; returns a ``CollectionManifest`` JSON string.

    Backs ``rustest --v2-collect-only`` (see ``python/rustest/core.py``). ``invocation_dir``
    must be absolute; ``args`` are raw CLI path arguments, and an empty list lets
    ``testpaths`` decide the roots. ``python_executable`` is the interpreter the collection
    workers run under (``sys.executable``) -- the Rust side never guesses one.
    ``workers`` is the pool size, clamped to ``[1, number of files]``.

    The JSON object is the manifest frozen in ``src/v2/manifest.rs``: ``schema_version``,
    ``rootdir`` (absolute posix), ``tests`` (each with ``id``, ``path``, ``qualname`` and
    optional ``class_name``/``param_id``/``marks``/``fixtures``) and ``errors`` (omitted
    when empty; each with ``path`` and ``message``).

    Raises ``ValueError`` for a usage error (a bad ``invocation_dir``, a path argument that
    does not exist, or an unusable config file) and ``RuntimeError`` for an orchestration
    failure. A test file that fails to import raises nothing -- it is data in ``errors``.
    """
    ...
