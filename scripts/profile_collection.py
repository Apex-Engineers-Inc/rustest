#!/usr/bin/env python3
"""
Profile all stages of rustest execution.

Usage:
    python scripts/profile_collection.py [test_paths...]

Examples:
    python scripts/profile_collection.py tests/
    python scripts/profile_collection.py tests/ examples/tests/
    python scripts/profile_collection.py . --profile

Output includes:
    - Phase breakdown (collection, execution, per-file timing)
    - Comparison with pytest
    - Detailed cProfile breakdown of hot functions
"""

import argparse
import cProfile
import gc
import pstats
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def find_rustest():
    """Try to import rustest, with helpful error if not found."""
    try:
        from rustest import rust
        return rust
    except ImportError:
        # Try adding python/ to path for development
        repo_root = Path(__file__).parent.parent
        sys.path.insert(0, str(repo_root / "python"))
        try:
            from rustest import rust
            return rust
        except ImportError:
            print("Error: Could not import rustest.", file=sys.stderr)
            print("Make sure rustest is installed or run from the repo root.", file=sys.stderr)
            sys.exit(1)


@dataclass
class PhaseTimings:
    """Track timing for each phase of test execution."""
    # Overall timing
    total_start: float = 0.0
    total_end: float = 0.0

    # Collection phase
    collection_start: float = 0.0
    collection_end: float = 0.0
    files_collected: int = 0
    tests_collected: int = 0

    # Execution phase
    execution_start: float = 0.0
    execution_end: float = 0.0

    # Per-file tracking
    file_timings: dict = field(default_factory=dict)
    current_file: str = ""
    current_file_start: float = 0.0

    # Test tracking
    test_count: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0

    # Slowest items
    slowest_files: list = field(default_factory=list)
    slowest_tests: list = field(default_factory=list)

    @property
    def collection_duration(self) -> float:
        if self.collection_end and self.collection_start:
            return self.collection_end - self.collection_start
        return 0.0

    @property
    def execution_duration(self) -> float:
        if self.execution_end and self.execution_start:
            return self.execution_end - self.execution_start
        return 0.0

    @property
    def total_duration(self) -> float:
        if self.total_end and self.total_start:
            return self.total_end - self.total_start
        return 0.0

    @property
    def overhead_duration(self) -> float:
        """Time not accounted for by collection or execution."""
        return self.total_duration - self.collection_duration - self.execution_duration


class TimingCollector:
    """Event consumer that collects timing information."""

    def __init__(self):
        self.timings = PhaseTimings()
        self._event_count = 0

    def handle(self, event: Any) -> None:
        """Handle events and record timestamps."""
        self._event_count += 1
        event_type = type(event).__name__
        now = time.perf_counter()

        if event_type == "CollectionStartedEvent":
            self.timings.collection_start = now

        elif event_type == "CollectionProgressEvent":
            self.timings.files_collected = event.files_collected
            self.timings.tests_collected += event.tests_collected

        elif event_type == "CollectionCompletedEvent":
            self.timings.collection_end = now
            self.timings.files_collected = event.total_files
            self.timings.tests_collected = event.total_tests

        elif event_type == "SuiteStartedEvent":
            self.timings.execution_start = now

        elif event_type == "FileStartedEvent":
            self.timings.current_file = event.file_path
            self.timings.current_file_start = now

        elif event_type == "TestCompletedEvent":
            self.timings.test_count += 1
            if event.status == "passed":
                self.timings.tests_passed += 1
            elif event.status == "failed":
                self.timings.tests_failed += 1
            elif event.status == "skipped":
                self.timings.tests_skipped += 1

            # Track slowest tests
            self.timings.slowest_tests.append((event.test_id, event.duration))

        elif event_type == "FileCompletedEvent":
            duration = now - self.timings.current_file_start
            self.timings.file_timings[event.file_path] = {
                "duration": duration,
                "passed": event.passed,
                "failed": event.failed,
                "skipped": event.skipped,
                "total": event.passed + event.failed + event.skipped,
            }
            self.timings.slowest_files.append((event.file_path, duration))

        elif event_type == "SuiteCompletedEvent":
            self.timings.execution_end = now

        elif event_type == "CollectionErrorEvent":
            pass  # Tracked separately


def format_time(seconds: float) -> str:
    """Format time in appropriate units."""
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.1f}µs"
    elif seconds < 1:
        return f"{seconds * 1000:.1f}ms"
    else:
        return f"{seconds:.2f}s"


def format_percent(part: float, total: float) -> str:
    """Format percentage."""
    if total == 0:
        return "0%"
    return f"{100 * part / total:.1f}%"


def run_with_timing(rust, test_paths, pattern=None):
    """Run tests and collect timing information."""
    collector = TimingCollector()

    collector.timings.total_start = time.perf_counter()

    report = rust.run(
        paths=test_paths,
        pattern=pattern,
        mark_expr=None,
        workers=1,
        capture_output=True,
        enable_codeblocks=False,
        last_failed_mode="none",
        fail_fast=False,
        pytest_compat=True,
        verbose=False,
        ascii=True,
        no_color=True,
        event_callback=collector.handle,
    )

    collector.timings.total_end = time.perf_counter()

    return collector.timings, report


