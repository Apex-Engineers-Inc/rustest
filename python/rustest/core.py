"""High level Python API wrapping the Rust extension."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

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
    deselected: NotRequired[int]


class _ReportSummary(TypedDict):
    """The schema-v2 summary. Six status buckets, not v1's three. See `src/v2/execute.rs`."""

    total: int
    passed: int
    failed: int
    skipped: int
    xfailed: int
    xpassed: int
    error: int
    deselected: int
    duration: float


class _ReportTest(TypedDict):
    id: str
    status: str
    duration: float
    message: NotRequired[str]
    stdout: NotRequired[str]
    stderr: NotRequired[str]


class _RunReport(TypedDict):
    version: int
    rootdir: str
    exit_code: int
    summary: _ReportSummary
    tests: list[_ReportTest]
    collection_errors: list[_ManifestError]
    teardown_errors: NotRequired[list[str]]
    worker_stderr: NotRequired[list[str]]


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


def _summary(collected: int, errors: int, deselected: int = 0) -> str:
    """The one-line stderr summary, in pytest's wording (`N tests collected, M errors`)."""
    line = f"{collected} {'test' if collected == 1 else 'tests'} collected"
    if deselected:
        line += f", {deselected} deselected"
    if errors:
        line += f", {errors} {'error' if errors == 1 else 'errors'}"
    return line


def _run_summary(summary: _ReportSummary, collection_errors: int = 0) -> str:
    """The one-line stderr summary for a run, in pytest's wording and bucket order.

    The order is ``_pytest/terminal.py::KNOWN_TYPES`` (l. 63-72) and it is **failed-first**::

        failed, passed, skipped, deselected, xfailed, xpassed, warnings, error

    Probed rather than transcribed: a run with one of each prints
    ``1 failed, 1 passed, 1 skipped, 1 deselected, 1 xfailed, 1 xpassed, 1 error``.
    ``warnings`` is absent here because v2 has no warnings channel yet; every other bucket
    keeps its position, so adding one later does not reshuffle the line.

    Zero buckets are omitted, exactly as pytest omits them -- a run with no xfails should
    not have to say ``0 xfailed``. An entirely empty tally becomes ``no tests ran``, which
    is pytest's own wording for the exit-5 shape.

    ``collection_errors`` folds into the ``error`` bucket because **pytest puts them there**:
    a file that fails to import is reported as ``1 error``, the same words a broken fixture
    gets. Keeping them out would print ``no tests ran`` for a run that was interrupted by a
    broken import -- the wording for an *empty tree*, and the single most misleading thing
    this line could say about an exit-2 run. The JSON report keeps the two apart
    (``summary.error`` is a bucket over ``tests``; ``collection_errors`` is its own list),
    since a machine reader can afford the distinction and a terminal line cannot.

    ``error`` is spelled by count (pytest writes ``1 error`` but ``2 errors``); every other
    bucket has one spelling. The buckets are listed literally rather than looked up from a
    table because ``_ReportSummary`` is a ``TypedDict``, whose keys must be literals for the
    type checker to see them at all.
    """
    errors = summary["error"] + collection_errors
    counts = (
        (summary["failed"], "failed"),
        (summary["passed"], "passed"),
        (summary["skipped"], "skipped"),
        (summary["deselected"], "deselected"),
        (summary["xfailed"], "xfailed"),
        (summary["xpassed"], "xpassed"),
        (errors, "error" if errors == 1 else "errors"),
    )
    parts = [f"{count} {label}" for count, label in counts if count]
    return ", ".join(parts) if parts else "no tests ran"


