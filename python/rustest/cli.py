"""Command line interface helpers.

**There is one engine.**  ``rustest <paths>`` runs the engine end to end — config
resolution, the file walk, a worker pool that collects and then executes, pytest's exit
codes — with the pytest compatibility shim installed unconditionally.  Collect-only is
pytest's spelling, ``--collect-only`` / ``--co``.  A flag that has been removed exits 4
naming the change rather than being swallowed by ``nargs="*"`` as a path, which is what
``REMOVED_FLAGS`` exists for.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from .core import NeedsPytest, terminal_columns, collect_only, run

if TYPE_CHECKING:
    # Annotation-only; see the note in `core.py` about keeping `typing` off the run-time
    # import path.
    from typing import Any, NoReturn


class _LazyHelpFormatter(argparse.HelpFormatter):
    """``argparse.HelpFormatter`` with its two eager imports taken off the hot path.

    **The problem, traced rather than guessed.** ``ArgumentParser.add_argument`` builds a
    formatter *twice per argument* — once in ``_ActionsContainer.add_argument`` to validate
    the metavar against nargs, once in ``_check_help`` to expand the help string
    (`Lib/argparse.py`, CPython 3.14). Each construction runs
    ``HelpFormatter.__init__``, which does ``import shutil; shutil.get_terminal_size()`` when
    no explicit ``width`` is given, and ``_set_color``, which does
    ``from _colorize import can_colorize, decolor, get_theme``. ``_get_formatter`` then calls
    ``_set_color`` a third time.

    So building this CLI's parser — 15 arguments — imported ``shutil`` (and behind it
    ``bz2``, ``lzma``, ``zlib``, ``zstd``) and ``_colorize`` on **every** rustest invocation,
    including ``--collect-only`` and every worker subprocess, none of which ever print
    help. Measured on the reference machine: constructing a parser with one argument cost
    ~170 ms more than constructing an empty one.

    **The fix.** Both values are needed only when help is actually *formatted*, so both are
    deferred:

    * ``width`` is supplied up front from :func:`rustest.core.terminal_columns`, a port of
      ``shutil.get_terminal_size`` over ``os`` alone — so ``__init__`` never takes its
      ``import shutil`` branch, and the width is still the real terminal's;
    * ``_set_color`` records the flag and nothing else; ``_theme`` and ``_decolor`` are
      properties that resolve on first *use*, which happens only inside the formatting
      methods.

    Nothing about the rendered help changes: the same width, the same theme, the same
    colour decision — just computed when they are needed rather than fifteen times while the
    parser is being described. ``test_help_output_is_unchanged_by_the_lazy_formatter``
    compares the full ``--help`` text against stock argparse's.
    """

    def __init__(
        self,
        prog: str,
        indent_increment: int = 2,
        max_help_position: int = 24,
        width: int | None = None,
        color: bool = True,
    ) -> None:
        #: ``(theme, decolor)`` once `_colorization` has resolved them; `None` until then.
        #: Assigned before `super().__init__`, which calls `_set_color` on the way through.
        self._resolved: tuple[Any, Any] | None = None
        self._color: bool = color
        if width is None:
            # argparse's own arithmetic: `get_terminal_size().columns - 2`.
            width = terminal_columns() - 2
        # `color` reaches this class through `_set_color` rather than through
        # `super().__init__`: the parameter exists on CPython 3.13+ but not in every typeshed
        # stub, and the base constructor's only use of it is the `_set_color` call this class
        # overrides anyway.
        super().__init__(prog, indent_increment, max_help_position, width)

    def _set_color(self, color: bool) -> None:
        """Record the flag and resolve nothing.

        The base class does the ``_colorize`` import here; deferring it is half the point of
        this subclass. ``_get_formatter`` calls this a second time after construction, so a
        formatter that *is* about to render help still gets the parser's real setting.
        """
        self._color = color
        self._resolved = None

    def _colorization(self) -> tuple[Any, Any]:
        """The theme and decolor function, importing ``_colorize`` on first use only.

        **``_colorize`` is version-gated stdlib private API**, which is why it is reached
        through :func:`importlib.import_module` rather than an ``import`` statement. Measured
        across the supported floor: it does not exist at all on 3.12, and on 3.13 it exists
        *without* ``decolor`` or ``get_theme``. Only 3.14 has all three.

        The branch is nevertheless unreachable below 3.14, because the ``_theme``/``_decolor``
        properties that call it are read only by 3.14's ``argparse`` formatting methods —
        3.12 and 3.13 ship a ``HelpFormatter`` that has never heard of them. Verified end to
        end on a real 3.12 install: ``--help``, ``--help --color always``, a usage error and
        a full run all behave.

        **A plain ``import`` cannot be made to type-check in both places.** `ci.yml`'s Type
        Check job runs on 3.12, where the module is missing and the import is an error; a
        developer machine on 3.14 resolves it fine, which makes a
        ``# pyright: ignore[reportMissingImports]`` *unnecessary* — and
        ``reportUnnecessaryTypeIgnoreComment`` is an error too, so the suppression fails
        wherever the bare import would have passed. A dynamic import is honest about what is
        actually happening and is stable in both environments.

        ``AttributeError`` joins ``ImportError`` in the guard because 3.13's failure mode is
        the *names*, not the module.
        """
        if self._resolved is None:
            try:
                import importlib

                colorize = importlib.import_module("_colorize")
                can_colorize = colorize.can_colorize
                decolor = colorize.decolor
                get_theme = colorize.get_theme
            except (ImportError, AttributeError):
                # No theme object to hand back; `None` is what argparse's own pre-3.14
                # formatter effectively has, and `_identity` leaves the text alone.
                self._resolved = (None, _identity)
                return self._resolved

            if self._color and can_colorize():
                self._resolved = (get_theme(force_color=True).argparse, decolor)
            else:
                self._resolved = (get_theme(force_no_color=True).argparse, _identity)
        return self._resolved

    # Properties rather than lazily-assigned attributes: argparse reads `self._theme` and
    # `self._decolor` from inside its formatting methods, and a property is the one form
    # that keeps them resolvable without ever existing until something asks.
    @property
    def _theme(self) -> Any:
        return self._colorization()[0]

    @property
    def _decolor(self) -> Any:
        return self._colorization()[1]


def _identity(text: str) -> str:
    """``decolor``'s no-op counterpart, named so the stored value stays an ordinary function."""
    return text


