"""Benchmark pytest vs rustest v1 vs rustest v2: collection, full run.

Usage: python -m conformance.bench.bench
"""

from __future__ import annotations

import argparse
import json
import shutil
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
    #: ``rustest --v1 <suite>`` -- the legacy engine, explicitly flagged. Before the Phase 1c
    #: flip a bare ``rustest <suite>`` command *was* the v1 timing; since the flip a bare
    #: command runs v2, so this field keeps its historical meaning only because the flag is
    #: now spelled out. See ``rustest_v2_run_s`` for what the bare command measures today.
    rustest_run_s: float
    #: ``rustest <suite>`` with no mode flag -- the v2 default path, since the Phase 1c flip.
    rustest_v2_run_s: float
    #: ``rustest --v2-collect-only <suite>`` with the Tier S manifest cache **cold** --
    #: ``.rustest_cache/v2-manifest`` is deleted immediately before the measurement, so
    #: every file is read and parsed. Reserved through Phase 1c Task 2; filled in by Task 3.
    rustest_collect_s: float
    #: The same command run again, with the cache the cold run just wrote. This is the number
    #: the Phase 2 gate is about (target: <= 50 ms at 5 000 tests) and the one a user
    #: experiences on every run after the first, so it is measured as **wall time of the whole
    #: process** -- interpreter start-up included, because that is latency the user waits for
    #: whether or not the engine is responsible for it.
    rustest_collect_warm_s: float


class Derived(TypedDict):
    pytest_overhead_us_per_test: float | None
    rustest_overhead_us_per_test: float | None
    rustest_v2_overhead_us_per_test: float | None


class BenchReport(TypedDict):
    results: list[BenchRow]
    derived: Derived


def _time_cmd(cmd: list[str], cwd: Path) -> float:
    """Time *cmd*, raising if it failed.

    Every generated benchmark suite is all-passing by construction, so every
    timed command (pytest collect, pytest run, rustest v1 run, rustest v2 run,
    rustest v2 collect-only) must exit 0. A nonzero exit means the runner errored
    out and the "time" measured is the time to fail -- silently averaging that
    into a baseline would publish a meaningless number.
    """
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise RuntimeError(
            f"benchmark command failed (exit {proc.returncode}): {cmd} :: {proc.stderr[-300:]}"
        )
    return elapsed


def _time_cold_collect(rustest_base: list[str], suite: Path) -> float:
    """Time a ``--v2-collect-only`` with the manifest cache guaranteed cold.

    The cache is removed rather than assumed absent. Earlier rows in the same suite
    directory (the v1 and v2 *run* timings) do not write it -- the run path is Tier D only
    and never touches the manifest cache -- but "does not today" is not a property a
    published cold number should rest on, and one added call site would silently turn this
    column into a second warm one.
    """
    shutil.rmtree(suite / ".rustest_cache" / "v2-manifest", ignore_errors=True)
    return _time_cmd([*rustest_base, "--v2-collect-only", "."], suite)


def derive_overhead(results: list[BenchRow]) -> Derived:
    """Per-test marginal cost, in microseconds, from the two largest sizes.

    Subtracting the smaller size's total run time from the larger's cancels the
    fixed startup cost (interpreter boot, plugin loading, extension import) that
    both runs pay identically, leaving the incremental cost of the extra tests:

        overhead_us = (run_s_big - run_s_small) / (tests_big - tests_small) * 1e6

    Needs at least two distinct sizes; otherwise all three values are None.
    """
    _empty: Derived = {
        "pytest_overhead_us_per_test": None,
        "rustest_overhead_us_per_test": None,
        "rustest_v2_overhead_us_per_test": None,
    }
    if len(results) < 2:
        return _empty
    ordered = sorted(results, key=lambda r: r["tests"])
    small, big = ordered[-2], ordered[-1]
    delta_tests = big["tests"] - small["tests"]
    if delta_tests <= 0:
        return _empty
    return {
        "pytest_overhead_us_per_test": (big["pytest_run_s"] - small["pytest_run_s"])
        / delta_tests
        * 1e6,
        "rustest_overhead_us_per_test": (big["rustest_run_s"] - small["rustest_run_s"])
        / delta_tests
        * 1e6,
        "rustest_v2_overhead_us_per_test": (big["rustest_v2_run_s"] - small["rustest_v2_run_s"])
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
            rustest_base = [sys.executable, "-m", "rustest"]
            row: BenchRow = {
                "files": files,
                "tests": files * tests_per_file,
                "pytest_collect_s": _time_cmd([*pytest_base, "--collect-only"], suite),
                "pytest_run_s": _time_cmd([*pytest_base, "--tb=no"], suite),
                # Explicit --v1: since the Phase 1c flip a bare command runs v2, so this is
                # the only way left to measure the legacy engine's timing.
                "rustest_run_s": _time_cmd([*rustest_base, "--v1", ".", "--color", "never"], suite),
                # No mode flag: the v2 default path.
                "rustest_v2_run_s": _time_cmd([*rustest_base, "."], suite),
                # Cold: the cache directory is removed first, so this is a full parse of
                # every file however many benchmark commands ran before it.
                "rustest_collect_s": _time_cold_collect(rustest_base, suite),
                # Warm: the very next run, reading what the cold one just wrote.
                "rustest_collect_warm_s": _time_cmd(
                    [*rustest_base, "--v2-collect-only", "."], suite
                ),
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
    print(
        "| files | tests | pytest collect | pytest run | rustest v1 run "
        + "| rustest v2 run | rustest v2 collect (cold) | rustest v2 collect (warm) |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for row in report["results"]:
        print(
            f"| {row['files']} | {row['tests']} | {row['pytest_collect_s']:.2f}s "
            + f"| {row['pytest_run_s']:.2f}s | {row['rustest_run_s']:.2f}s "
            + f"| {row['rustest_v2_run_s']:.2f}s | {row['rustest_collect_s']:.2f}s "
            + f"| {row['rustest_collect_warm_s']:.3f}s |"
        )
    derived = report["derived"]
    print(f"pytest marginal overhead: {_fmt_overhead(derived['pytest_overhead_us_per_test'])}")
    print(f"rustest v1 marginal overhead: {_fmt_overhead(derived['rustest_overhead_us_per_test'])}")
    print(
        "rustest v2 marginal overhead: "
        + f"{_fmt_overhead(derived['rustest_v2_overhead_us_per_test'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