@contextlib.contextmanager
def _escaping_unencodable_output() -> Iterator[None]:
    """Print node ids that the console encoding cannot represent, exactly as pytest does.

    A test function may be named in any script Python accepts -- ``def test_測試():`` is
    legal -- so node ids are not ASCII by construction. On Windows a redirected stdout uses
    the locale encoding (cp1252 here), and a bare ``print`` of such an id raises
    ``UnicodeEncodeError``: the process would die with a traceback and exit 1, *outside* the
    0/2/3/4/5 exit-code contract this surface exists to honour.

    pytest survives the same id by escaping at write time rather than at id-construction
    time -- ``pytest --collect-only -q`` emits the bytes ``test_u.py::test_\\u6e2c\\u8a66``
    -- which is precisely ``errors="backslashreplace"``. Setting only ``errors`` and never
    ``encoding`` is what makes the output byte-identical: forcing UTF-8 would emit the raw
    characters and diverge.

    The change is scoped and restored, so importing ``rustest`` and calling
    :func:`v2_collect_only` as a library never leaves the caller's streams reconfigured.
    Streams that are not real text wrappers (a replaced ``sys.stdout`` in an embedding host)
    are skipped: there is nothing to reconfigure, and printing is unchanged for them.
    """
    # `TextIOWrapper` is generic in its buffer type, which `isinstance` cannot infer; the
    # annotation supplies it so the list is fully typed rather than partially unknown.
    streams: list[io.TextIOWrapper[Any]] = [
        stream for stream in (sys.stdout, sys.stderr) if isinstance(stream, io.TextIOWrapper)
    ]
    previous = [stream.errors for stream in streams]
    for stream in streams:
        stream.reconfigure(errors="backslashreplace")
    try:
        yield
    finally:
        for stream, errors in zip(streams, previous):
            stream.reconfigure(errors=errors)


def _pool_size(workers: int | None) -> int:
    """One worker per CPU unless the caller said otherwise; the Rust side clamps to files."""
    return workers if workers is not None and workers > 0 else (os.cpu_count() or 1)