#: Flags that once existed and are now refused **loudly** rather than ignored — a CI file
#: still passing one deserves to be told what changed, not to have the argument silently
#: swallowed by ``nargs="*"`` as a path.
#:
#: ``--pytest-compat`` went at the flip: the shim it enabled is now always installed, so the
#: flag could only ever be a no-op or a lie.  ``--v1`` went with the engine it selected —
#: and it is listed here rather than left to argparse precisely because "unrecognised flag"
#: does not tell a reader whose CI has run ``--v1`` for months what to do about it.
REMOVED_FLAGS: dict[str, str] = {
    "--pytest-compat": (
        "--pytest-compat has been removed: the pytest compatibility shim is now always"
        + " installed, so every run is a compat run. Drop the flag."
        + " See CHANGELOG.md (0.17.0, 'the flip')."
    ),
    "--v1": (
        "--v1 has been removed: the legacy engine it selected is deleted. Drop the flag --"
        + " the default engine is the only engine, and it is the one every conformance gate"
        + " measures. See CHANGELOG.md."
    ),
}


#: pytest flags rustest **accepts and ignores**, with the value they take.
#:
#: Every one of these changes how pytest *reports*, not what it runs, and every one of them
#: is the kind of thing that lives in a project's `addopts` forever. Before Phase 4 Task 1's
#: review they were unrecognised, so `addopts = "-ra --tb=short"` -- an entirely ordinary
#: line -- made `rustest` exit 4 on a repo pytest runs happily. Erroring on a *cosmetic*
#: flag is the wrong trade: the run it describes is one rustest can do, just with its own
#: output.
#:
#: The value is how many following tokens the flag consumes when it is written separately
#: (`--tb short`), so the argument is dropped with it rather than left behind to be read as
#: a path.
#:
#: **Dropping is loud.** One stderr line per flag, naming it, so a reader is never wondering
#: why `--durations=10` produced no timing table. Anything *not* on this list is still exit 4
#: (`_Parser.error`), because a flag that changes what runs must never be silently ignored.
IGNORED_PYTEST_FLAGS: dict[str, int] = {
    "--tb": 1,
    "--durations": 1,
    "--durations-min": 1,
    "--import-mode": 1,
    "--strict-markers": 0,
    "--strict-config": 0,
    "--strict": 0,
    "-p": 1,
    "--showlocals": 0,
    "-l": 0,
    "--full-trace": 0,
}


def _drop_ignored_pytest_flags(raw: list[str]) -> tuple[list[str], list[str]]:
    """Split *raw* into (what the parser sees, what was dropped).

    ``-rA``/``-ra``/``-rfE`` are handled apart from the table because pytest's ``-r`` takes
    its characters **attached** and there is no separate-token form to consume.
    """
    kept: list[str] = []
    dropped: list[str] = []
    index = 0
    while index < len(raw):
        token = raw[index]
        index += 1
        # `-r` + report characters, always attached (`-ra`, `-rfEsxX`).
        if len(token) > 2 and token.startswith("-r") and not token.startswith("--"):
            dropped.append(token)
            continue
        name, _, inline = token.partition("=")
        takes = IGNORED_PYTEST_FLAGS.get(name)
        if takes is None:
            kept.append(token)
            continue
        dropped.append(token)
        # `--tb short` (separate token) as well as `--tb=short`; only consume when the flag
        # takes a value and did not already carry one, and never consume another flag.
        if takes and not inline and index < len(raw) and not raw[index].startswith("-"):
            index += 1
    return kept, dropped


class _ArgError(Exception):
    """A usage error the parser would have exited on, raised instead while pytest may take over."""


class _Parser(argparse.ArgumentParser):
    """``ArgumentParser`` whose usage errors exit **4**, and can name where the flag came from.

    Two reasons, and they point the same way.

    *pytest exits 4 for a usage error* — ``UsageError`` is ``ExitCode.USAGE_ERROR``. Measured
    on pytest 8.4.2: an unrecognised flag, whether typed on the command line or read out of
    ``addopts``, prints ``error: unrecognized arguments: --bogus-flag`` and exits 4. argparse's
    stock ``error`` exits **2**, which in this CLI already means "collection error" — the
    exact collision ``REMOVED_FLAGS`` was given its own branch to avoid.

    *And the flag may not be the user's.* When a bad option arrives through ``addopts``,
    pytest appends the config file and rootdir to the message so the reader knows which file
    to edit (``Config._validate_args`` sets ``_config_source_hint``). :attr:`config_source_hint`
    is that, under a different name.
    """

    config_source_hint: str | None = None
    #: ``True`` while a usage error should be handed to pytest instead of ending the run.
    fallback: bool = False

    def error(self, message: str) -> "NoReturn":
        if self.fallback:
            raise _ArgError(message)
        self.print_usage(sys.stderr)
        hint = f"\n{self.config_source_hint}" if self.config_source_hint else ""
        self.exit(4, f"{self.prog}: error: {message}{hint}\n")


