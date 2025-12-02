"""High level Python API wrapping the Rust extension."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from rich.console import Console
from rich.panel import Panel

from . import rust
from .event_router import EventRouter
from .renderers import RichRenderer
from .reporting import RunReport


def _print_pytest_compat_banner(use_colors: bool) -> None:
    """Print the pytest compatibility mode banner using rich.

    Args:
        use_colors: Whether to use colored output
    """
    console = Console(force_terminal=use_colors, file=sys.stderr)

    banner_text = (
        "[bold]Running pytest tests with rustest.[/bold]\n\n"
        "[cyan]Supported:[/cyan] fixtures, parametrize, marks, approx\n"
        "[cyan]Built-ins:[/cyan] tmp_path, tmpdir, monkeypatch, request\n"
        "[cyan]pytest_asyncio:[/cyan] Translated to native async support\n\n"
        "[dim]NOTE: Other plugin APIs are stubbed (non-functional).\n"
        "pytest-asyncio fixtures work via rustest native async.[/dim]\n\n"
        "[bold]For full features, use native rustest:[/bold]\n"
        "  [cyan]from rustest import fixture, mark, ...[/cyan]"
    )

    console.print(
        Panel(
            banner_text,
            title="[bold yellow]RUSTEST PYTEST COMPATIBILITY MODE[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    console.print()  # Add blank line after banner


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
    # Print pytest compatibility banner if enabled
    if pytest_compat:
        _print_pytest_compat_banner(use_colors=not no_color)

    # Set up event routing with rich terminal renderer
    router = EventRouter()
    rich_renderer = RichRenderer(use_colors=not no_color, use_ascii=ascii)
    router.subscribe(rich_renderer)

    previous_running = os.environ.get("RUSTEST_RUNNING")
    os.environ["RUSTEST_RUNNING"] = "1"
    try:
        # Run tests with event callback
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
            event_callback=router.emit,
        )
    finally:
        if previous_running is None:
            os.environ.pop("RUSTEST_RUNNING", None)
        else:
            os.environ["RUSTEST_RUNNING"] = previous_running

    return RunReport.from_py(raw_report)