def v2_collect_only(
    *,
    paths: Sequence[str],
    workers: int | None = None,
    keyword: str | None = None,
    mark_expr: str | None = None,
) -> int:
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
        keyword: The raw ``-k`` expression, or ``None``. Applied after collection, exactly
            where pytest applies it, so a file that failed to import still reports its
            error however aggressively the expression deselects.
        mark_expr: The raw ``-m`` expression, or ``None``.

    Returns:
        0 with tests, 5 with none (including "everything was deselected"), 2 when any file
        failed to import, 4 for a usage error (a missing path argument, an unusable config
        file, or a malformed ``-k``/``-m`` expression) and 3 for an orchestration failure.
    """
    # Everything that writes is inside the escaping scope, not just the node ids: a file
    # path or a traceback in an error message can be just as unencodable as an id, and a
    # crash while *reporting* a failure would be the worst place to leave one.
    with _escaping_unencodable_output():
        try:
            payload = rust.v2_collect(
                os.getcwd(),
                list(paths),
                sys.executable,
                _pool_size(workers),
                keyword,
                mark_expr,
            )
        except ValueError as exc:
            # pytest's UsageError shape, including its `ERROR: file or directory not
            # found: x` and `ERROR: Wrong expression passed to '-k': ...` wording, which
            # the Rust side produces verbatim.
            print(f"ERROR: {exc}", file=sys.stderr)
            return _EXIT_USAGE_ERROR
        except RuntimeError as exc:
            # The pool itself failed (a worker died, protocol drift, an unusable
            # interpreter). Loud and distinct from a user error -- never a quietly empty
            # collection.
            print(f"INTERNALERROR: {exc}", file=sys.stderr)
            return _EXIT_INTERNAL_ERROR

        manifest = cast(_Manifest, json.loads(payload))
        tests = manifest["tests"]
        errors = manifest.get("errors", [])
        deselected = manifest.get("deselected", 0)

        for test in tests:
            print(test["id"])
        for error in errors:
            print(f"ERROR collecting {error['path']}", file=sys.stderr)
            for line in error["message"].splitlines():
                print(f"  {line}", file=sys.stderr)
        print(_summary(len(tests), len(errors), deselected), file=sys.stderr)

    # The same three branches as `src/v2/execute.rs::exit_code` with `failures = 0`, which
    # is what a collect-only run always has: collection errors outrank everything, then
    # "nothing left" -- counted *after* deselection, which is why `-k nomatch` is 5 and not
    # 0. Kept here rather than read back off the manifest because collection produces no
    # `exit_code` field. Each branch is diffed against real pytest in
    # `python/tests/test_v2_collect_cli.py` (`..._exits_5`, `..._exits_2_and_names_the_file`,
    # `test_deselecting_everything_exits_5_and_says_how_many`, and
    # `test_selection_does_not_suppress_a_collection_error` for the precedence).
    if errors:
        return _EXIT_INTERRUPTED
    if not tests:
        return _EXIT_NO_TESTS_COLLECTED
    return _EXIT_OK


def v2_run(
    *,
    paths: Sequence[str],
    workers: int | None = None,
    keyword: str | None = None,
    mark_expr: str | None = None,
    report_json: str | None = None,
) -> int:
    """Run tests with the **v2** engine and return pytest's exit code.

    This is the whole of ``rustest --v2``. It runs the v2 spine end to end -- config
    resolution, file walk, a worker pool that collects and then stays alive to execute,
    ``-k``/``-m`` selection in between -- and never touches the v1 discovery or execution
    path.

    Output is deliberately thin, because output UX is Phase 1c and a half-built progress
    renderer would have to be thrown away:

    * **stdout** -- nothing at all on a clean run. A failing test's message is printed here
      under a ``FAILED <id>`` header, which is what makes a red run diagnosable from a
      terminal without ``--report-json``.
    * **stderr** -- ``ERROR collecting <path>`` blocks, anything the workers wrote (which
      legitimately includes class/module teardown output -- see the Task 3 divergence),
      and the one-line summary in pytest's wording.

    Args:
        paths: Files or directories to run. An **empty** sequence means "no path argument
            was given", which lets ``testpaths`` decide the roots exactly as under pytest.
        workers: Pool size. ``None`` means one worker per CPU; the Rust side clamps it to
            the number of files found.
        keyword: The raw ``-k`` expression, or ``None``.
        mark_expr: The raw ``-m`` expression, or ``None``.
        report_json: Where to write the schema-v2 JSON report, or ``None``.

    Returns:
        pytest's exit code: 0 clean, 1 failures (or errors, or an unattributable teardown
        failure), 2 collection errors, 5 nothing collected, 4 for a usage error and 3 for
        an orchestration failure.
    """
    with _escaping_unencodable_output():
        try:
            payload = rust.v2_run(
                os.getcwd(),
                list(paths),
                sys.executable,
                _pool_size(workers),
                keyword,
                mark_expr,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return _EXIT_USAGE_ERROR
        except RuntimeError as exc:
            print(f"INTERNALERROR: {exc}", file=sys.stderr)
            return _EXIT_INTERNAL_ERROR

        report = cast(_RunReport, json.loads(payload))

        # Written before anything is printed, so a report exists even if rendering the
        # summary somehow fails -- the conformance harness treats a missing report as a
        # harness fault rather than a case outcome.
        if report_json is not None:
            Path(report_json).write_text(json.dumps(report, indent=2), encoding="utf-8")

        for test in report["tests"]:
            if test["status"] in ("failed", "error"):
                print(f"{test['status'].upper()} {test['id']}")
                message = test.get("message")
                if message:
                    for line in message.splitlines():
                        print(f"  {line}")

        for error in report["collection_errors"]:
            print(f"ERROR collecting {error['path']}", file=sys.stderr)
            for line in error["message"].splitlines():
                print(f"  {line}", file=sys.stderr)

        # Never graded, never discarded: boundary teardown output lands here on runs that
        # are entirely green.
        for chunk in report.get("worker_stderr", []):
            print(chunk, file=sys.stderr, end="" if chunk.endswith("\n") else "\n")
        for failure in report.get("teardown_errors", []):
            print(f"ERROR {failure}", file=sys.stderr)

        print(
            _run_summary(report["summary"], len(report["collection_errors"])),
            file=sys.stderr,
        )

    return report["exit_code"]


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