def build_parser() -> _Parser:
    parser = _Parser(
        prog="rustest",
        # pytest's parser does not abbreviate either, and an abbreviation would swallow a
        # conftest option that happens to be a prefix of a rustest flag (`--report`).
        allow_abbrev=False,
        description="Run Python tests at blazing speed with a Rust powered core.",
        # See :class:`_LazyHelpFormatter`: without it, describing the arguments below
        # imports `shutil` and `_colorize` on every rustest invocation.
        formatter_class=_LazyHelpFormatter,
    )
    _ = parser.add_argument(
        "paths",
        nargs="*",
        default=(".",),
        help="Files or directories to collect tests from.",
    )
    _ = parser.add_argument(
        "-k",
        "--pattern",
        help="Substring to filter tests by (case insensitive).",
    )
    _ = parser.add_argument(
        "-m",
        "--marks",
        dest="mark_expr",
        help='Run tests matching the given mark expression (e.g., "slow", "not slow", "slow and integration").',
    )
    _ = parser.add_argument(
        "-n",
        "--workers",
        type=int,
        help="Number of worker processes to use (default: 4, capped by CPU count).",
    )
    _ = parser.add_argument(
        "-s",
        "--no-capture",
        dest="capture_output",
        action="store_false",
        help="Do not capture stdout/stderr during test execution.",
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="List every test with its outcome (one PASSED/FAILED line per test when piped).",
    )
    _ = parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help=(
            "Show only the overall progress bar (bare letters when piped). Failures are still"
            " reported."
        ),
    )
    _ = parser.add_argument(
        "--ascii",
        action="store_true",
        help="Draw the terminal display with ASCII characters only.",
    )
    _ = parser.add_argument(
        "--color",
        # pytest spells these `yes`/`no`/`auto` (`_pytest/terminal.py::pytest_addoption`);
        # rustest shipped `always`/`never`/`auto`. Both spellings are accepted.
        choices=["auto", "always", "never", "yes", "no"],
        default="auto",
        help="Colorize the output (default: auto).",
    )
    _ = parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Run the tests, then rerun on every save (changed test files plus last time's"
            " failures; everything when a source file changes). Keys: Enter rerun, a all,"
            " f failed, p file filter, t name filter, q quit. Needs a terminal."
        ),
    )
    _ = parser.add_argument(
        "--no-pytest-fallback",
        dest="pytest_fallback",
        action="store_false",
        help=(
            "Run with rustest even when the session needs pytest (a conftest hook that changes"
            " what runs, -p PLUGIN, or an option rustest does not have). Without it such a"
            " session is handed to pytest."
        ),
    )
    _ = parser.add_argument(
        "-o",
        "--override-ini",
        action="append",
        default=None,
        metavar="OPTION=VALUE",
        dest="override_ini",
        help=(
            "Override an ini option, e.g. -o addopts=. Supported key: addopts."
            " Registered here for --help and usage errors; the values are consumed by"
            " _extract_ini_overrides before the config is resolved, because addopts has to"
            " be known before argv is assembled."
        ),
    )
    _ = parser.add_argument(
        "--maxfail",
        type=int,
        default=0,
        metavar="NUM",
        help="Exit after the first NUM failures or errors (0 means no limit).",
    )
    _ = parser.add_argument(
        "--codeblocks",
        dest="enable_codeblocks",
        action="store_true",
        default=None,
        help="Collect and run python code blocks in markdown files named as arguments.",
    )
    _ = parser.add_argument(
        "--no-codeblocks",
        dest="enable_codeblocks",
        action="store_false",
        help="Do not collect code blocks, overriding any config setting.",
    )
    _ = parser.add_argument(
        "--lf",
        "--last-failed",
        action="store_true",
        dest="last_failed",
        help="Rerun only the tests that failed in the last run.",
    )
    _ = parser.add_argument(
        "--ff",
        "--failed-first",
        action="store_true",
        dest="failed_first",
        help="Run previously failed tests first, then all other tests.",
    )
    _ = parser.add_argument(
        "-x",
        "--exitfirst",
        action="store_true",
        dest="fail_fast",
        help="Exit instantly on first error or failed test.",
    )
    _ = parser.add_argument(
        "--report-json",
        dest="report_json",
        metavar="PATH",
        help="Write a machine-readable JSON report to PATH.",
    )
    # `nargs="?"` + `const=""` is pytest-cov's `--cov [SOURCE]` shape: the flag takes an
    # optional value, and `""` (bare `--cov`) means the rootdir. `append` because the option
    # is multi-allowed there too, and a project with two source trees is ordinary.
    _ = parser.add_argument(
        "--cov",
        dest="cov",
        nargs="?",
        const="",
        default=None,
        action="append",
        metavar="SOURCE",
        help=(
            "Measure line coverage of SOURCE (a directory; repeatable). "
            "With no value, measures the rootdir. Needs the `cov` extra."
        ),
    )
    _ = parser.add_argument(
        "--cov-report",
        dest="cov_report",
        action="append",
        metavar="TYPE",
        help="Coverage report to produce: `term` (default) or `xml[:PATH]`. Repeatable.",
    )
    _ = parser.add_argument(
        "--cov-branch",
        dest="cov_branch",
        action="store_true",
        help="Not implemented: rustest measures line coverage only. Refused rather than ignored.",
    )
    # `--llm` and its two satellites. The flag *surface* is 0.18's, deliberately -- a project
    # that already scripts `rustest --llm`, `--llm-full` or `--llm-schema` keeps working -- while
    # the JSONL the first of them emits is schema 2 and shaped to the v2 report. See
    # `docs/guide/llm-output.md` for the contract and `_llm_schema.SCHEMA_VERSION` for why the
    # major version moved.
    _ = parser.add_argument(
        "--llm",
        action="store_true",
        dest="llm",
        help=(
            "Emit the run as JSONL on stdout for LLM tooling: one object per line, "
            "meta first and a summary sentinel last. Replaces the human output entirely."
        ),
    )
    _ = parser.add_argument(
        "--llm-full",
        action="store_true",
        dest="llm_full",
        help=(
            "With --llm: attach captured output whole, instead of the last 50 lines. "
            "Refused on its own, because it would be inert."
        ),
    )
    _ = parser.add_argument(
        "--llm-schema",
        action="store_true",
        dest="llm_schema",
        help=(
            "Print the JSON Schema for --llm output on stdout and exit 0. "
            "Runs nothing; every other option is ignored."
        ),
    )
    # Deliberately `store_true` and not argparse's `action="version"`. The stock action wants
    # the version *string* at parser-construction time, which would put an
    # `importlib.metadata` scan on every single rustest invocation -- the same cost
    # `_LazyHelpFormatter` exists to keep off this path. Answered from `raw` in `main`.
    _ = parser.add_argument(
        "--version",
        action="store_true",
        dest="version",
        help="Print the rustest version on stdout and exit 0. Runs nothing.",
    )
    # `--co` is spelled out rather than left to argparse's prefix matching, which would call
    # it ambiguous against `--color`, `--codeblocks` and the three `--cov*` flags. An exact
    # option string wins over abbreviation, so declaring it is what makes pytest's short
    # spelling usable at all.
    _ = parser.add_argument(
        "--collect-only",
        "--co",
        action="store_true",
        dest="collect_only",
        help=(
            "Collect tests and print their node ids one per line, without running anything. "
            "Honours -k, -m and -n. Exits 0 with tests, 5 with none, 2 on collection errors. "
            "None of the other options apply."
        ),
    )
    parser.set_defaults(
        capture_output=True,
        last_failed=False,
        failed_first=False,
        fail_fast=False,
        maxfail=0,
        quiet=False,
        report_json=None,
        collect_only=False,
        cov=None,
        cov_report=None,
        cov_branch=False,
        llm=False,
        llm_full=False,
        llm_schema=False,
        version=False,
    )
    return parser


