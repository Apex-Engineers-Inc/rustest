"""High level Python API wrapping the Rust extension."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from . import rust

if TYPE_CHECKING:
    from typing import Any, Final, NotRequired, TypedDict

    from .reporting import RunReport

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
        stopped_early: NotRequired[bool]
        session_exit: NotRequired[str]

# The `TypedDict`s above live under `TYPE_CHECKING`, and that is a latency decision like the
# ones below rather than a style one: on CPython 3.14 `typing` pulls `annotationlib` and
# `ast` behind it, ~15 ms that a `--v2-collect-only` run has no use for. `from __future__
# import annotations` makes every annotation a string, so the declarations are never
# evaluated at run time; the two `cast()` calls they used to serve became annotated
# assignments, which a type checker reads identically.

# `rich` is **not** imported here, and that is a measured decision rather than a style one.
#
# `import rich.console` costs ~230 ms above a bare interpreter on the reference machine, and
# it used to be paid by *every* rustest process: the v2 collect-only path, the v2 run path,
# the `--report-json` path, and — worst — each of the N worker subprocesses a run spawns,
# none of which render anything through rich at all. Only :func:`run`, the frozen v1 engine's
# entry point, and :func:`_print_pytest_compat_banner` ever touch it, so both import it in
# their own bodies.
#
# The same argument covers `.renderers` (which pulls `rich.live` and `rich.progress` behind
# it), `.event_router`, and `.reporting`: all three are v1-only, and `.reporting` is needed at
# type-check time only, hence the `TYPE_CHECKING` import above.
#
# Two stdlib modules are deferred for the same measured reason, each into the one function
# that needs it: `datetime` (only :func:`_format_duration`) and `pathlib` (only the
# `--report-json` write). A `--v2-collect-only` run reaches neither, and it is the
# latency-sensitive path.
#
# **`shutil` is not deferred, it is removed** — see :func:`_terminal_size`. Deferring it was
# the first attempt and it did not work, because `argparse` imports it too (below), so every
# rustest invocation paid it whether or not anything rendered.
#
# Measured, Phase 2 Task 3: warm `--v2-collect-only` on the 5 000-test suite went from 508 ms
# to ~285 ms with `rich` and the package's own graph made lazy, and to the figure in
# `.superpowers/sdd/p2-task-3-report.md` §4 once `shutil` and `_colorize` were taken off the
# argparse path as well.

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


#: The word `-v` prints for each status, taken from pytest's own verbose column.  Probed
#: (`pytest -v` on a file with one of each): `PASSED`, `FAILED`, `SKIPPED (reason)`,
#: `XFAIL (reason)`, `XPASS (reason)`, `ERROR`.  Note `XFAIL`/`XPASS` are *not* the
#: report-bucket spellings (`xfailed`/`xpassed`) — the summary line and the per-test column
#: genuinely use different words, and copying one into the other is the easy mistake.
_VERBOSE_WORD: Final[Mapping[str, str]] = {
    "passed": "PASSED",
    "failed": "FAILED",
    "skipped": "SKIPPED",
    "xfailed": "XFAIL",
    "xpassed": "XPASS",
    "error": "ERROR",
}

#: Statuses whose `message` is a *reason* rather than a traceback, so `-v` can append it in
#: parentheses exactly as pytest does.  A `failed`/`error` message is a traceback and belongs
#: in the failure section, not on the one-line column.
_REASON_STATUSES: Final = frozenset({"skipped", "xfailed", "xpassed"})


def _print_pytest_compat_banner(use_colors: bool) -> None:
    """Print the pytest compatibility mode banner using rich.

    Args:
        use_colors: Whether to use colored output
    """
    from rich.console import Console
    from rich.panel import Panel

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


#: Forces every file through a Python worker when set to ``"d"``, disabling the Rust static
#: collection tier (``src/v2/static_collect.rs``).
#:
#: Not a documented option and deliberately not a CLI flag: it exists so the **three-way
#: differential** can collect one tree twice -- once hybrid, once Tier D only -- and diff the
#: manifests against each other and against pytest. Tier D is the oracle, so a knob that turns
#: Tier S off is the only way to ask "did the static tier change the answer?", and that
#: question has to be askable from a subprocess, which is what makes it an environment
#: variable rather than a keyword argument. An unrecognised value means the default; see
#: ``src/v2/collect.rs::TierMode::from_wire`` for why a typo is not a usage error.
_COLLECT_TIER_ENV = "RUSTEST_V2_COLLECT_TIER"

#: Turns the Tier S manifest cache (``.rustest_cache/v2-manifest``) off when set to ``"off"``.
#:
#: The cache's twin of :data:`_COLLECT_TIER_ENV`, and an escape hatch for the same reason: a
#: cache that can only be trusted is not a cache that can be *debugged*. A user who suspects a
#: stale manifest -- or a maintainer bisecting one -- needs a way to ask for the answer
#: recomputed from source, from a subprocess, without deleting anything. Deleting
#: ``.rustest_cache`` also works and is the documented cure; this is the diagnosis.
#:
#: Not a CLI flag, for the same reason ``--no-cache`` is not one in most build tools: the
#: correct number of users who need it is small, and a flag would imply the cache is a choice
#: rather than an invariant of a correct run.
_MANIFEST_CACHE_ENV = "RUSTEST_V2_MANIFEST_CACHE"

#: Turns assertion rewriting off when set to ``"off"``.
#:
#: The third escape hatch, and the one with the largest claim behind it: rewriting changes
#: the **bytecode a user's tests run as** (``python/rustest/_assertion_rewrite.py``). A user
#: whose suite behaves differently under rustest than under a plain ``python -c "import
#: test_mod"`` needs a way to ask "is it the rewriter?" from a subprocess, without editing
#: anything and without deleting a cache. It is also the control leg of the message
#: differential: the same file, run twice, with and without.
#:
#: Not a CLI flag, for the same reason the other two are not: the correct number of users who
#: need it is small, and a flag would suggest rewriting is a choice rather than how the engine
#: reports a failed assertion.
_ASSERT_REWRITE_ENV = "RUSTEST_V2_ASSERT_REWRITE"


def v2_collect_only(
    *,
    paths: Sequence[str],
    workers: int | None = None,
    keyword: str | None = None,
    mark_expr: str | None = None,
    codeblocks: bool = True,
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
        keyword: The raw ``-k`` expression, or ``None``. Applied inside collection, exactly
            where pytest applies it -- and, for the files the static tier answered, *before*
            any worker is spawned, so a fully static tree whose every test is deselected
            starts no interpreter. A file that failed to import still reports its error
            however aggressively the expression deselects, which is why a file the dynamic
            tier owns is never pruned before its worker has run.
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
                codeblocks,
                os.environ.get(_COLLECT_TIER_ENV, "auto"),
                os.environ.get(_MANIFEST_CACHE_ENV, "auto"),
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

        manifest: _Manifest = json.loads(payload)
        tests = manifest["tests"]
        errors = manifest.get("errors", [])
        deselected = manifest.get("deselected", 0)

        # One `write` for the whole id list rather than 5 000 `print` calls.  Byte-identical
        # output (`print` is `write(x)` + `write("\n")` on a line-buffered-or-block-buffered
        # stream), and measurably cheaper: at 5 000 ids the loop cost ~55 ms of a ~270 ms
        # command, because each `print` re-enters the `TextIOWrapper` encode/newline path.
        # `sys.stdout` is used rather than `print(..., end="")` so the empty case writes
        # nothing at all instead of an empty string.
        if tests:
            _ = sys.stdout.write("".join(f"{test['id']}\n" for test in tests))
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


def _progress_line(test: _ReportTest, index: int, total: int) -> str:
    """One ``-v`` line: ``<nodeid> WORD (reason)`` plus pytest's right-hand percent column.

    pytest pads the id-and-word out and puts ``[ NN%]`` in the last columns
    (``_pytest/terminal.py::TerminalReporter._write_progress_information_filling_space``).
    The width is read from the terminal here for the same reason: a fixed 80 would wrap on a
    narrow console and leave a ragged gutter on a wide one.  A line already wider than the
    gutter gets a single space rather than a negative pad.
    """
    word = _VERBOSE_WORD.get(test["status"], test["status"].upper())
    line = f"{test['id']} {word}"
    message = test.get("message")
    if message and test["status"] in _REASON_STATUSES:
        line += f" ({message.splitlines()[0]})"
    percent = (index + 1) * 100 // total
    pad = max(1, _terminal_width() - len(line) - 8)
    return f"{line}{' ' * pad}[{percent:3d}%]"


def terminal_columns(fallback: int = 80) -> int:
    """``shutil.get_terminal_size().columns``, **without importing shutil**.

    A line-for-line port of ``shutil.get_terminal_size`` (CPython 3.14 ``Lib/shutil.py``),
    narrowed to the columns half: read ``COLUMNS`` from the environment, and only if it is
    absent or non-positive ask ``os.get_terminal_size(sys.__stdout__.fileno())``, falling back
    when stdout is ``None``, closed, detached, not a terminal, or the platform does not
    support the query.

    **Why a port and not the import.** ``shutil`` is ~50-70 ms on the reference machine — it
    pulls ``bz2``, ``lzma``, ``zlib`` and ``zstd`` at module scope for its archive support,
    none of which has anything to do with terminal size — and the function it is wanted for is
    three lines over ``os``, which is already imported and whose ``get_terminal_size`` is a
    builtin. The measured cost mattered because ``argparse`` reaches the same function on
    *every* rustest invocation (see :class:`_LazyHelpFormatter`), so deferring the import was
    not enough; it had to stop happening.

    The semantics are shutil's, deliberately, so behaviour under ``COLUMNS=…``, under a pipe,
    and under a detached stdout is unchanged from what every previous release did.
    """
    try:
        columns = int(os.environ["COLUMNS"])
    except (KeyError, ValueError):
        columns = 0
    if columns > 0:
        return columns
    try:
        # `sys.__stdout__` rather than `sys.stdout`: the worker rebinds `sys.stdout` to
        # stderr, and a captured stream has no fileno at all. shutil asks the same object.
        columns = os.get_terminal_size(sys.__stdout__.fileno()).columns  # pyright: ignore[reportOptionalMemberAccess]
    except (AttributeError, ValueError, OSError):
        return fallback
    return columns or fallback


def _terminal_width() -> int:
    """The console width, by pytest's own rule (`_pytest/_io/terminalwriter.py`).

    The floor is pytest's, not caution: ``get_terminal_width`` comments that "the Windows
    ``get_terminal_size`` may be bogus" and rounds anything under 40 back up to 80. Without
    it a console that reports 4 columns turns every separator into a bare ``==``.
    """
    width = terminal_columns()
    return 80 if width < 40 else width


def _sep(sepchar: str, title: str | None = None) -> str:
    """One separator line, a port of ``_pytest/_io/terminalwriter.py::TerminalWriter.sep``.

    ``=`` rules off a section (``=== FAILURES ===``) and ``_`` heads one failure inside it
    (``___ test_bad ___``) — probed from ``pytest`` 8.4.2 rather than transcribed, and the
    arithmetic is ported line for line so the result is the same *width* pytest would emit:
    the title is centred in ``fullwidth`` columns and a final ``sepchar`` is added when one
    more fits.

    **The ``win32`` subtraction is pytest's**, with pytest's reason: writing in the last
    column of a Windows console wraps invisibly, so it keeps one column in hand. Dropping it
    would put every separator one character past pytest's on the platform this repository is
    developed on, which is exactly where such a difference goes unnoticed.
    """
    fullwidth = _terminal_width()
    if sys.platform == "win32":
        fullwidth -= 1
    if title is not None:
        fill = sepchar * max((fullwidth - len(title) - 2) // (2 * len(sepchar)), 1)
        line = f"{fill} {title} {fill}"
    else:
        line = sepchar * (fullwidth // len(sepchar))
    if len(line) + len(sepchar.rstrip()) <= fullwidth:
        line += sepchar.rstrip()
    return line


def _headline(test: _ReportTest) -> str:
    """The title of one failure block: pytest's ``TestReport.head_line``.

    pytest heads each block with the report's *domain* — the node id with the file path
    removed and the remaining ``::`` separators written as ``.``, so
    ``test_a.py::TestBox::test_method`` becomes ``TestBox.test_method``
    (``_pytest/reports.py``, ``head_line``; probed: a class method prints
    ``TestX.test_fail``). The full id is not lost — the ``short test summary info`` section
    below carries it verbatim, which is also where pytest puts it.
    """
    node_id = test["id"]
    _, separator, domain = node_id.partition("::")
    return (domain if separator else node_id).replace("::", ".")


def _format_duration(seconds: float) -> str:
    """pytest's ``in <n>s`` tail, from ``_pytest/_io/terminalwriter.py::format_session_duration``.

    Two decimals under a minute; a bracketed ``H:MM:SS`` is appended above it, because
    ``in 3754.10s`` is not a number anyone reads. The threshold and both spellings are
    pytest's.
    """
    if seconds < 60:
        return f"{seconds:.2f}s"
    import datetime

    return f"{seconds:.2f}s ({datetime.timedelta(seconds=int(seconds))})"


def _print_failure_sections(tests: Sequence[_ReportTest]) -> None:
    """pytest's failure report: ``ERRORS``, then ``FAILURES``, then the short summary.

    Structural parity, not byte parity. Each block carries the ``message`` the worker sent,
    which is a formatted traceback produced by the worker's own ``traceback`` call rather
    than by pytest's ``ExceptionInfo`` machinery — so the *frames* inside a block are worded
    rustest's way and always will be. What is reproduced is the part a reader navigates by:
    the two section rules, one underscore-headed block per failing test, and the
    ``short test summary info`` list of full node ids.

    The order is pytest's, probed on a tree with one of each: ``ERRORS`` first (a test that
    never ran is a different question from one that ran and failed), then ``FAILURES``, then
    a short summary that lists **failures before errors** — pytest's default ``-r`` value is
    ``fE`` and the section is emitted in that character order
    (``_pytest/terminal.py::short_test_summary``).

    v2's blocks head with a phase-neutral ``ERROR <domain>`` where pytest writes
    ``ERROR at setup of <domain>``. The wire carries one *reduced* status per test and not
    the phase that produced it, so naming a phase here would be a guess; ``at setup of`` is
    right far more often than not, which is precisely what makes guessing it a bad idea.
    """
    failed = [test for test in tests if test["status"] == "failed"]
    errored = [test for test in tests if test["status"] == "error"]
    if not failed and not errored:
        return
    for title, group, prefix in (("ERRORS", errored, "ERROR "), ("FAILURES", failed, "")):
        if not group:
            continue
        print(_sep("=", title))
        for test in group:
            print(_sep("_", f"{prefix}{_headline(test)}"))
            message = test.get("message")
            if message:
                print(message.rstrip("\n"))
    print(_sep("=", "short test summary info"))
    for test in (*failed, *errored):
        print(f"{test['status'].upper()} {test['id']}")


class _CoverageRun:
    """``--cov`` seen from the orchestrator: the wire value, the scratch directory, the report.

    A context manager, because the per-worker data files must outlive the run and **not**
    outlive the report: they are intermediate state in a directory this process created, and
    leaving them behind on a failed run would seed the next run's ``combine`` with a previous
    run's lines.

    :meth:`disabled` is the no-coverage instance and is what every ordinary run gets. It holds
    no directory, sends ``None`` on the wire, and renders nothing -- so the coverage path costs
    a run without ``--cov`` one object allocation and two `if`s.
    """

    def __init__(
        self,
        sources: list[str] | None,
        reports: list[tuple[str, str | None]],
        data_dir: str | None,
    ) -> None:
        super().__init__()
        self._sources: list[str] | None = sources
        self._reports: list[tuple[str, str | None]] = reports
        self._data_dir: str | None = data_dir

    @classmethod
    def disabled(cls) -> _CoverageRun:
        return cls(None, [], None)

    @classmethod
    def prepare(
        cls,
        cov: Sequence[str] | None,
        cov_report: Sequence[str] | None,
        paths: list[str],
    ) -> _CoverageRun:
        """Validate the options, resolve the sources, and make the scratch directory.

        Raises `ValueError` for every user-facing mistake -- an unknown report type, a
        ``--cov=PATH`` that is not a directory, a missing ``coverage`` install -- so the caller
        can answer with pytest's usage exit code before a single worker is spawned.

        The rootdir a bare ``--cov`` resolves to is obtained from
        :func:`rustest.rust.v2_resolve_config`, i.e. from the **same** resolver the run itself
        will use, rather than from ``os.getcwd()``. They differ for every run started below a
        config file, and a coverage report scoped to the wrong tree is not obviously wrong when
        you read it.
        """
        if cov is None:
            return cls.disabled()

        from ._v2_coverage import (
            branch_refusal,
            config_requests_branch,
            parse_report_spec,
            require_coverage,
            resolve_sources,
        )

        require_coverage()
        # `branch = True` in `.coveragerc` / `[tool.coverage.run]` is the *second* door into a
        # request rustest cannot honour, and the dangerous one: `--cov-branch` is visible on the
        # command line, a config file set a year ago is not. Refused here, before a worker is
        # spawned, so it costs a usage error rather than a full run whose number overstates
        # coverage. `combine_and_report` passes `branch=False` explicitly as the structural half.
        if config_requests_branch():
            raise ValueError(branch_refusal("branch = True in the coverage configuration"))
        reports = [parse_report_spec(spec) for spec in (cov_report or ["term"])]
        rootdir: str = json.loads(rust.v2_resolve_config(os.getcwd(), paths))["rootdir"]
        sources = resolve_sources(cov, rootdir)

        import tempfile

        return cls(sources, reports, tempfile.mkdtemp(prefix="rustest-cov-"))

    @property
    def wire(self) -> str | None:
        """The ``coverage`` argument for ``rust.v2_run`` -- `None` when coverage is off.

        A JSON object matching ``src/v2/protocol.rs::CoverageWire`` exactly, because it *is*
        that object: the Rust boundary parses it with `serde` and forwards it onto every
        worker's ``init`` line unchanged, so there is one description of the shape rather than
        a Python encoder and a Rust decoder free to drift.
        """
        if self._sources is None or self._data_dir is None:
            return None
        return json.dumps(
            {
                "sources": [_posix(source) for source in self._sources],
                "data_dir": _posix(self._data_dir),
            },
            separators=(",", ":"),
        )

    def render(self, stream: Any) -> None:
        """Combine the workers' data files and write the requested reports.

        Errors are **reported, not raised**: the exit code belongs to the tests. A coverage
        report that cannot be produced is a loud line on stderr next to a test result that is
        still true.
        """
        if self._sources is None or self._data_dir is None:
            return
        from ._v2_coverage import combine_and_report

        try:
            _ = combine_and_report(
                data_dir=self._data_dir,
                sources=self._sources,
                # coverage.py's own default name and location, so `coverage html` or
                # `coverage report` after a `rustest --cov` run works with no arguments.
                data_file=os.path.abspath(".coverage"),
                reports=self._reports,
                stream=stream,
            )
        except Exception as exc:  # noqa: BLE001 - a broken report must not change the verdict
            print(f"ERROR: could not produce the coverage report: {exc}", file=sys.stderr)

    def cleanup(self) -> None:
        if self._data_dir is None:
            return
        import shutil

        shutil.rmtree(self._data_dir, ignore_errors=True)
        self._data_dir = None

    def __enter__(self) -> _CoverageRun:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()


def _posix(path: str) -> str:
    """Absolute path with forward slashes -- the spelling every v2 wire field uses."""
    return os.path.abspath(path).replace("\\", "/")


def v2_run(
    *,
    paths: Sequence[str],
    workers: int | None = None,
    keyword: str | None = None,
    mark_expr: str | None = None,
    report_json: str | None = None,
    fail_fast: bool = False,
    max_fail: int = 0,
    last_failed_mode: str = "none",
    capture: bool = True,
    codeblocks: bool = True,
    verbosity: int = 0,
    cov: Sequence[str] | None = None,
    cov_report: Sequence[str] | None = None,
) -> int:
    """Run tests with the **v2** engine and return pytest's exit code.

    This is the whole of a default ``rustest <paths>``.  It runs the v2 spine end to end --
    config resolution, the file walk, a worker pool that collects and then stays alive to
    execute, ``-k``/``-m`` selection and ``--lf``/``--ff`` reordering in between -- and never
    touches the v1 discovery or execution path.

    Output is a three-rung ladder -- pytest's own verbosity ladder, narrowed to three rungs:

    * ``verbosity < 0`` (``-q``) -- the summary line and nothing else.
    * ``verbosity == 0`` (default) -- plus pytest's failure report: an ``ERRORS`` section, a
      ``FAILURES`` section, and a ``short test summary info`` list of node ids
      (:func:`_print_failure_sections`), which is what makes a red run diagnosable from a
      terminal without ``--report-json``.
    * ``verbosity > 0`` (``-v``) -- plus one line per test in pytest's verbose wording
      (``PASSED``/``FAILED``/``SKIPPED (reason)``/``XFAIL``/``XPASS``/``ERROR``).

    The summary line carries pytest's ``in <n>s`` tail. Everything before the tail stays
    byte-identical to pytest's own summary line, which is what the differential tests
    compare; the tail is stripped there because a duration is not a claim about outcomes.

    The stream split is deliberate and unchanged from the ``--v2`` days: **stdout** carries
    the per-test lines and the failure blocks (the payload), **stderr** carries collection
    errors, whatever the workers wrote, and the summary (the diagnostics) -- so
    ``rustest ... > results`` keeps a grep-able file.

    Args:
        paths: Files or directories to run. An **empty** sequence means "no path argument
            was given", which lets ``testpaths`` decide the roots exactly as under pytest.
        workers: Pool size. ``None`` means one worker per CPU; the Rust side clamps it to
            the number of files found.
        keyword: The raw ``-k`` expression, or ``None``.
        mark_expr: The raw ``-m`` expression, or ``None``.
        report_json: Where to write the schema-v2 JSON report, or ``None``.
        fail_fast: ``-x`` -- stop dispatching after the first failure.
        max_fail: ``--maxfail=N`` -- stop dispatching after N failures; 0 is no limit.
        last_failed_mode: ``"none"``, ``"only"`` (``--lf``) or ``"first"`` (``--ff``).
        capture: ``False`` for ``-s``; the workers stop redirecting a test's streams.
        codeblocks: Collect python fences from ``.md`` files (``--no-codeblocks`` clears it).
        verbosity: ``-1`` for ``-q``, ``0`` default, ``1`` for ``-v``.
        cov: ``--cov`` values -- source directories, with ``""`` for a bare ``--cov`` (the
            rootdir).  ``None`` means no coverage at all, which is the only value that leaves
            the workers with no ``sys.monitoring`` tool registered.
        cov_report: ``--cov-report`` values (``term``, ``xml[:PATH]``); ``term`` when a
            ``--cov`` run gives none, matching pytest-cov's default.

    Returns:
        pytest's exit code: 0 clean, 1 failures (or errors, or an unattributable teardown
        failure), 2 collection errors, 5 nothing collected, 4 for a usage error and 3 for
        an orchestration failure.
    """
    try:
        coverage = _CoverageRun.prepare(cov, cov_report, list(paths))
    except ValueError as exc:
        # `--cov` argument errors are pytest's usage errors, and they are raised **before**
        # any worker is spawned: a bad `--cov-report` spelling must cost a line, not a run.
        print(f"ERROR: {exc}", file=sys.stderr)
        return _EXIT_USAGE_ERROR

    with _escaping_unencodable_output(), coverage:
        try:
            payload = rust.v2_run(
                os.getcwd(),
                list(paths),
                sys.executable,
                _pool_size(workers),
                keyword,
                mark_expr,
                fail_fast,
                max_fail,
                last_failed_mode,
                not capture,
                codeblocks,
                os.environ.get(_ASSERT_REWRITE_ENV, "auto"),
                coverage.wire,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return _EXIT_USAGE_ERROR
        except RuntimeError as exc:
            print(f"INTERNALERROR: {exc}", file=sys.stderr)
            return _EXIT_INTERNAL_ERROR

        report: _RunReport = json.loads(payload)

        # Written before anything is printed, so a report exists even if rendering the
        # summary somehow fails -- the conformance harness treats a missing report as a
        # harness fault rather than a case outcome.
        if report_json is not None:
            from pathlib import Path

            Path(report_json).write_text(json.dumps(report, indent=2), encoding="utf-8")

        tests = report["tests"]
        if verbosity > 0:
            # The denominator is what the run **selected**, not what it managed to run:
            # pytest's percent column is over `session.testscollected`, so a `-x` run that
            # stops at the second of three prints `[ 33%]`, `[ 66%]` and never reaches 100%.
            # Probed against pytest 8.4.2 both ways -- and `-k` selecting 2 of 4 does move the
            # denominator to 2, which is why it is `summary.total` (post-deselection) rather
            # than a collected count.
            total = max(report["summary"]["total"], len(tests))
            for index, test in enumerate(tests):
                print(_progress_line(test, index, total))

        if verbosity >= 0:
            _print_failure_sections(tests)

        for error in report["collection_errors"]:
            print(f"ERROR collecting {error['path']}", file=sys.stderr)
            for line in error["message"].splitlines():
                print(f"  {line}", file=sys.stderr)

        # Never graded, never discarded: boundary teardown output lands here on runs that
        # are entirely green -- and, under ``-s``, so does every test's own output.
        for chunk in report.get("worker_stderr", []):
            print(chunk, file=sys.stderr, end="" if chunk.endswith("\n") else "\n")
        for failure in report.get("teardown_errors", []):
            print(f"ERROR {failure}", file=sys.stderr)

        session_exit = report.get("session_exit")
        if session_exit:
            # The worker already wrote pytest's `Exit: <reason>` banner into its stderr; this
            # forwards it verbatim rather than re-wording the user's own reason.
            print(session_exit, file=sys.stderr)

        if report.get("stopped_early") and not session_exit:
            # pytest's own wording, from the ``!!!! stopping after 1 failures !!!!`` banner
            # ``_pytest/main.py`` puts on the terminal when ``maxfail`` trips.  Without it a
            # ``-x`` run looks like a suite that simply has fewer tests than it does.
            stopped = report["summary"]["failed"] + report["summary"]["error"]
            print(f"stopping after {stopped} failures (-x)", file=sys.stderr)

        # The tail is pytest's, and so is the fact that it is *appended* rather than made
        # part of `_run_summary`: the counts are a claim about outcomes and the duration is
        # not, which is why every parity comparison in the suite strips it. `summary.duration`
        # is the orchestrator's wall clock over the staged run (`src/v2/execute.rs`), so it
        # excludes interpreter startup where pytest's includes its own session setup.
        # Rendered **before** the summary line and on **stdout**, which places it where
        # pytest-cov puts it: its `pytest_terminal_summary` hook runs after the failure
        # sections and the short test summary, and before pytest's own `= N passed =` line.
        # Failures here are reported and do not change the exit code -- the tests' verdict is
        # the run's verdict, and a broken report must not turn a red run green or a green one
        # red.
        coverage.render(sys.stdout)

        summary_line = _run_summary(report["summary"], len(report["collection_errors"]))
        tail = _format_duration(report["summary"]["duration"])
        print(f"{summary_line} in {tail}", file=sys.stderr)

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
    # v1-only imports, deferred so the v2 paths never pay for them (see the module header).
    from .event_router import EventRouter
    from .renderers import RichRenderer
    from .reporting import RunReport

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
