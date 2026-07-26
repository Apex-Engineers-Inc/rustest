"""Benchmark pytest vs rustest: collection, full run. Usage: python -m conformance.bench.bench"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict

from .gen import generate_suite

DEFAULT_SIZES = [(10, 10), (100, 10), (500, 10)]


class BenchRow(TypedDict):
    files: int
    tests: int
    pytest_collect_s: float
    pytest_run_s: float
    rustest_run_s: float
    rustest_collect_s: float | None


class Derived(TypedDict):
    pytest_overhead_us_per_test: float | None
    rustest_overhead_us_per_test: float | None


class BenchReport(TypedDict):
    results: list[BenchRow]
    derived: Derived


def _time_cmd(cmd: list[str], cwd: Path) -> float:
    """Time *cmd*, raising if it failed.

    Every generated benchmark suite is all-passing by construction, so all three
    timed commands must exit 0. A nonzero exit means the runner errored out and
    the "time" measured is the time to fail -- silently averaging that into a
    baseline would publish a meaningless number.
    """
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise RuntimeError(
            f"benchmark command failed (exit {proc.returncode}): {cmd} :: {proc.stderr[-300:]}"
        )
    return elapsed


def derive_overhead(results: list[BenchRow]) -> Derived:
    """Per-test marginal cost, in microseconds, from the two largest sizes.

    Subtracting the smaller size's total run time from the larger's cancels the
    fixed startup cost (interpreter boot, plugin loading, extension import) that
    both runs pay identically, leaving the incremental cost of the extra tests:

        overhead_us = (run_s_big - run_s_small) / (tests_big - tests_small) * 1e6

    Needs at least two distinct sizes; otherwise both values are None.
    """
    if len(results) < 2:
        return {"pytest_overhead_us_per_test": None, "rustest_overhead_us_per_test": None}
    ordered = sorted(results, key=lambda r: r["tests"])
    small, big = ordered[-2], ordered[-1]
    delta_tests = big["tests"] - small["tests"]
    if delta_tests <= 0:
        return {"pytest_overhead_us_per_test": None, "rustest_overhead_us_per_test": None}
    return {
        "pytest_overhead_us_per_test": (big["pytest_run_s"] - small["pytest_run_s"])
        / delta_tests
        * 1e6,
        "rustest_overhead_us_per_test": (big["rustest_run_s"] - small["rustest_run_s"])
        / delta_tests
        * 1e6,
    }


def run_benchmarks(sizes: list[tuple[int, int]], quick: bool) -> BenchReport:
    results: list[BenchRow] = []
    for files, tests_per_file in sizes:
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / "suite"
            generate_suite(suite, files, tests_per_file)
            pytest_base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
            row: BenchRow = {
                "files": files,
                "tests": files * tests_per_file,
                "pytest_collect_s": _time_cmd([*pytest_base, "--collect-only"], suite),
                "pytest_run_s": _time_cmd([*pytest_base, "--tb=no"], suite),
                "rustest_run_s": _time_cmd(
                    [sys.executable, "-m", "rustest", ".", "--color", "never"], suite
                ),
                "rustest_collect_s": None,  # v1 has no collect-only; Phase 2 fills this
            }
            results.append(row)
        if quick:
            break
    return {"results": results, "derived": derive_overhead(results)}


def _fmt_overhead(value: float | None) -> str:
    return "n/a (needs >= 2 sizes)" if value is None else f"{value:.1f} us/test"


def main() -> int:
    parser = argparse.ArgumentParser(prog="conformance-bench")
    parser.add_argument("--quick", action="store_true", help="Smallest size only")
    parser.add_argument("--out", default="conformance/bench_results.json")
    args = parser.parse_args()
    report = run_benchmarks(DEFAULT_SIZES[:1] if args.quick else DEFAULT_SIZES, args.quick)
    # Trailing newline: baselines.json is a tracked file and the end-of-file-fixer
    # pre-commit hook would otherwise rewrite it after every run.
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("| files | tests | pytest collect | pytest run | rustest run |")
    print("|---|---|---|---|---|")
    for row in report["results"]:
        print(
            f"| {row['files']} | {row['tests']} | {row['pytest_collect_s']:.2f}s "
            + f"| {row['pytest_run_s']:.2f}s | {row['rustest_run_s']:.2f}s |"
        )
    derived = report["derived"]
    print(f"pytest marginal overhead: {_fmt_overhead(derived['pytest_overhead_us_per_test'])}")
    print(f"rustest marginal overhead: {_fmt_overhead(derived['rustest_overhead_us_per_test'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
