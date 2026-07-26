"""High level Python API wrapping the Rust extension."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from typing import NotRequired, TypedDict, cast

from rich.console import Console
from rich.panel import Panel

from . import rust
from .event_router import EventRouter
from .renderers import RichRenderer
from .reporting import RunReport

# pytest's exit codes (`_pytest.config.ExitCode`), which the v2 engine adopts verbatim --
# "contracts are pytest's" is the v2 spec's rule. Verified against pytest 8.4.2 by running
# `pytest --collect-only -q` on each shape rather than transcribed from memory:
#   * a tree with tests            -> 0
#   * an empty tree                -> 5   (NO_TESTS_COLLECTED)
#   * one unimportable file        -> 2   (INTERRUPTED -- even when other files collected)
#   * a path argument that is gone -> 4   (USAGE_ERROR, with `ERROR: ...` on stderr)
_EXIT_OK = 0
_EXIT_INTERRUPTED = 2
_EXIT_INTERNAL_ERROR = 3
_EXIT_USAGE_ERROR = 4
_EXIT_NO_TESTS_COLLECTED = 5


class _ManifestTest(TypedDict):
    """The one manifest field the collect-only surface reads. See `src/v2/manifest.rs`."""

    id: str


class _ManifestError(TypedDict):
    path: str
    message: str


class _Manifest(TypedDict):
    """`errors` is omitted entirely when empty -- an omit-when-empty wire rule, not a bug."""

    tests: list[_ManifestTest]
    errors: NotRequired[list[_ManifestError]]


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


def _summary(collected: int, errors: int) -> str:
    """The one-line stderr summary, in pytest's wording (`N tests collected, M errors`)."""
    line = f"{collected} {'test' if collected == 1 else 'tests'} collected"
    if errors:
        line += f", {errors} {'error' if errors == 1 else 'errors'}"
    return line


def v2_collect_only(*, paths: Sequence[str], workers: int | None = None) -> int:
    """Collect with the **v2** engine, print node ids, and return pytest's exit code.

    This is the whole of ``rustest --v2-collect-only``. It runs the v2 spine end to end
    (config resolution -> file walk -> worker pool -> manifest) and never touches the v1
    discovery or execution path.

    Output is shaped so stdout is a machine-readable node id list:

    * **stdout** -- one node id per line, in manifest order (which is pytest's collection
      order). Nothing else, ever: no summary, no banner, no blank lines.
    * **stderr** -- ``ERROR collecting <path>`` plus the indented message for each file
      that failed to import, then the ``N tests collected`` summary.

    That split is a deliberate departure from pytest, which puts its summary and its
    ``ERRORS`` section on stdout; the ids are the payload here and everything else is
    diagnostics.

    ``sys.executable`` is resolved *here* and handed to the Rust orchestrator, so the
    workers always run under the interpreter the user invoked -- the Rust side never
    guesses an interpreter, which would silently collect against the wrong environment.

    Args:
        paths: Files or directories to collect from. An **empty** sequence means "no path
            argument was given", which is what lets ``testpaths`` decide the roots exactly
            as it does under pytest; passing ``["."]`` is an explicit argument and
            suppresses ``testpaths``.
        workers: Collection pool size. ``None`` (and any non-positive value) means one
            worker per CPU; the Rust side clamps the pool to the number of files found.

    Returns:
        0 with tests, 5 with none, 2 when any file failed to import, 4 for a usage error
        (a missing path argument or an unusable config file) and 3 for an orchestration
        failure.
    """
    pool_size = workers if workers is not None and workers > 0 else (os.cpu_count() or 1)

    try:
        payload = rust.v2_collect(os.getcwd(), list(paths), sys.executable, pool_size)
    except ValueError as exc:
        # pytest's UsageError shape, including its `ERROR: file or directory not found: x`
        # wording, which the Rust side produces verbatim.
        print(f"ERROR: {exc}", file=sys.stderr)
        return _EXIT_USAGE_ERROR
    except RuntimeError as exc:
        # The pool itself failed (a worker died, protocol drift, an unusable interpreter).
        # Loud and distinct from a user error -- never a quietly empty collection.
        print(f"INTERNALERROR: {exc}", file=sys.stderr)
        return _EXIT_INTERNAL_ERROR

    manifest = cast(_Manifest, json.loads(payload))
    tests = manifest["tests"]
    errors = manifest.get("errors", [])

    for test in tests:
        print(test["id"])
    for error in errors:
        print(f"ERROR collecting {error['path']}", file=sys.stderr)
        for line in error["message"].splitlines():
            print(f"  {line}", file=sys.stderr)
    print(_summary(len(tests), len(errors)), file=sys.stderr)

    if errors:
        return _EXIT_INTERRUPTED
    if not tests:
        return _EXIT_NO_TESTS_COLLECTED
    return _EXIT_OK


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
    # Store runtime configuration for fixtures to access
    try:
        import rustest._runtime_config as _runtime_config
    except ModuleNotFoundError:
        # Fallback for when rustest is not recognized as a package (e.g., during testing)
        from . import _runtime_config

    _runtime_config.set_runtime_config(
        verbose=1 if verbose else 0,  # Convert bool to int (could be expanded to levels)
        capture="no" if not capture_output else "fd",
        pytest_compat=pytest_compat,
        ascii=ascii,
        no_color=no_color,
        workers=workers,
        fail_fast=fail_fast,
    )

    # Print pytest compatibility banner and install _pytest stubs if enabled
    if pytest_compat:
        _print_pytest_compat_banner(use_colors=not no_color)
        # Install _pytest stub modules for compatibility
        from rustest.compat.pytest import install_pytest_stubs

        install_pytest_stubs()

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
