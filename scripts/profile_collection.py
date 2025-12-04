#!/usr/bin/env python3
"""
Profile rustest test collection performance.

Usage:
    python scripts/profile_collection.py [test_paths...]

Examples:
    python scripts/profile_collection.py tests/
    python scripts/profile_collection.py tests/ examples/tests/
    python scripts/profile_collection.py .  # current directory

Output includes:
    - Collection timing (min, mean, median, max)
    - Comparison with pytest --collect-only
    - Detailed cProfile breakdown of hot functions
"""

import argparse
import cProfile
import gc
import io
import pstats
import statistics
import subprocess
import sys
import time
from pathlib import Path


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


def benchmark_collection(rust, test_paths, iterations=10, warmup=2):
    """Benchmark collection time over multiple iterations."""
    times = []
    test_count = 0

    # Warmup runs (not counted)
    for _ in range(warmup):
        rust.run(
            paths=test_paths,
            pattern="__NOMATCH_WARMUP__",
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
            event_callback=None,
        )

    # Timed runs
    for i in range(iterations):
        gc.collect()
        gc.disable()

        start = time.perf_counter()
        report = rust.run(
            paths=test_paths,
            pattern="__NOMATCH_BENCHMARK__",  # Collect only, don't execute
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
            event_callback=None,
        )
        elapsed = time.perf_counter() - start

        gc.enable()
        times.append(elapsed)

        if i == 0:
            # Get actual test count from a real collection
            real_report = rust.run(
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
                event_callback=None,
            )
            test_count = real_report.total

    return times, test_count


def benchmark_pytest(test_paths, iterations=5):
    """Benchmark pytest --collect-only for comparison."""
    times = []
    test_count = 0

    for i in range(iterations):
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"] + test_paths,
            capture_output=True,
            text=True,
        )
        elapsed = time.perf_counter() - start
        times.append(elapsed)

        if i == 0:
            # Count tests from output
            test_count = len([l for l in result.stdout.split('\n') if '::' in l])

    return times, test_count


def profile_collection(rust, test_paths):
    """Profile collection with cProfile and return stats."""
    profiler = cProfile.Profile()

    profiler.enable()
    rust.run(
        paths=test_paths,
        pattern="__NOMATCH_PROFILE__",
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
        event_callback=None,
    )
    profiler.disable()

    return pstats.Stats(profiler)


def format_time(seconds):
    """Format time in appropriate units."""
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.1f}µs"
    elif seconds < 1:
        return f"{seconds * 1000:.1f}ms"
    else:
        return f"{seconds:.2f}s"


def main():
    parser = argparse.ArgumentParser(
        description="Profile rustest test collection performance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s tests/
    %(prog)s tests/ examples/tests/
    %(prog)s . --iterations 20
    %(prog)s tests/ --no-pytest  # skip pytest comparison
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
        default=10,
        help="Number of benchmark iterations (default: 10)",
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

    args = parser.parse_args()
    rust = find_rustest()

    print("=" * 70)
    print("RUSTEST COLLECTION PROFILER")
    print("=" * 70)
    print(f"Test paths: {', '.join(args.paths)}")
    print(f"Iterations: {args.iterations}")
    print()

    # Benchmark rustest
    print("Benchmarking rustest collection...")
    rustest_times, rustest_count = benchmark_collection(
        rust, args.paths, iterations=args.iterations
    )

    print()
    print("RUSTEST RESULTS")
    print("-" * 40)
    print(f"Tests collected: {rustest_count}")
    print(f"Min:    {format_time(min(rustest_times))}")
    print(f"Max:    {format_time(max(rustest_times))}")
    print(f"Mean:   {format_time(statistics.mean(rustest_times))}")
    print(f"Median: {format_time(statistics.median(rustest_times))}")
    if len(rustest_times) > 1:
        print(f"Stdev:  {format_time(statistics.stdev(rustest_times))}")
    print(f"All:    {[format_time(t) for t in rustest_times]}")

    # Benchmark pytest for comparison
    if not args.no_pytest:
        print()
        print("Benchmarking pytest --collect-only...")
        try:
            pytest_times, pytest_count = benchmark_pytest(args.paths, iterations=5)

            print()
            print("PYTEST RESULTS")
            print("-" * 40)
            print(f"Tests collected: {pytest_count}")
            print(f"Min:    {format_time(min(pytest_times))}")
            print(f"Max:    {format_time(max(pytest_times))}")
            print(f"Mean:   {format_time(statistics.mean(pytest_times))}")
            print(f"Median: {format_time(statistics.median(pytest_times))}")

            # Comparison
            print()
            print("COMPARISON")
            print("-" * 40)
            rustest_median = statistics.median(rustest_times)
            pytest_median = statistics.median(pytest_times)
            if rustest_median > 0:
                ratio = pytest_median / rustest_median
                if ratio >= 1:
                    print(f"rustest is {ratio:.1f}x FASTER than pytest")
                else:
                    print(f"rustest is {1/ratio:.1f}x SLOWER than pytest")
        except FileNotFoundError:
            print("pytest not found, skipping comparison")

    # Detailed profile if requested
    if args.profile:
        print()
        print("=" * 70)
        print("DETAILED PROFILE (cProfile)")
        print("=" * 70)

        stats = profile_collection(rust, args.paths)
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


if __name__ == "__main__":
    main()
