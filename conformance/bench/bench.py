"""Benchmark pytest vs rustest v1 vs rustest v2: collection, full run.

Usage: python -m conformance.bench.bench
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict

from .gen import generate_suite

DEFAULT_SIZES = [(10, 10), (100, 10), (500, 10)]

#: The two suites the per-test overhead is derived from: **the same file count**, different
#: tests per file.
#:
#: The old derivation subtracted the 1 000-test row from the 5 000-test row of
#: :data:`DEFAULT_SIZES`, and those two differ in *files* (100 vs 500) as well as in tests. The
#: difference therefore contained the cost of importing 400 extra modules — ~2.5 ms each,
#: measured — so what it reported as "marginal cost per test" was mostly marginal cost per
#: *file*. Holding files constant at 100 makes the delta the cost of 4 000 extra tests and
#: nothing else.
#:
#: It is a **separate pair** rather than a reuse of the 500-file row on purpose: that row is
#: what the collect gate measures, and a metric that shares a cell with a gate cannot be
#: changed without moving the gate.
OVERHEAD_SIZES = [(100, 10), (100, 50)]

#: Repetitions per overhead cell. One sample of a ~1 s command on Windows carries ±0.4 s,
#: which over 4 000 tests is ±100 us/test — larger than the 200 us gate the number is compared
#: against. The published baseline for this metric was once **-714.9 us/test**, which is not a
#: measurement but a warning label. Medians of 5 put the noise well below the gate.
OVERHEAD_REPETITIONS = 5


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


class OverheadRow(TypedDict):
    """One cell of the fixed-file-count overhead measurement. See :func:`measure_overhead`."""

    files: int
    tests: int
    #: How many timed samples the medians below are drawn from.
    repetitions: int
    pytest_run_s: float
    rustest_run_s: float
    #: ``rustest . -n 1 -q`` -- sequential on purpose; see :func:`measure_overhead`.
    rustest_v2_run_s: float


class BenchReport(TypedDict):
    results: list[BenchRow]
    derived: Derived
    #: The cells :data:`derived` was computed from, so a published number is traceable.
    overhead: list[OverheadRow]


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


def measure_overhead(quick: bool = False) -> tuple[Derived, list[OverheadRow]]:
    """Per-test marginal cost, in microseconds, at a **fixed file count**.

    Two suites of 100 files, one with 10 tests each and one with 50
    (:data:`OVERHEAD_SIZES`). Subtracting the smaller's median run time from the larger's
    cancels everything both pay identically -- interpreter boot, worker-pool spawn, importing
    the same 100 modules -- and leaves the incremental cost of 4 000 extra tests::

        overhead_us = (median(run_s_big) - median(run_s_small)) / (tests_big - tests_small) * 1e6

    Four deliberate differences from the derivation this replaces, each fixing a way the old
    number lied:

    * **files are held constant.** The old pair differed in files as well as tests, so its
      "per test" figure was dominated by ~2.5 ms of module import per extra *file*;
    * **medians of** :data:`OVERHEAD_REPETITIONS`, not one sample. The single-sample version
      published -714.9 us/test once -- a negative marginal cost;
    * **`-n 1`.** With one worker per CPU the pool spawn dominates and varies with machine
      load, and it is a fixed cost the subtraction is supposed to cancel, not measure;
    * **both cells warm.** Each suite is run once and discarded before timing, so neither
      cell pays a cold manifest cache or a cold bytecode cache that the other does not. A
      cache warmed in one cell and not the other lands entirely in the delta.

    Returns the derived values and the per-cell rows behind them, so a published number can
    be traced to the timings it came from rather than taken on faith.
    """
    rows: list[OverheadRow] = []
    sizes = OVERHEAD_SIZES[:1] if quick else OVERHEAD_SIZES
    for files, tests_per_file in sizes:
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / "suite"
            generate_suite(suite, files, tests_per_file)
            pytest_base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
            rustest_base = [sys.executable, "-m", "rustest"]
            commands = {
                "pytest_run_s": [*pytest_base, "--tb=no", "-p", "no:randomly"],
                "rustest_run_s": [*rustest_base, "--v1", ".", "--color", "never"],
                "rustest_v2_run_s": [*rustest_base, ".", "-n", "1", "-q"],
            }
            row: OverheadRow = {
                "files": files,
                "tests": files * tests_per_file,
                "repetitions": OVERHEAD_REPETITIONS,
                "pytest_run_s": 0.0,
                "rustest_run_s": 0.0,
                "rustest_v2_run_s": 0.0,
            }
            for key, cmd in commands.items():
                # One discarded run per command, so the timed samples all see the same warm
                # state: `__pycache__`, the manifest cache and the rewritten-bytecode cache.
                _ = _time_cmd(cmd, suite)
                samples = [_time_cmd(cmd, suite) for _ in range(OVERHEAD_REPETITIONS)]
                row[key] = statistics.median(samples)
            rows.append(row)

    return derive_overhead(rows), rows


def derive_overhead(rows: list[OverheadRow]) -> Derived:
    """The slope, in microseconds per test, between two same-file-count cells.

    Split from :func:`measure_overhead` so the arithmetic is testable without running a
    benchmark: the timing half takes minutes and cannot assert a number, while this half is
    the part that can be wrong in a way nobody notices.

    Fewer than two cells, or two cells with the same test count, yield ``None`` -- one point
    has no slope, and dividing by a zero delta would produce an infinity that looks like a
    measurement.
    """
    empty: Derived = {
        "pytest_overhead_us_per_test": None,
        "rustest_overhead_us_per_test": None,
        "rustest_v2_overhead_us_per_test": None,
    }
    if len(rows) < 2:
        return empty
    ordered = sorted(rows, key=lambda row: row["tests"])
    small, big = ordered[-2], ordered[-1]
    delta = big["tests"] - small["tests"]
    if delta <= 0:
        return empty
    return {
        "pytest_overhead_us_per_test": (big["pytest_run_s"] - small["pytest_run_s"]) / delta * 1e6,
        "rustest_overhead_us_per_test": (big["rustest_run_s"] - small["rustest_run_s"])
        / delta
        * 1e6,
        "rustest_v2_overhead_us_per_test": (big["rustest_v2_run_s"] - small["rustest_v2_run_s"])
        / delta
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
    derived, overhead = measure_overhead(quick)
    return {"results": results, "derived": derived, "overhead": overhead}


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
    print()
    print(
        f"Marginal per-test overhead, {OVERHEAD_SIZES[0][0]} files held constant, "
        + f"medians of {OVERHEAD_REPETITIONS}, sequential:"
    )
    for cell in report["overhead"]:
        print(
            f"| {cell['files']} files | {cell['tests']} tests "
            + f"| pytest {cell['pytest_run_s']:.2f}s | v1 {cell['rustest_run_s']:.2f}s "
            + f"| v2 {cell['rustest_v2_run_s']:.2f}s |"
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
