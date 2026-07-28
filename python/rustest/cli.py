"""Command line interface helpers.

**As of 0.17 the default engine is v2.**  ``rustest <paths>`` with no mode flag runs the v2
spine end to end — config resolution, the file walk, a worker pool that collects and then
executes, pytest's exit codes — with the pytest compatibility shim installed
unconditionally.  ``--v1`` opts back into the legacy engine and ``--v2`` is a no-op alias
kept so existing scripts and CI files keep working.

Two rules keep the two engines from contaminating each other, and both are structural rather
than conventional:

* the v2 branch **short-circuits at the top of** :func:`main`, before any v1 option is
  interpreted and before ``core.run`` can touch v1's process-global runtime config;
* every option the two share is *forwarded verbatim* rather than re-interpreted here, so
  there is one owner per behaviour (v2 owns pytest's ``-k``/``-m`` grammar and its usage
  errors, v1 owns its own).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from .core import run, terminal_columns, v2_collect_only, v2_run

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
    including ``--v2-collect-only`` and every worker subprocess, none of which ever print
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
        """The theme and decolor function, importing ``_colorize`` on first use only."""
        if self._resolved is None:
            from _colorize import can_colorize, decolor, get_theme

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


#: ``--pytest-compat`` was deleted at the flip: the shim it enabled is now always installed,
#: so the flag could only ever be a no-op or a lie.  It is rejected **loudly** rather than
#: ignored — a CI file still passing it deserves to be told the behaviour it asked for is now
#: the default, not to have the argument silently swallowed by ``nargs="*"`` as a path.
REMOVED_FLAGS: dict[str, str] = {
    "--pytest-compat": (
        "--pytest-compat has been removed: the pytest compatibility shim is now always"
        + " installed, so every run is a compat run. Drop the flag."
        + " See CHANGELOG.md (0.17.0, 'the flip')."
    ),
}


def is_ci_environment() -> bool:
    """Detect if running in a CI environment.

    Checks for common CI environment variables across all major providers:
    - GitHub Actions
    - GitLab CI
    - CircleCI
    - Travis CI
    - Jenkins
    - Azure Pipelines
    - Bitbucket Pipelines
    - TeamCity
    - And many others

    Returns:
        True if running in CI, False otherwise
    """
    # Check for common CI environment variables
    # This is the most reliable method across all CI providers
    ci_vars = [
        "CI",  # Generic CI indicator (GitHub Actions, Travis, CircleCI, GitLab)
        "CONTINUOUS_INTEGRATION",  # Travis CI, CircleCI
        "GITHUB_ACTIONS",  # GitHub Actions
        "GITLAB_CI",  # GitLab CI
        "CIRCLECI",  # CircleCI
        "TRAVIS",  # Travis CI
        "JENKINS_HOME",  # Jenkins
        "JENKINS_URL",  # Jenkins
        "BUILDKITE",  # Buildkite
        "DRONE",  # Drone CI
        "TEAMCITY_VERSION",  # TeamCity
        "TF_BUILD",  # Azure Pipelines
        "BITBUCKET_BUILD_NUMBER",  # Bitbucket Pipelines
        "CODEBUILD_BUILD_ID",  # AWS CodeBuild
        "APPVEYOR",  # AppVeyor
    ]

    return any(os.getenv(var) for var in ci_vars)


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

    def error(self, message: str) -> "NoReturn":
        self.print_usage(sys.stderr)
        hint = f"\n{self.config_source_hint}" if self.config_source_hint else ""
        self.exit(4, f"{self.prog}: error: {message}{hint}\n")


def build_parser() -> _Parser:
    parser = _Parser(
        prog="rustest",
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
        help="Number of worker processes to use (default: one per CPU).",
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
        help="Print one PASSED/FAILED line per test.",
    )
    _ = parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print only the summary line.",
    )
    _ = parser.add_argument(
        "--ascii",
        action="store_true",
        help="Use ASCII characters instead of Unicode symbols for output (--v1 only).",
    )
    _ = parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help=(
            "When to use colored output: auto (default, detect CI), always, or never "
            "(--v1 only; the default engine's output is not colored yet)."
        ),
    )
    _ = parser.add_argument(
        "--no-codeblocks",
        dest="enable_codeblocks",
        action="store_false",
        help="Disable code block tests from markdown files.",
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
    #
    # `--cov` is a **v2-only** surface. `--v1` with it is refused in `main`, rather than
    # ignored, for the same reason `--v2-collect-only` is: silently running unmeasured is the
    # worst possible answer to "measure this".
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
    _ = parser.add_argument(
        "--v2-collect-only",
        action="store_true",
        dest="v2_collect_only",
        help=(
            "Collect tests and print their node ids one per line, without running anything. "
            "Honours -k, -m and -n. Exits 0 with tests, 5 with none, 2 on collection errors. "
            "None of the other options apply."
        ),
    )
    _ = parser.add_argument(
        "--v2",
        action="store_true",
        dest="v2",
        help="Deprecated no-op: the v2 engine is the default. Accepted so old scripts keep working.",
    )
    _ = parser.add_argument(
        "--v1",
        action="store_true",
        dest="v1",
        help="Run the legacy v1 engine. Removed in a future release.",
    )
    parser.set_defaults(
        capture_output=True,
        enable_codeblocks=True,
        last_failed=False,
        failed_first=False,
        fail_fast=False,
        quiet=False,
        report_json=None,
        v2_collect_only=False,
        v2=False,
        v1=False,
        cov=None,
        cov_report=None,
        cov_branch=False,
    )
    return parser


def _last_failed_mode(args: argparse.Namespace) -> str:
    """``--lf`` / ``--ff`` as the one string both engines' cores take.

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
        return json.loads(rust.v2_resolve_config(os.getcwd(), probe))
    except Exception:  # noqa: BLE001 - re-raised by the engine, with its own message
        return {}


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    parser = build_parser()
    # `addopts` is prepended to argv, which is what `Config._preparse` does
    # (`args[:] = self._validate_args(self.getini("addopts"), "via addopts config") + args`,
    # l. 1394-1397). *Prepended*, not appended, so an explicit command-line flag still wins a
    # last-one-wins option, and paths in `addopts` come first.
    #
    # NOT modelled: `PYTEST_ADDOPTS`, which pytest splices ahead of the ini value (l.
    # 1386-1392). Recorded as a limitation rather than added, because it would change the
    # behaviour of every rustest invocation in an environment that happens to export it —
    # including this repo's own conformance harness — for a mechanism no ledgered suite uses.
    resolved = _config_for_addopts(raw, parser)
    addopts = [str(opt) for opt in resolved.get("addopts", ())]
    raw = addopts + raw
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

    # Scanned *after* the splice, so a repo whose `addopts` still carries a removed flag is
    # told so instead of having it swallowed by `nargs="*"` as a path.
    for flag, message in REMOVED_FLAGS.items():
        if flag in raw or any(arg.startswith(flag + "=") for arg in raw):
            # pytest's exit 4 (USAGE_ERROR), because that is exactly what this is.  argparse
            # would say 2, which in this CLI already means "collection error".
            source = f"\n{parser.config_source_hint}" if flag in addopts else ""
            print(f"ERROR: {message}{source}", file=sys.stderr)
            return 4

    args = parser.parse_args(raw)

    # Checked before the engine split, because every one of these is a refusal whichever
    # engine was asked for, and because `--cov-branch` must be refused even when it is the
    # *only* coverage flag given (a `--cov-branch` with no `--cov` is still a request rustest
    # cannot honour, and answering it with a silent nothing is the failure mode
    # `_v2_coverage.branch_refusal` exists to name).
    if args.cov_branch:
        from ._v2_coverage import branch_refusal

        print(f"ERROR: {branch_refusal()}", file=sys.stderr)
        return 4
    if args.cov is not None and args.v1:
        print(
            "ERROR: --cov is a v2-engine surface and cannot be combined with --v1"
            + " (the legacy engine has no coverage support). Drop --v1.",
            file=sys.stderr,
        )
        return 4
    if args.cov is None and args.cov_report:
        print(
            "ERROR: --cov-report was given without --cov, so there is nothing to report on."
            + " Add --cov (optionally --cov=PATH).",
            file=sys.stderr,
        )
        return 4

    if args.v1:
        if args.v2_collect_only:
            # `--v2-collect-only` is a v2 surface; there is no v1 collect-only mode. Before
            # this guard the flag was simply ignored on the v1 branch and the suite **ran** —
            # the single worst way to answer "list the tests, do not run anything", because
            # it does the opposite silently and a user pointing it at a suite with side
            # effects finds out afterwards.
            print(
                "ERROR: --v2-collect-only is a v2-engine surface and cannot be combined with"
                + " --v1 (the legacy engine has no collect-only mode). Drop --v1.",
                file=sys.stderr,
            )
            return 4
        if args.v2:
            print(
                "ERROR: --v1 and --v2 select different engines and cannot be combined.",
                file=sys.stderr,
            )
            return 4
        return _run_v1(args)

    # argparse hands back the *default object itself* when no positional was supplied, so
    # identity separates `rustest` (no argument, and therefore `testpaths` decides the roots,
    # as in pytest) from an explicit `.` (an argument, which suppresses `testpaths`).
    # Forwarding the default would erase that distinction and quietly diverge from pytest on
    # every `testpaths` project.
    paths = [] if args.paths is parser.get_default("paths") else list(args.paths)

    if args.v2_collect_only:
        if args.cov is not None:
            # Collect-only runs no test, so the only lines it could report are import-time
            # ones. Refused rather than answered with a number that means nothing.
            print(
                "ERROR: --cov and --v2-collect-only ask for different things: collect-only"
                + " runs no test, so there is no execution to measure. Drop one.",
                file=sys.stderr,
            )
            return 4
        return v2_collect_only(
            paths=paths,
            workers=args.workers,
            keyword=args.pattern,
            mark_expr=args.mark_expr,
            codeblocks=args.enable_codeblocks,
        )

    if args.v2:
        print(
            "NOTE: --v2 is a no-op; the v2 engine is the default."
            + " The flag will be removed in a future release.",
            file=sys.stderr,
        )
    return v2_run(
        paths=paths,
        workers=args.workers,
        keyword=args.pattern,
        mark_expr=args.mark_expr,
        report_json=args.report_json,
        fail_fast=args.fail_fast,
        last_failed_mode=_last_failed_mode(args),
        capture=args.capture_output,
        codeblocks=args.enable_codeblocks,
        verbosity=_verbosity(args),
        cov=args.cov,
        cov_report=args.cov_report,
    )


