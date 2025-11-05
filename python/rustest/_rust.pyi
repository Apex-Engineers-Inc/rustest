"""Type stubs for the Rust extension module."""

from __future__ import annotations

from collections.abc import Sequence

class PyTestResult:
    """Test result from Rust layer."""

    name: str
    path: str
    status: str
    duration: float
    message: str | None
    stdout: str | None
    stderr: str | None

class PyRunReport:
    """Run report from Rust layer."""

    total: int
    passed: int
    failed: int
    skipped: int
    duration: float
    results: Sequence[PyTestResult]

def run(
    paths: list[str],
    pattern: str | None,
    workers: int | None,
    capture_output: bool,
) -> PyRunReport:
    """Run tests and return a report."""
    ...
