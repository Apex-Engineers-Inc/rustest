"""Command line interface helpers."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Sequence
from io import StringIO

from .reporting import RunReport, TestResult
from .core import run


# ANSI color codes
class _ColorsNamespace:
    """Namespace for ANSI color codes."""

    def __init__(self) -> None:  # pyright: ignore[reportMissingSuperCall]
        self.green = "\033[92m"
        self.red = "\033[91m"
        self.yellow = "\033[93m"
        self.cyan = "\033[96m"
        self.bold = "\033[1m"
        self.dim = "\033[2m"
        self.reset = "\033[0m"

    def disable(self) -> None:
        """Disable all colors."""
        self.green = ""
        self.red = ""
        self.yellow = ""
        self.cyan = ""
        self.bold = ""
        self.dim = ""
        self.reset = ""


Colors = _ColorsNamespace()


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
    _ = parser.set_defaults(
        capture_output=True,
        color=True,
        enable_codeblocks=True,
        last_failed=False,
        failed_first=False,
        fail_fast=False,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Disable colors if requested
    if not args.color:
        Colors.disable()

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
    )
    _print_report(report, verbose=args.verbose, ascii_mode=args.ascii)
    return 0 if report.failed == 0 else 1


def _print_report(report: RunReport, verbose: bool = False, ascii_mode: bool = False) -> None:
    """Print test report with configurable output format.

    Args:
        report: The test run report
        verbose: If True, show hierarchical verbose output (vitest-style)
        ascii_mode: If True, use ASCII characters instead of Unicode symbols
    """
    if verbose:
        _print_verbose_report(report, ascii_mode)
    else:
        _print_default_report(report, ascii_mode)

    # Print summary line with colors
    passed_str = (
        f"{Colors.green}{report.passed} passed{Colors.reset}"
        if report.passed > 0
        else f"{report.passed} passed"
    )
    failed_str = (
        f"{Colors.red}{report.failed} failed{Colors.reset}"
        if report.failed > 0
        else f"{report.failed} failed"
    )
    skipped_str = (
        f"{Colors.yellow}{report.skipped} skipped{Colors.reset}"
        if report.skipped > 0
        else f"{report.skipped} skipped"
    )

    summary = (
        f"\n{Colors.bold}{report.total} tests:{Colors.reset} "
        f"{passed_str}, "
        f"{failed_str}, "
        f"{skipped_str} in {Colors.dim}{report.duration:.3f}s{Colors.reset}"
    )
    sys.stdout.write(f"{summary}\n")


def _split_result_name(name: str) -> tuple[str | None, str]:
    """Return the containing class (if present) and display name for a test result."""

    if "::" in name:
        parent, leaf = name.rsplit("::", 1)
        return (parent or None, leaf)
    if "." in name:
        parent, leaf = name.rsplit(".", 1)
        return (parent or None, leaf)
    return (None, name)


def _print_default_report(report: RunReport, ascii_mode: bool) -> None:
    """Print pytest-style progress indicators followed by failure details."""
    # Define symbols
    if ascii_mode:
        # pytest-style: . (pass), F (fail), s (skip)
        pass_symbol = "."
        fail_symbol = "F"
        skip_symbol = "s"
    else:
        # Unicode symbols (no spaces, with colors)
        pass_symbol = f"{Colors.green}✓{Colors.reset}"
        fail_symbol = f"{Colors.red}✗{Colors.reset}"
        skip_symbol = f"{Colors.yellow}⊘{Colors.reset}"

    # Print progress indicators
    progress_symbols: list[str] = []
    for result in report.results:
        if result.status == "passed":
            progress_symbols.append(pass_symbol)
        elif result.status == "failed":
            progress_symbols.append(fail_symbol)
        elif result.status == "skipped":
            progress_symbols.append(skip_symbol)
    if progress_symbols:
        sys.stdout.write("".join(progress_symbols))
    sys.stdout.write("\n")

    # Print failure details
    failures = [r for r in report.results if r.status == "failed"]
    if failures:
        separator = f"{Colors.red}{'=' * 70}{Colors.reset}"
        failure_header = f"{Colors.bold}FAILURES{Colors.reset}"
        details_chunks = [f"\n{separator}\n", f"{failure_header}\n", f"{separator}\n"]
        for result in failures:
            details_chunks.append(
                f"\n{Colors.bold}{result.name}{Colors.reset} ({Colors.cyan}{result.path}{Colors.reset})\n"
            )
            details_chunks.append(f"{Colors.red}{'-' * 70}{Colors.reset}\n")
            if result.message:
                details_chunks.append(result.message.rstrip("\n"))
                details_chunks.append("\n")
        sys.stdout.write("".join(details_chunks))


def _print_verbose_report(report: RunReport, ascii_mode: bool) -> None:
    """Print vitest-style hierarchical output with nesting and timing."""
    # Define symbols
    if ascii_mode:
        pass_symbol = "PASS"
        fail_symbol = "FAIL"
        skip_symbol = "SKIP"
    else:
        pass_symbol = f"{Colors.green}✓{Colors.reset}"
        fail_symbol = f"{Colors.red}✗{Colors.reset}"
        skip_symbol = f"{Colors.yellow}⊘{Colors.reset}"

    # Group tests by file path and organize hierarchically
    tests_by_file: dict[str, list[tuple[str | None, str, TestResult]]] = defaultdict(list)
    for result in report.results:
        class_name, display_name = _split_result_name(result.name)
        tests_by_file[result.path].append((class_name, display_name, result))

    # Print hierarchical structure
    buffer = StringIO()
    symbol_by_status = {
        "passed": pass_symbol,
        "failed": fail_symbol,
        "skipped": skip_symbol,
    }
    for file_path in sorted(tests_by_file.keys()):
        buffer.write(f"\n{Colors.bold}{file_path}{Colors.reset}\n")

        # Group tests by class within this file
        tests_by_class: dict[str | None, list[tuple[str, TestResult]]] = defaultdict(list)
        for class_name, display_name, result in tests_by_file[file_path]:
            tests_by_class[class_name].append((display_name, result))

        # Print tests organized by class
        for class_name in sorted(tests_by_class.keys(), key=lambda x: (x is None, x)):
            # Print class name if present
            if class_name:
                buffer.write(f"  {Colors.cyan}{class_name}{Colors.reset}\n")

            for display_name, result in tests_by_class[class_name]:
                symbol = symbol_by_status.get(result.status, "?")

                # Indent based on whether it's in a class
                indent = "    " if class_name else "  "

                # Print with symbol, name, and timing
                duration_str = f"{Colors.dim}{result.duration * 1000:.0f}ms{Colors.reset}"
                buffer.write(f"{indent}{symbol} {display_name} {duration_str}\n")

                # Show error message for failures
                if result.status == "failed" and result.message:
                    error_lines = result.message.rstrip().split("\n")
                    for line in error_lines:
                        buffer.write(f"{indent}  {line}\n")
    sys.stdout.write(buffer.getvalue())
