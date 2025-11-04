"""Command line interface helpers."""

from __future__ import annotations

import argparse
from typing import Sequence

from ._reporting import RunReport
from .core import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rustest",
        description="Run Python tests at blazing speed with a Rust powered core.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=(".",),
        help="Files or directories to collect tests from.",
    )
    parser.add_argument(
        "-k",
        "--pattern",
        help="Substring to filter tests by (case insensitive).",
    )
    parser.add_argument(
        "-n",
        "--workers",
        type=int,
        help="Number of worker slots to use (experimental).",
    )
    parser.add_argument(
        "--no-capture",
        dest="capture_output",
        action="store_false",
        help="Do not capture stdout/stderr during test execution.",
    )
    parser.set_defaults(capture_output=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = run(
        paths=tuple(args.paths),
        pattern=args.pattern,
        workers=args.workers,
        capture_output=args.capture_output,
    )
    _print_report(report)
    return 0 if report.failed == 0 else 1


def _print_report(report: RunReport) -> None:
    for result in report.results:
        status = result.status.upper()
        line = f"{status:>7} {result.duration:>7.3f}s {result.name}"
        print(line)
        if result.status == "failed" and result.message:
            print("-" * len(line))
            print(result.message.rstrip())
    summary = (
        f"{report.total} tests: "
        f"{report.passed} passed, "
        f"{report.failed} failed, "
        f"{report.skipped} skipped in {report.duration:.3f}s"
    )
    print(summary)