def _run_with(
    args: argparse.Namespace, paths: list[str], *, keyword: str | None, report_json: str | None
) -> int:
    """:func:`rustest.core.run` with everything from the parsed command line, but for the
    paths, the ``-k`` expression and the report path, which ``--watch`` varies per run."""
    return run(
        paths=paths,
        workers=args.workers,
        keyword=keyword,
        mark_expr=args.mark_expr,
        report_json=report_json,
        # `-x` is `--maxfail=1`, which is how pytest defines it
        # (`_pytest/main.py::pytest_addoption`: `-x` is `--maxfail=1`'s alias). Passing both
        # is not an error there either; the stricter of the two wins.
        max_fail=1 if args.fail_fast else args.maxfail,
        fail_fast=args.fail_fast or args.maxfail == 1,
        last_failed_mode=_last_failed_mode(args),
        capture=args.capture_output,
        codeblocks=args.enable_codeblocks,
        verbosity=_verbosity(args),
        cov=args.cov,
        cov_report=args.cov_report,
        llm=args.llm,
        llm_full=args.llm_full,
        color=args.color,
        ascii=args.ascii,
        pytest_fallback=args.pytest_fallback,
    )


def _watch_main(args: argparse.Namespace, paths: list[str], resolved: dict[str, Any]) -> int:
    """``--watch``: the engine behind :func:`rustest._watch.watch`, wired to this CLI's run."""
    import json
    import tempfile

    from . import _progress, _watch

    problem = _watch.refusal(
        llm=args.llm,
        collect_only=args.collect_only,
        interactive=sys.stdin.isatty() and sys.stdout.isatty(),
    )
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 4
    color = _progress.use_color(sys.stdout, args.color)  # switches ANSI on for a Windows console
    # the screen clear needs escape codes even when colour is off (a legacy console may lack them)
    escapes = color or sys.platform != "win32" or _progress._enable_vt(sys.stdout)
    cwd = os.getcwd()
    rootdir = str(resolved.get("rootdir") or cwd)
    norecursedirs = [str(p) for p in resolved.get("norecursedirs", [])]
    roots = _watch.choose_roots(rootdir, paths, cwd)
    report = args.report_json
    temporary = None
    if report is None:
        handle, temporary = tempfile.mkstemp(prefix="rustest-watch-", suffix=".json")
        os.close(handle)
        report = temporary

    def run_spec(spec: _watch.RunSpec) -> tuple[int, list[str]]:
        code = _run_with(args, spec.paths, keyword=spec.keyword, report_json=report)
        try:
            with open(report, encoding="utf-8") as handle:
                failed = _watch.failed_ids(json.load(handle))
        except (OSError, ValueError):
            failed = []
        return code, failed

    try:
        return _watch.watch(
            run=run_spec,
            scan_files=lambda: _watch.scan(roots, norecursedirs),
            is_test_file=_watch.test_file_matcher(
                [str(p) for p in resolved.get("python_files", [])]
            ),
            paths=paths,
            rootdir=rootdir,
            cwd=cwd,
            keyword=args.pattern,
            keys=_watch.TerminalKeys(),
            out=sys.stdout,
            selected=_watch.selection(
                rootdir, paths, [str(p) for p in resolved.get("testpaths", [])], cwd
            ),
            clear=escapes,
            dim=color,
        )
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _last_failed_mode(args: argparse.Namespace) -> str:
    """``--lf`` / ``--ff`` as the one string the core takes.

    ``--lf`` wins when both are given, matching pytest: ``LFPlugin`` checks
    ``config.getoption("lf")`` first and only falls through to the failed-first branch
    otherwise (`_pytest/cacheprovider.py`).
    """
    if args.last_failed:
        return "only"
    if args.failed_first:
        return "first"
    return "none"