def benchmark_phases(rust, test_paths, iterations=5, warmup=1):
    """Benchmark each phase over multiple iterations."""
    all_timings = []

    # Warmup
    for _ in range(warmup):
        run_with_timing(rust, test_paths)

    # Timed runs
    for _ in range(iterations):
        gc.collect()
        gc.disable()

        timings, _ = run_with_timing(rust, test_paths)
        all_timings.append(timings)

        gc.enable()

    return all_timings


def benchmark_pytest(test_paths, iterations=3):
    """Benchmark full pytest run for comparison."""
    times = []
    test_count = 0

    for i in range(iterations):
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no"] + test_paths,
            capture_output=True,
            text=True,
        )
        elapsed = time.perf_counter() - start
        times.append(elapsed)

        if i == 0:
            # Parse test count from output
            for line in result.stdout.split('\n'):
                if 'passed' in line or 'failed' in line:
                    # Try to extract count
                    import re
                    match = re.search(r'(\d+) passed', line)
                    if match:
                        test_count = int(match.group(1))
                    match = re.search(r'(\d+) failed', line)
                    if match:
                        test_count += int(match.group(1))

    return times, test_count


def profile_full_run(rust, test_paths):
    """Profile full run with cProfile."""
    collector = TimingCollector()
    profiler = cProfile.Profile()

    profiler.enable()
    rust.run(
        paths=test_paths,
        pattern=None,
        mark_expr=None,
        workers=1,
        capture_output=True,
        enable_codeblocks=False,
        last_failed_mode="none",
        fail_fast=False,
        pytest_compat=True,
        verbose=False,
        ascii=True,
        no_color=True,
        event_callback=collector.handle,
    )
    profiler.disable()

    return pstats.Stats(profiler), collector.timings


def print_phase_breakdown(timings: PhaseTimings):
    """Print detailed phase breakdown."""
    total = timings.total_duration

    print()
    print("PHASE BREAKDOWN")
    print("-" * 60)
    print(f"{'Phase':<20} {'Duration':>12} {'Percent':>10}")
    print("-" * 60)

    print(f"{'Collection':<20} {format_time(timings.collection_duration):>12} "
          f"{format_percent(timings.collection_duration, total):>10}")

    print(f"{'Execution':<20} {format_time(timings.execution_duration):>12} "
          f"{format_percent(timings.execution_duration, total):>10}")

    print(f"{'Overhead':<20} {format_time(timings.overhead_duration):>12} "
          f"{format_percent(timings.overhead_duration, total):>10}")

    print("-" * 60)
    print(f"{'TOTAL':<20} {format_time(total):>12} {'100%':>10}")


def print_slowest_files(timings: PhaseTimings, top_n=10):
    """Print slowest test files."""
    if not timings.slowest_files:
        return

    print()
    print(f"SLOWEST FILES (top {top_n})")
    print("-" * 70)

    sorted_files = sorted(timings.slowest_files, key=lambda x: x[1], reverse=True)[:top_n]

    for file_path, duration in sorted_files:
        info = timings.file_timings.get(file_path, {})
        tests = info.get("total", 0)
        print(f"  {format_time(duration):>10}  {file_path} ({tests} tests)")


def print_slowest_tests(timings: PhaseTimings, top_n=10):
    """Print slowest individual tests."""
    if not timings.slowest_tests:
        return

    print()
    print(f"SLOWEST TESTS (top {top_n})")
    print("-" * 70)

    sorted_tests = sorted(timings.slowest_tests, key=lambda x: x[1], reverse=True)[:top_n]

    for test_id, duration in sorted_tests:
        # Truncate long test IDs
        display_id = test_id if len(test_id) < 55 else "..." + test_id[-52:]
        print(f"  {format_time(duration):>10}  {display_id}")


def print_summary_stats(all_timings: list[PhaseTimings]):
    """Print summary statistics across multiple runs."""
    collection_times = [t.collection_duration for t in all_timings]
    execution_times = [t.execution_duration for t in all_timings]
    total_times = [t.total_duration for t in all_timings]

    print()
    print("TIMING STATISTICS")
    print("-" * 70)
    print(f"{'Metric':<15} {'Collection':>15} {'Execution':>15} {'Total':>15}")
    print("-" * 70)

    print(f"{'Min':<15} {format_time(min(collection_times)):>15} "
          f"{format_time(min(execution_times)):>15} {format_time(min(total_times)):>15}")

    print(f"{'Max':<15} {format_time(max(collection_times)):>15} "
          f"{format_time(max(execution_times)):>15} {format_time(max(total_times)):>15}")

    print(f"{'Mean':<15} {format_time(statistics.mean(collection_times)):>15} "
          f"{format_time(statistics.mean(execution_times)):>15} "
          f"{format_time(statistics.mean(total_times)):>15}")

    print(f"{'Median':<15} {format_time(statistics.median(collection_times)):>15} "
          f"{format_time(statistics.median(execution_times)):>15} "
          f"{format_time(statistics.median(total_times)):>15}")

    if len(all_timings) > 1:
        print(f"{'Stdev':<15} {format_time(statistics.stdev(collection_times)):>15} "
              f"{format_time(statistics.stdev(execution_times)):>15} "
              f"{format_time(statistics.stdev(total_times)):>15}")


