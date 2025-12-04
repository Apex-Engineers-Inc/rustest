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
