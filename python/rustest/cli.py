"""Command line interface helpers."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .core import run


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
        help="Number of worker slots to use (experimental).",
    )
    _ = parser.add_argument(
        "--no-capture",
        dest="capture_output",
        action="store_false",
        help="Do not capture stdout/stderr during test execution.",
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show verbose output with hierarchical test structure.",
    )
    _ = parser.add_argument(
        "--ascii",
        action="store_true",
        help="Use ASCII characters instead of Unicode symbols for output.",
    )
    _ = parser.add_argument(
        "--no-color",
        dest="color",
        action="store_false",
        help="Disable colored output.",
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
        "--pytest-compat",
        action="store_true",
        dest="pytest_compat",
        help="Enable pytest compatibility mode - allows running existing pytest tests without modifying imports.",
    )
    _ = parser.set_defaults(
        capture_output=True,
        color=True,
        enable_codeblocks=True,
        last_failed=False,
        failed_first=False,
        fail_fast=False,
        pytest_compat=False,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Determine last_failed_mode
    if args.last_failed:
        last_failed_mode = "only"
    elif args.failed_first:
        last_failed_mode = "first"
    else:
        last_failed_mode = "none"

    report = run(
        paths=list(args.paths),
        pattern=args.pattern,
        mark_expr=args.mark_expr,
        workers=args.workers,
        capture_output=args.capture_output,
        enable_codeblocks=args.enable_codeblocks,
        last_failed_mode=last_failed_mode,
        fail_fast=args.fail_fast,
        pytest_compat=args.pytest_compat,
        verbose=args.verbose,
        ascii=args.ascii,
        no_color=not args.color,
    )
    # Note: Rust now handles all output rendering with real-time progress
    # The Python _print_report() function is no longer called
    return 0 if report.failed == 0 else 1