def main():
    parser = argparse.ArgumentParser(
        description="Profile all stages of rustest execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s tests/
    %(prog)s tests/ examples/tests/
    %(prog)s . -n 10 --profile
    %(prog)s tests/ --no-pytest --slowest 20
        """,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Test paths to profile (default: current directory)",
    )
    parser.add_argument(
        "-n", "--iterations",
        type=int,
        default=5,
        help="Number of benchmark iterations (default: 5)",
    )
    parser.add_argument(
        "--no-pytest",
        action="store_true",
        help="Skip pytest comparison",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Show detailed cProfile output",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top functions to show in profile (default: 20)",
    )
    parser.add_argument(
        "--slowest",
        type=int,
        default=10,
        help="Number of slowest files/tests to show (default: 10)",
    )
    parser.add_argument(
        "-k", "--pattern",
        type=str,
        default=None,
        help="Only run tests matching pattern",
    )

    args = parser.parse_args()
    rust = find_rustest()

    print("=" * 70)
    print("RUSTEST FULL PROFILER")
    print("=" * 70)
    print(f"Test paths: {', '.join(args.paths)}")
    print(f"Iterations: {args.iterations}")
    if args.pattern:
        print(f"Pattern: {args.pattern}")
    print()

    # Single detailed run first
    print("Running detailed timing analysis...")
    timings, report = run_with_timing(rust, args.paths, pattern=args.pattern)

    print()
    print("TEST RESULTS")
    print("-" * 40)
    print(f"Tests collected: {timings.tests_collected}")
    print(f"Tests executed:  {timings.test_count}")
    print(f"  Passed:  {timings.tests_passed}")
    print(f"  Failed:  {timings.tests_failed}")
    print(f"  Skipped: {timings.tests_skipped}")
    print(f"Files: {timings.files_collected}")

    # Phase breakdown
    print_phase_breakdown(timings)

    # Slowest items
    print_slowest_files(timings, top_n=args.slowest)
    print_slowest_tests(timings, top_n=args.slowest)

    # Multiple iterations for statistics
    if args.iterations > 1:
        print()
        print(f"Running {args.iterations} iterations for statistics...")
        all_timings = benchmark_phases(rust, args.paths, iterations=args.iterations)
        print_summary_stats(all_timings)

    # pytest comparison
    if not args.no_pytest:
        print()
        print("Benchmarking pytest for comparison...")
        try:
            pytest_times, pytest_count = benchmark_pytest(args.paths, iterations=3)

            print()
            print("PYTEST COMPARISON")
            print("-" * 40)
            print(f"pytest tests: {pytest_count}")
            print(f"pytest median: {format_time(statistics.median(pytest_times))}")
            print(f"rustest median: {format_time(statistics.median([t.total_duration for t in all_timings if args.iterations > 1] or [timings.total_duration]))}")

            rustest_median = statistics.median([t.total_duration for t in all_timings] if args.iterations > 1 else [timings.total_duration])
            pytest_median = statistics.median(pytest_times)

            if rustest_median > 0:
                ratio = pytest_median / rustest_median
                if ratio >= 1:
                    print(f"rustest is {ratio:.1f}x FASTER than pytest")
                else:
                    print(f"rustest is {1/ratio:.1f}x SLOWER than pytest")
        except FileNotFoundError:
            print("pytest not found, skipping comparison")
        except Exception as e:
            print(f"Error running pytest: {e}")

    # Detailed cProfile
    if args.profile:
        print()
        print("=" * 70)
        print("DETAILED PROFILE (cProfile)")
        print("=" * 70)

        stats, _ = profile_full_run(rust, args.paths)
        stats.strip_dirs()

        print()
        print(f"TOP {args.top} FUNCTIONS BY CUMULATIVE TIME:")
        print("-" * 70)
        stats.sort_stats("cumulative")
        stats.print_stats(args.top)

        print()
        print(f"TOP {args.top} FUNCTIONS BY TOTAL TIME (self only):")
        print("-" * 70)
        stats.sort_stats("tottime")
        stats.print_stats(args.top)

        # Filter to show async/execution related
        print()
        print("ASYNC/EXECUTION RELATED FUNCTIONS:")
        print("-" * 70)
        stats.sort_stats("cumulative")
        stats.print_stats("async|executor|run_coroutine|gather", 15)


if __name__ == "__main__":
    main()