def _verbosity(args: argparse.Namespace) -> int:
    """``-q`` = -1, default = 0, ``-v`` = 1 — pytest's verbosity ladder, three rungs of it.

    pytest's own is an integer that ``-v`` increments and ``-q`` decrements
    (`_pytest/config/argparsing.py`), so passing both cancels out.  Reproduced here rather
    than made an error, because "cancels out" is what a user who wrote ``-qv`` in a Makefile
    already gets from pytest.
    """
    return int(args.verbose) - int(args.quiet)


def _config_for_addopts(raw: list[str], parser: _Parser) -> "dict[str, Any]":
    """Resolve the config that owns ``addopts``, from the *path-ish* arguments only.

    Mirrors `_pytest/config/__init__.py::Config._initini`, which parses the command line
    with ``parse_known_and_unknown_args`` and then hands ``ns.file_or_dir + unknown_args`` to
    ``determine_setup``.  Parsing first is what stops ``-k pattern`` from being read as a
    directory named ``pattern`` and resolving the config from the wrong tree.

    A config file rustest cannot use is **not** reported here: the engine resolves the same
    config a moment later and raises the same error with the same exit code, and complaining
    twice about one file is worse than complaining once.
    """
    import json

    from . import rust

    known, unknown = parser.parse_known_args(raw)
    probe: list[str] = []
    if known.paths is not parser.get_default("paths"):
        probe.extend(str(path) for path in known.paths)
    probe.extend(arg for arg in unknown if not arg.startswith("-"))
    try:
        return json.loads(rust.resolve_config(os.getcwd(), probe))
    except Exception:  # noqa: BLE001 - re-raised by the engine, with its own message
        return {}


#: The ini keys ``-o``/``--override-ini`` can actually override.
#:
#: **One key, and the restriction is honest rather than lazy.** ``addopts`` is the only ini
#: value the *Python CLI* consumes -- it is spliced onto argv by :func:`main` -- so it is the
#: only one this layer can change. Every other key (``testpaths``, ``python_files``,
#: ``norecursedirs``, ...) is read by the Rust config resolver, which has no override channel
#: and would silently ignore anything set here. Accepting ``-o python_files=...`` and doing
#: nothing with it is precisely the class of silent no-op this project refuses, so an
#: unsupported key is a **usage error (exit 4)** naming what is supported.
OVERRIDABLE_INI_KEYS: frozenset[str] = frozenset({"addopts"})


def _extract_ini_overrides(raw: list[str]) -> "tuple[list[str], dict[str, str], list[str]]":
    """Split ``-o KEY=VALUE`` / ``--override-ini=KEY=VALUE`` out of *raw*.

    Returns ``(remaining_args, overrides, malformed)``.  Both attached (``-oaddopts=``,
    ``--override-ini=addopts=``) and separated (``-o addopts=``) spellings are accepted,
    which is pytest's own surface (`_pytest/config/argparsing.py` registers ``-o`` with
    ``action="append"`` and ``Config._override_ini`` splits on the first ``=``).

    Extraction happens **before** the ``addopts`` splice in :func:`main`, and that ordering is
    the whole feature: ``addopts`` is prepended to argv, so a value that arrives after the
    splice could not change what was spliced.

    Splitting on the **first** ``=`` only, so ``-o addopts=-k "a=b"`` keeps its inner ``=``.
    A token with no ``=`` at all is collected into *malformed* rather than guessed at.
    """
    remaining: list[str] = []
    overrides: dict[str, str] = {}
    malformed: list[str] = []
    index = 0
    while index < len(raw):
        arg = raw[index]
        value: str | None = None
        if arg in ("-o", "--override-ini"):
            if index + 1 < len(raw):
                value = raw[index + 1]
                index += 1
            else:
                malformed.append(arg)
        elif arg.startswith("--override-ini="):
            value = arg[len("--override-ini=") :]
        elif arg.startswith("-o") and len(arg) > 2:
            value = arg[2:]
        else:
            remaining.append(arg)
        if value is not None:
            key, sep, setting = value.partition("=")
            if not sep:
                malformed.append(value)
            else:
                overrides[key.strip()] = setting
        index += 1
    return remaining, overrides, malformed


def _plugin_names(raw: Sequence[str]) -> list[str]:
    """The plugins ``-p NAME`` / ``-pNAME`` would load; ``-p no:NAME`` only disables one."""
    names: list[str] = []
    for index, token in enumerate(raw):
        if token == "-p" and index + 1 < len(raw):
            names.append(raw[index + 1])
        elif token.startswith("-p") and not token.startswith("--") and len(token) > 2:
            names.append(token[2:].lstrip("="))
    return [name for name in names if not name.startswith("no:")]


