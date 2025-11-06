"""Command line interface helpers."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ._reporting import RunReport
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
    _ = parser.set_defaults(capture_output=True)
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

    # Print summary line
    summary = (
        f"\n{report.total} tests: "
        f"{report.passed} passed, "
        f"{report.failed} failed, "
        f"{report.skipped} skipped in {report.duration:.3f}s"
    )
    print(summary)


def _print_default_report(report: RunReport, ascii_mode: bool) -> None:
    """Print pytest-style progress indicators followed by failure details."""
    # Define symbols
    if ascii_mode:
        # pytest-style: . (pass), F (fail), s (skip)
        pass_symbol = "."
        fail_symbol = "F"
        skip_symbol = "s"
        separator = ""
    else:
        # Unicode symbols with spaces
        pass_symbol = "✓"
        fail_symbol = "✗"
        skip_symbol = "⊘"
        separator = " "

    # Print progress indicators
    for result in report.results:
        if result.status == "passed":
            print(pass_symbol, end=separator)
        elif result.status == "failed":
            print(fail_symbol, end=separator)
        elif result.status == "skipped":
            print(skip_symbol, end=separator)
    print()  # Newline after progress indicators

    # Print failure details
    failures = [r for r in report.results if r.status == "failed"]
    if failures:
        print("\n" + "=" * 70)
        print("FAILURES")
        print("=" * 70)
        for result in failures:
            print(f"\n{result.name} ({result.path})")
            print("-" * 70)
            if result.message:
                print(result.message.rstrip())


def _print_verbose_report(report: RunReport, ascii_mode: bool) -> None:
    """Print vitest-style hierarchical output with nesting and timing."""
    # Define symbols
    if ascii_mode:
        pass_symbol = "PASS"
        fail_symbol = "FAIL"
        skip_symbol = "SKIP"
    else:
        pass_symbol = "✓"
        fail_symbol = "✗"
        skip_symbol = "⊘"

    # Group tests by file path and organize hierarchically
    from collections import defaultdict
    tests_by_file: dict[str, list] = defaultdict(list)
    for result in report.results:
        tests_by_file[result.path].append(result)

    # Print hierarchical structure
    for file_path in sorted(tests_by_file.keys()):
        print(f"\n{file_path}")

        # Group tests by class within this file
        tests_by_class: dict[str | None, list] = defaultdict(list)
        for result in tests_by_file[file_path]:
            # Parse test name to extract class if present
            # Format can be: "test_name" or "ClassName.test_name" or "module::Class::test"
            if "::" in result.name:
                parts = result.name.split("::")
                class_name = "::".join(parts[:-1]) if len(parts) > 1 else None
            elif "." in result.name:
                parts = result.name.split(".")
                class_name = parts[0] if len(parts) > 1 else None
            else:
                class_name = None
            tests_by_class[class_name].append((result, class_name))

        # Print tests organized by class
        for class_name in sorted(tests_by_class.keys(), key=lambda x: (x is None, x)):
            # Print class name if present
            if class_name:
                print(f"  {class_name}")

            for result, _ in tests_by_class[class_name]:
                # Get symbol based on status
                if result.status == "passed":
                    symbol = pass_symbol
                elif result.status == "failed":
                    symbol = fail_symbol
                elif result.status == "skipped":
                    symbol = skip_symbol
                else:
                    symbol = "?"

                # Extract just the test method name
                if "::" in result.name:
                    display_name = result.name.split("::")[-1]
                elif "." in result.name:
                    display_name = result.name.split(".")[-1]
                else:
                    display_name = result.name

                # Indent based on whether it's in a class
                indent = "    " if class_name else "  "

                # Print with symbol, name, and timing
                duration_str = f"{result.duration * 1000:.0f}ms"
                print(f"{indent}{symbol} {display_name} {duration_str}")

                # Show error message for failures
                if result.status == "failed" and result.message:
                    error_lines = result.message.rstrip().split("\n")
                    for line in error_lines:
                        print(f"{indent}  {line}")