def _run_v1(args: argparse.Namespace) -> int:
    """The legacy engine, reached only through ``--v1``.

    Byte-identical to the pre-flip default path apart from the banner — **including**
    ``pytest_compat=False``.  That looks inconsistent next to "the shim is always installed"
    and is deliberate: ``--v1`` exists so a suite that the new engine cannot yet run has
    somewhere to go, and it can only serve that purpose if it behaves exactly as it did
    before the flip.  Turning the shim on here would also silently disable v1's markdown
    tier (`src/discovery.rs` l. 358 refuses code blocks in compat mode) and print v1's
    compat banner — two behaviour changes on a path whose whole contract is "unchanged".
    v1 is frozen, not maintained.
    """
    print(
        "NOTE: --v1 selects the legacy engine, removed in a future release."
        + " Run without --v1 for the default (v2) engine."
        + " (If a message below suggests --pytest-compat, that flag no longer exists:"
        + " run without --v1 instead, where compatibility is always on.)",
        file=sys.stderr,
    )

    if args.color == "auto":
        # Auto-detect: colors enabled locally, disabled in CI
        use_color = not is_ci_environment()
    elif args.color == "always":
        use_color = True
    else:  # "never"
        use_color = False

    report = run(
        paths=list(args.paths),
        pattern=args.pattern,
        mark_expr=args.mark_expr,
        workers=args.workers,
        capture_output=args.capture_output,
        enable_codeblocks=args.enable_codeblocks,
        last_failed_mode=_last_failed_mode(args),
        fail_fast=args.fail_fast,
        pytest_compat=False,
        verbose=args.verbose,
        ascii=args.ascii,
        no_color=not use_color,
    )
    if args.report_json:
        from .json_report import write_json_report

        write_json_report(report, args.report_json)
    # Note: Rust now handles all output rendering with real-time progress
    # The Python _print_report() function is no longer called

    # Exit codes match pytest:
    # 0 = all tests passed
    # 1 = some tests failed
    # 2 = collection errors (syntax errors, import errors, etc.)
    if len(report.collection_errors) > 0:
        return 2
    elif report.failed > 0:
        return 1
    else:
        return 0
