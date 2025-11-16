"""High level Python API wrapping the Rust extension."""

from __future__ import annotations

from collections.abc import Sequence

from . import rust
from .reporting import RunReport


def run(
    *,
    paths: Sequence[str],
    pattern: str | None = None,
    mark_expr: str | None = None,
    workers: int | None = None,
    capture_output: bool = True,
    enable_codeblocks: bool = True,
    last_failed_mode: str = "none",
    fail_fast: bool = False,
    pytest_compat: bool = False,
    verbose: bool = False,
    ascii: bool = False,
    no_color: bool = False,
) -> RunReport:
    """Execute tests and return a rich report.

    Args:
        paths: Files or directories to collect tests from
        pattern: Substring to filter tests by (case insensitive)
        mark_expr: Mark expression to filter tests (e.g., "slow", "not slow", "slow and integration")
        workers: Number of worker slots to use (experimental)
        capture_output: Whether to capture stdout/stderr during test execution
        enable_codeblocks: Whether to enable code block tests from markdown files
        last_failed_mode: Last failed mode: "none", "only", or "first"
        fail_fast: Exit instantly on first error or failed test
        pytest_compat: Enable pytest compatibility mode (intercept 'import pytest')
        verbose: Show verbose output with hierarchical test structure
        ascii: Use ASCII characters instead of Unicode symbols for output
        no_color: Disable colored output
    """
    raw_report = rust.run(
        paths=list(paths),
        pattern=pattern,
        mark_expr=mark_expr,
        workers=workers,
        capture_output=capture_output,
        enable_codeblocks=enable_codeblocks,
        last_failed_mode=last_failed_mode,
        fail_fast=fail_fast,
        pytest_compat=pytest_compat,
        verbose=verbose,
        ascii=ascii,
        no_color=no_color,
    )
    return RunReport.from_py(raw_report)