def _blocked_plugins(raw: Sequence[str]) -> list[str]:
    """The names ``-p no:NAME`` / ``-pno:NAME`` disables, which pytest applies to entry points."""
    names: list[str] = []
    for index, token in enumerate(raw):
        if token == "-p" and index + 1 < len(raw):
            names.append(raw[index + 1])
        elif token.startswith("-p") and not token.startswith("--") and len(token) > 2:
            names.append(token[2:].lstrip("="))
    return [name[3:] for name in names if name.startswith("no:")]


def _hand_to_pytest(
    parser: _Parser,
    raw: Sequence[str],
    user_argv: Sequence[str],
    reasons: Sequence[str],
    optout: str | None = None,
) -> int:
    """Give the session to pytest, with whichever machine outputs *raw* asked for."""
    from . import _fallback

    try:
        known, _ = parser.parse_known_args(list(raw))
    except _ArgError:  # a bad value: pytest will say so itself
        known = argparse.Namespace(
            report_json=None,
            llm=False,
            llm_full=False,
            collect_only=False,
            verbose=False,
            quiet=False,
            watch=False,
        )
    if known.watch:
        from ._watch import needs_pytest

        print(needs_pytest(reasons), file=sys.stderr)
        return 4
    return _fallback.run(
        user_argv,
        reasons,
        optout,
        report_json=known.report_json,
        llm=known.llm,
        llm_full=known.llm_full,
        collect_only=known.collect_only,
        verbosity=_verbosity(known),
    )


def _rejected(
    parser: _Parser,
    error: _ArgError,
    raw: Sequence[str],
    user_argv: Sequence[str],
    origin: dict[str, str] | None = None,
) -> int:
    """The parser refused the arguments; if it is a flag it does not know, pytest may.

    Only ``unrecognized arguments`` goes to pytest. A rustest flag with a bad or missing
    value (``-n abc``) is rustest's own usage error, as are the removed ``--pytest-compat``
    and ``--v1``, malformed ``-o``, and the ``--llm-full``/``--cov-report``/``--cov-branch``
    refusals: none of those is "pytest may accept this". Without pytest installed the
    original usage error is printed too.
    """
    from . import _fallback

    message = str(error)
    prefix = "unrecognized arguments: "
    if not message.startswith(prefix) or not _fallback.pytest_available():
        parser.fallback = False
        parser.error(message)
    reasons = [
        f"{arg} is not a rustest option{(origin or {}).get(arg, '')}"
        for arg in message[len(prefix) :].split()
    ]
    return _hand_to_pytest(parser, raw, user_argv, reasons)


