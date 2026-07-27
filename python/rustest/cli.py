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

from .core import run, v2_collect_only, v2_run

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rustest",
        description="Run Python tests at blazing speed with a Rust powered core.",
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


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    for flag, message in REMOVED_FLAGS.items():
        if flag in raw or any(arg.startswith(flag + "=") for arg in raw):
            # pytest's exit 4 (USAGE_ERROR), because that is exactly what this is.  argparse
            # would say 2, which in this CLI already means "collection error".
            print(f"ERROR: {message}", file=sys.stderr)
            return 4

    parser = build_parser()
    args = parser.parse_args(raw)

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
