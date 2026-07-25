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


class BenchReport(TypedDict):
    results: list[BenchRow]


def _time_cmd(cmd: list[str], cwd: Path) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    return time.perf_counter() - start


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
    return {"results": results}


def main() -> int:
    parser = argparse.ArgumentParser(prog="conformance-bench")
    parser.add_argument("--quick", action="store_true", help="Smallest size only")
    parser.add_argument("--out", default="conformance/bench_results.json")
    args = parser.parse_args()
    report = run_benchmarks(DEFAULT_SIZES[:1] if args.quick else DEFAULT_SIZES, args.quick)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("| files | tests | pytest collect | pytest run | rustest run |")
    print("|---|---|---|---|---|")
    for row in report["results"]:
        print(
            f"| {row['files']} | {row['tests']} | {row['pytest_collect_s']:.2f}s "
            + f"| {row['pytest_run_s']:.2f}s | {row['rustest_run_s']:.2f}s |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