def _autoloaded_plugins(blocked: Sequence[str]) -> list[str]:
    from . import _fallback

    return _fallback.autoload_reasons(blocked)


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # pytest reads its own `addopts`, so a handed-over session gets the user's argv as typed.
    user_argv = list(raw)

    parser = build_parser()
    parser.fallback = "--no-pytest-fallback" not in raw
    # `addopts` is prepended to argv, which is what `Config._preparse` does
    # (`args[:] = self._validate_args(self.getini("addopts"), "via addopts config") + args`,
    # l. 1394-1397). *Prepended*, not appended, so an explicit command-line flag still wins a
    # last-one-wins option, and paths in `addopts` come first.
    #
    # `PYTEST_ADDOPTS` is spliced after it, below, while a session may still go to pytest.
    # `-o`/`--override-ini` is pulled out **first**, because the one key it supports is the
    # one that decides what argv even is. See `OVERRIDABLE_INI_KEYS` for why the list is one
    # entry long, and `_extract_ini_overrides` for why extraction precedes the splice.
    #
    # MECHANISM M1 of the Phase 4 Task 1b sweep, and the only architectural one of the nine.
    # rustest is RIGHT to refuse a collection-changing flag it does not implement --
    # silently ignoring `--doctest-modules` would manufacture a quiet divergence, and that
    # flag stays unimplemented here. The defect was that there was no way to say "read this
    # project's config except that key": no `-o`, no `--override-ini`, no `-c`. So a project
    # with such a flag in its ini `addopts` was a hard BLOCK rather than a degraded run --
    # measured on humanize, where `--doctest-modules` is provably inert (784 tests collected
    # with and without it) and still cost the entire suite, because rustest fails while
    # parsing the ini it found by walking up from its own cwd and the target repo is
    # read-only to the caller.
    raw, ini_overrides, malformed_overrides = _extract_ini_overrides(raw)
    if malformed_overrides:
        print(
            "ERROR: -o/--override-ini takes OPTION=VALUE; got "
            + ", ".join(repr(item) for item in malformed_overrides),
            file=sys.stderr,
        )
        return 4
    unsupported = sorted(set(ini_overrides) - OVERRIDABLE_INI_KEYS)
    if unsupported and parser.fallback:
        # pytest accepts any ini key, so this is "an option rustest does not have".
        return _hand_to_pytest(
            parser,
            raw,
            user_argv,
            [f"-o {key} is an ini option rustest cannot override" for key in unsupported],
        )
    if unsupported:
        print(
            "ERROR: -o/--override-ini cannot override "
            + ", ".join(repr(key) for key in unsupported)
            + f" (supported: {', '.join(sorted(OVERRIDABLE_INI_KEYS))})."
            + " Every other ini key is read by the engine's own config resolver, which has"
            + " no override channel -- accepting the flag and ignoring it would be a silent"
            + " no-op.",
            file=sys.stderr,
        )
        return 4

    try:
        resolved = _config_for_addopts(raw, parser)
    except _ArgError as exc:
        return _rejected(parser, exc, raw, user_argv)
    if "addopts" in ini_overrides:
        # `shlex.split` for the same reason the engine's `getini_args` does shell-like
        # splitting: `addopts` is one ini STRING, and `-k "not slow"` has to survive as two
        # arguments rather than three.
        import shlex

        addopts = shlex.split(ini_overrides["addopts"])
    else:
        addopts = [str(opt) for opt in resolved.get("addopts", ())]
    # pytest's order (`Config._preparse`): ini `addopts`, then `PYTEST_ADDOPTS`, then the command
    # line. rustest reads the variable only while a session may still go to pytest, so that a
    # `--no-pytest-fallback` run (the conformance harness, the benchmarks) is what it was.
    import shlex

    parser.fallback = "--no-pytest-fallback" not in addopts + raw
    env_opts = shlex.split(os.environ.get("PYTEST_ADDOPTS", "")) if parser.fallback else []
    origin = {tok: " (from PYTEST_ADDOPTS)" for tok in env_opts}
    origin |= {
        tok: f" (from addopts in {resolved.get('config_file')})"
        if resolved.get("config_file")
        else " (from addopts)"
        for tok in addopts
    }
    # `-o KEY=VALUE` in `addopts` or `PYTEST_ADDOPTS` is pytest's to apply: rustest reads only
    # the command line's, so these are lifted out here rather than parsed and forgotten.
    addopts, ini_a, _ = _extract_ini_overrides(addopts)
    env_opts, ini_e, _ = _extract_ini_overrides(env_opts)
    late_ini = [
        f"-o {key} is an ini option rustest cannot apply (from {where})"
        for where, found in (("addopts", ini_a), ("PYTEST_ADDOPTS", ini_e))
        for key in found
    ]
    raw = addopts + env_opts + raw
    parser.fallback = "--no-pytest-fallback" not in raw
    # pytest appends these two lines to **every** usage error, not only to one sourced from
    # a config file: they come from `_parser.extra_info`, which `Config._initini` fills in as
    # soon as rootdir is known (l. 1254-1255). Measured — a mistyped flag typed at the prompt
    # prints them too. `inifile:` is dropped rather than printed as `None` when there is no
    # config file, which is the one place this is deliberately terser than the oracle.
    config_file = resolved.get("config_file")
    rootdir = resolved.get("rootdir")
    parser.config_source_hint = (
        "\n".join(
            line
            for line in (
                f"  inifile: {config_file}" if config_file else "",
                f"  rootdir: {rootdir}",
            )
            if line
        )
        if rootdir
        else None
    )

    # Cosmetic pytest flags are dropped **before** the removed-flag scan and before parsing,
    # so `addopts = "-ra --tb=short"` is a note rather than an exit 4. See
    # `IGNORED_PYTEST_FLAGS` for what qualifies and why the list is short.
    # `-p NAME` is on the ignored list, so its names are read before it is dropped.
    plugin_names = _plugin_names(raw)
    blocked_plugins = _blocked_plugins(raw)
    raw, ignored = _drop_ignored_pytest_flags(raw)
    # While the session may still go to pytest, which honours these flags, the NOTEs wait:
    # they are printed only once the run has stayed with rustest.
    deferred = ignored if parser.fallback else []
    for flag in () if parser.fallback else ignored:
        print(
            f"NOTE: {flag} is a pytest reporting option rustest does not implement;"
            + " it was ignored.",
            file=sys.stderr,
        )

    def finish(code: int) -> int:
        for flag in deferred:
            print(
                f"NOTE: {flag} is a pytest reporting option rustest does not implement;"
                + " it was ignored.",
                file=sys.stderr,
            )
        return code

    # Scanned *after* the splice, so a repo whose `addopts` still carries a removed flag is
    # told so instead of having it swallowed by `nargs="*"` as a path.
    for flag, message in REMOVED_FLAGS.items():
        if flag in raw or any(arg.startswith(flag + "=") for arg in raw):
            # pytest's exit 4 (USAGE_ERROR), because that is exactly what this is.  argparse
            # would say 2, which in this CLI already means "collection error".
            source = f"\n{parser.config_source_hint}" if flag in addopts else ""
            print(f"ERROR: {message}{source}", file=sys.stderr)
            return 4

    # `--llm-schema` is a **query**, not a run: it answers "what does --llm emit?" and exits,
    # which is what `--help` is and is why it is answered here rather than off `args`.
    #
    # *Before* `parse_args`, deliberately. A tool discovering the output format should not have
    # to be standing in a project rustest can run -- and after the `addopts` splice above, a
    # repo whose ini carries a flag this CLI refuses (`--doctest-modules`, say) would make
    # `parse_args` exit 4 before any `args.llm_schema` branch could fire. Scanning `raw`
    # *after* the splice rather than before also means the flag works from `addopts`, which is
    # where a project that always wants the schema would put it.
    #
    # The argument is still registered on the parser (see `build_parser`): that is what puts it
    # in `--help` and what keeps `--llm-schema` from being read as a path by `nargs="*"` on the
    # `_config_for_addopts` probe.
    if "--llm-schema" in raw:
        from ._llm_schema import schema_json

        print(schema_json())
        return 0

    # `--version` is the same kind of thing as `--llm-schema` -- a query answered without a
    # run -- so it is answered in the same place and for the same three reasons: it must work
    # from a directory holding nothing this runner can collect, it must survive an `addopts`
    # carrying a flag this CLI refuses (which would exit 4 inside `parse_args` before any
    # `args.version` branch could fire), and it is registered on the parser above so `--help`
    # lists it and `nargs="*"` does not read it as a path.
    #
    # Shaped after `pytest --version`, measured on pytest 8.4.2: `pytest 8.4.2` on stdout,
    # exit 0.
    if "--version" in raw:
        from ._version import package_version

        print(f"rustest {package_version()}")
        return 0

    # What rustest cannot honour and pytest can, known before anything runs: `-p NAME`, and
    # the two environment variables pytest reads and rustest does not.
    handoff = (
        late_ini
        + [
            f"-p {name} loads a pytest plugin{origin.get(name) or origin.get('-p' + name, '')}"
            for name in plugin_names
        ]
        + ["PYTEST_PLUGINS is set, and rustest ignores it"] * bool(os.environ.get("PYTEST_PLUGINS"))
        # rustest applies `filterwarnings` itself, so turning pytest's warnings plugin off
        # is a request only pytest can honour.
        + ["-p no:warnings turns off the warning filters rustest applies"]
        * ("warnings" in blocked_plugins)
        + _autoloaded_plugins(blocked_plugins)
        if parser.fallback
        else []
    )
    if handoff:
        return _hand_to_pytest(parser, raw, user_argv, handoff, "they will be ignored")

    try:
        args = parser.parse_args(raw)
    except _ArgError as exc:
        return _rejected(parser, exc, raw, user_argv, origin)

    # Inert-flag refusals, in the shape `--cov-report`-without-`--cov` already uses below.
    # `--llm-full` alone changes nothing about a human run, and accepting it would be the
    # silent no-op this CLI refuses everywhere else.
    if args.llm_full and not args.llm:
        print(
            "ERROR: --llm-full only affects --llm output, and --llm was not given, so it"
            + " would do nothing. Add --llm or drop --llm-full.",
            file=sys.stderr,
        )
        return 4

    # `--cov-branch` must be refused even when it is the *only* coverage flag given: a
    # `--cov-branch` with no `--cov` is still a request rustest cannot honour, and answering
    # it with a silent nothing is the failure mode `_coverage.branch_refusal` exists to
    # name.
    if args.cov_branch:
        from ._coverage import branch_refusal

        print(f"ERROR: {branch_refusal()}", file=sys.stderr)
        return 4
    if args.cov is None and args.cov_report:
        print(
            "ERROR: --cov-report was given without --cov, so there is nothing to report on."
            + " Add --cov (optionally --cov=PATH).",
            file=sys.stderr,
        )
        return 4

    # argparse hands back the *default object itself* when no positional was supplied, so
    # identity separates `rustest` (no argument, and therefore `testpaths` decides the roots,
    # as in pytest) from an explicit `.` (an argument, which suppresses `testpaths`).
    # Forwarding the default would erase that distinction and quietly diverge from pytest on
    # every `testpaths` project.
    paths = [] if args.paths is parser.get_default("paths") else list(args.paths)

    if args.watch:
        try:
            return _watch_main(args, paths, resolved)
        except NeedsPytest as needs:
            from ._watch import needs_pytest

            print(needs_pytest(needs.reasons), file=sys.stderr)
            return 4

    if args.collect_only:
        if args.llm:
            # Collect-only's stdout is already a machine format -- one node id per line -- and
            # it produces no result, no status and no capture, so there is nothing for a
            # `fail` or `summary` line to say. Answering with a stream of `meta` and an
            # all-zero `summary` would be a well-formed lie about a run that never happened.
            print(
                "ERROR: --llm and --collect-only ask for different things: collect-only"
                + " runs no test, so there is no result to report as JSONL. Its stdout is"
                + " already machine-readable -- one node id per line. Drop one.",
                file=sys.stderr,
            )
            return 4
        if args.cov is not None:
            # Collect-only runs no test, so the only lines it could report are import-time
            # ones. Refused rather than answered with a number that means nothing.
            print(
                "ERROR: --cov and --collect-only ask for different things: collect-only"
                + " runs no test, so there is no execution to measure. Drop one.",
                file=sys.stderr,
            )
            return 4
        if _last_failed_mode(args) != "none":
            # pytest's `--lf` filters at collection time in a way that depends on whether any
            # failure was recorded at module level (probed: three shapes differ from a plain
            # filter).  Until the engine applies its own last-failed logic to a collect, the
            # honest answer is pytest's.
            reason = (
                "--collect-only with --lf/--ff lists what pytest's last-failed filter keeps,"
                + " which rustest's collection does not reproduce yet"
            )
            if not args.pytest_fallback:
                print(
                    f"ERROR: {reason}. Drop --no-pytest-fallback to let pytest answer it.",
                    file=sys.stderr,
                )
                return 4
            return _hand_to_pytest(
                parser, raw, user_argv, [reason], "the last-failed option would be ignored"
            )
        try:
            return finish(
                collect_only(
                    paths=paths,
                    workers=args.workers,
                    keyword=args.pattern,
                    mark_expr=args.mark_expr,
                    codeblocks=args.enable_codeblocks,
                    pytest_fallback=args.pytest_fallback,
                )
            )
        except NeedsPytest as needs:
            return _hand_to_pytest(parser, raw, user_argv, needs.reasons, needs.optout)

    try:
        code = _run_with(args, paths, keyword=args.pattern, report_json=args.report_json)
    except NeedsPytest as needs:
        return _hand_to_pytest(parser, raw, user_argv, needs.reasons, needs.optout)
    return finish(code)
