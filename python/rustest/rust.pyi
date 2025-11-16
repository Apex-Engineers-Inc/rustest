"""Type stubs for the rustest Rust extension module."""

from __future__ import annotations

from typing import Sequence

class PyTestResult:
    """Individual test result from the Rust extension."""

    name: str
    path: str
    status: str
    duration: float
    message: str | None
    stdout: str | None
    stderr: str | None

class PyRunReport:
    """Test run report from the Rust extension."""

    total: int
    passed: int
    failed: int
    skipped: int
    duration: float
    results: list[PyTestResult]

def run(
    paths: Sequence[str],
    pattern: str | None,
    mark_expr: str | None,
    workers: int | None,
    capture_output: bool,
    enable_codeblocks: bool,
    last_failed_mode: str,
    fail_fast: bool,
    pytest_compat: bool,
    verbose: bool,
    ascii: bool,
    no_color: bool,
) -> PyRunReport:
    """Execute tests and return a report."""
    ...
