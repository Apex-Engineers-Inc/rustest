"""Benchmark pytest against rustest: collection, full run.

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

#: The two suites the per-**file** cost is derived from: **the same test count**, different
#: numbers of files.
#:
#: :data:`OVERHEAD_SIZES` is the mirror image of this pair and, on its own, it is why a large
#: cost went unmeasured for four phases. Holding files at 100 makes its delta the cost of
#: extra *tests* and differences the per-file term out entirely -- so the published
#: "118 us/test" was true and said nothing about the term that dominates a wide tree.
#:
#: The Phase 4b Task 1 profile measured it by hand and found it large: 5 000 tests in 100
#: files ran in 5.64 s, the same 5 000 tests in 500 files took 21.46 s -- **~40 ms per file**,
#: against 118 us per test. A 5 000-test suite laid out 500-wide therefore spends more on
#: being 500 files than on having 5 000 tests, and no benchmark cell could see it.
#:
#: Both cells are 5 000 tests, so the subtraction cancels every per-test cost exactly and
#: leaves 400 files' worth of whatever a file costs.
PER_FILE_SIZES = [(100, 50), (500, 10)]

#: Repetitions per overhead cell. One sample of a ~1 s command on Windows carries ±0.4 s,
#: which over 4 000 tests is ±100 us/test — larger than the 200 us gate the number is compared
#: against. The published baseline for this metric was once **-714.9 us/test**, which is not a
#: measurement but a warning label. Medians of 5 put the noise well below the gate.
OVERHEAD_REPETITIONS = 5

#: Repetitions per per-file cell. Fewer than :data:`OVERHEAD_REPETITIONS` because each cell
#: is a 5 000-test run rather than a 1 000-test one, and the term being measured is ~340x
#: larger relative to the noise (40 ms per file against 118 us per test), so three samples
#: already put the median well inside the signal.
PER_FILE_REPETITIONS = 3


class BenchRow(TypedDict):
    files: int
    tests: int
    pytest_collect_s: float
    pytest_run_s: float
    #: ``rustest <suite>`` with no mode flag -- the only engine there is. A
    #: ``rustest_run_s`` column sat beside this one until Phase 4 Task 2 and timed
    #: ``rustest --v1``; it went with the engine it measured.
    rustest_run_s: float
    #: ``rustest --collect-only <suite>`` with the Tier S manifest cache **cold** --
    #: ``.rustest_cache/manifest`` is deleted immediately before the measurement, so
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
    #: Marginal milliseconds per **file** at a fixed test count -- see :data:`PER_FILE_SIZES`.
    #: The axis ``OVERHEAD_SIZES`` differences out by construction.
    pytest_per_file_ms: float | None
    rustest_per_file_ms: float | None
    #: How much of ``rustest_per_file_ms`` a ``--collect-only`` already pays. The
    #: remainder is what the *execute* half spends per file: the worker's import of the
    #: module, plus the module/class fixture boundaries around its tests. Splitting the term
    #: is the difference between "make collection faster" and "make per-file dispatch
    #: cheaper", which are different fixes.
    rustest_per_file_collect_ms: float | None


class OverheadRow(TypedDict):
    """One cell of the fixed-file-count overhead measurement. See :func:`measure_overhead`."""

    files: int
    tests: int
    #: How many timed samples the medians below are drawn from.
    repetitions: int
    pytest_run_s: float
    #: ``rustest . -n 1 -q`` -- sequential on purpose; see :func:`measure_overhead`.
    rustest_run_s: float


class PerFileRow(TypedDict):
    """One cell of the fixed-test-count per-file measurement. See :func:`measure_per_file`."""

    files: int
    tests: int
    repetitions: int
    pytest_run_s: float
    #: ``rustest . -n 1 -q`` -- sequential for the same reason the overhead cells are: a
    #: pool whose size varies with the machine would put spawn cost in the delta.
    rustest_run_s: float
    #: ``rustest --collect-only .`` warm, so the pair also answers *where* the per-file
    #: cost lands rather than only how big it is.
    rustest_collect_only_s: float


class BenchReport(TypedDict):
    results: list[BenchRow]
    derived: Derived
    #: The cells :data:`derived` was computed from, so a published number is traceable.
    overhead: list[OverheadRow]
    #: ...and the same for the per-file half of :data:`derived`.
    per_file: list[PerFileRow]


def _time_cmd(cmd: list[str], cwd: Path) -> float:
    """Time *cmd*, raising if it failed.

    Every generated benchmark suite is all-passing by construction, so every
    timed command (pytest collect, pytest run, rustest run, rustest
    collect-only) must exit 0. A nonzero exit means the runner errored
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
    """Time a ``--collect-only`` with the manifest cache guaranteed cold.

    The cache is removed rather than assumed absent. Earlier rows in the same suite
    directory (the *run* timings) do not write it -- the run path is Tier D only
    and never touches the manifest cache -- but "does not today" is not a property a
    published cold number should rest on, and one added call site would silently turn this
    column into a second warm one.
    """
    shutil.rmtree(suite / ".rustest_cache" / "manifest", ignore_errors=True)
    return _time_cmd([*rustest_base, "--collect-only", "."], suite)


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
                "rustest_run_s": [*rustest_base, ".", "-n", "1", "-q"],
            }
            row: OverheadRow = {
                "files": files,
                "tests": files * tests_per_file,
                "repetitions": OVERHEAD_REPETITIONS,
                "pytest_run_s": 0.0,
                "rustest_run_s": 0.0,
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
    empty = _empty_derived()
    if len(rows) < 2:
        return empty
    ordered = sorted(rows, key=lambda row: row["tests"])
    small, big = ordered[-2], ordered[-1]
    delta = big["tests"] - small["tests"]
    if delta <= 0:
        return empty
    return {
        **empty,
        "pytest_overhead_us_per_test": (big["pytest_run_s"] - small["pytest_run_s"]) / delta * 1e6,
        "rustest_overhead_us_per_test": (big["rustest_run_s"] - small["rustest_run_s"])
        / delta
        * 1e6,
    }


def _empty_derived() -> Derived:
    return {
        "pytest_overhead_us_per_test": None,
        "rustest_overhead_us_per_test": None,
        "pytest_per_file_ms": None,
        "rustest_per_file_ms": None,
        "rustest_per_file_collect_ms": None,
    }


def measure_per_file(quick: bool = False) -> tuple[Derived, list[PerFileRow]]:
    """Marginal cost per **file**, in milliseconds, at a fixed *test* count.

    The counterpart to :func:`measure_overhead`, and the axis this benchmark was blind to
    until Phase 4b. Two suites of 5 000 tests, one 100 files wide and one 500
    (:data:`PER_FILE_SIZES`); subtracting the narrow one's median from the wide one's
    cancels the cost of 5 000 tests exactly and leaves 400 files::

        per_file_ms = (median(run_s_wide) - median(run_s_narrow)) / (files_wide - files_narrow) * 1e3

    The same discipline as the overhead pair, for the same reasons: ``-n 1`` so pool spawn is
    not in the delta, both cells warmed by a discarded run so neither pays a cold cache the
    other does not, and medians rather than single samples.

    A third column, warm ``--collect-only``, is measured on the same two suites so the
    derived figure comes with its own decomposition: whatever share of the per-file cost
    collection already accounts for is a Tier S / walk / parse problem, and the rest belongs
    to the execute half.
    """
    rows: list[PerFileRow] = []
    # `--quick` gets a *scaled-down* single cell rather than the first real one. Both real
    # cells are 5 000 tests, and running one of them four times over three commands is
    # minutes -- which is not what `--quick` means, and would put those minutes into the
    # unit test that exercises this path. One cell has no slope either way, so the derived
    # figures are `None` in quick mode exactly as they are for the overhead pair.
    sizes = [(4, 2)] if quick else PER_FILE_SIZES
    for files, tests_per_file in sizes:
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / "suite"
            generate_suite(suite, files, tests_per_file)
            pytest_base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
            rustest_base = [sys.executable, "-m", "rustest"]
            commands = {
                "pytest_run_s": [*pytest_base, "--tb=no", "-p", "no:randomly"],
                "rustest_run_s": [*rustest_base, ".", "-n", "1", "-q"],
                "rustest_collect_only_s": [*rustest_base, "--collect-only", "."],
            }
            row: PerFileRow = {
                "files": files,
                "tests": files * tests_per_file,
                "repetitions": PER_FILE_REPETITIONS,
                "pytest_run_s": 0.0,
                "rustest_run_s": 0.0,
                "rustest_collect_only_s": 0.0,
            }
            for key, cmd in commands.items():
                _ = _time_cmd(cmd, suite)
                samples = [_time_cmd(cmd, suite) for _ in range(PER_FILE_REPETITIONS)]
                row[key] = statistics.median(samples)
            rows.append(row)

    return derive_per_file(rows), rows


def derive_per_file(rows: list[PerFileRow]) -> Derived:
    """The slope, in milliseconds per file, between two same-test-count cells.

    Split from the timing half for the same reason :func:`derive_overhead` is: the
    arithmetic is the part that can be wrong without anybody noticing.

    Fewer than two cells, or two cells with the same file count, yield ``None`` -- and so
    does a pair whose *test* counts differ, because then the delta is not about files at
    all and publishing it as "per file" would be a lie rather than a gap.
    """
    empty = _empty_derived()
    if len(rows) < 2:
        return empty
    ordered = sorted(rows, key=lambda row: row["files"])
    narrow, wide = ordered[-2], ordered[-1]
    delta = wide["files"] - narrow["files"]
    if delta <= 0 or wide["tests"] != narrow["tests"]:
        return empty
    return {
        **empty,
        "pytest_per_file_ms": (wide["pytest_run_s"] - narrow["pytest_run_s"]) / delta * 1e3,
        "rustest_per_file_ms": (wide["rustest_run_s"] - narrow["rustest_run_s"]) / delta * 1e3,
        "rustest_per_file_collect_ms": (
            wide["rustest_collect_only_s"] - narrow["rustest_collect_only_s"]
        )
        / delta
        * 1e3,
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
                # the only way left to measure the legacy engine's timing.
                # No mode flag: the v2 default path.
                "rustest_run_s": _time_cmd([*rustest_base, "."], suite),
                # Cold: the cache directory is removed first, so this is a full parse of
                # every file however many benchmark commands ran before it.
                "rustest_collect_s": _time_cold_collect(rustest_base, suite),
                # Warm: the very next run, reading what the cold one just wrote.
                "rustest_collect_warm_s": _time_cmd([*rustest_base, "--collect-only", "."], suite),
            }
            results.append(row)
        if quick:
            break
    derived, overhead = measure_overhead(quick)
    per_file_derived, per_file = measure_per_file(quick)
    # The two derivations fill disjoint halves of the same mapping, so they are merged
    # key by key rather than one clobbering the other's `None`s.
    derived["pytest_per_file_ms"] = per_file_derived["pytest_per_file_ms"]
    derived["rustest_per_file_ms"] = per_file_derived["rustest_per_file_ms"]
    derived["rustest_per_file_collect_ms"] = per_file_derived["rustest_per_file_collect_ms"]
    return {
        "results": results,
        "derived": derived,
        "overhead": overhead,
        "per_file": per_file,
    }


def _fmt_overhead(value: float | None) -> str:
    return "n/a (needs >= 2 sizes)" if value is None else f"{value:.1f} us/test"


def _fmt_per_file(value: float | None) -> str:
    return "n/a (needs >= 2 sizes)" if value is None else f"{value:.1f} ms/file"


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
        "| files | tests | pytest collect | pytest run "
        + "| rustest run | rustest collect (cold) | rustest collect (warm) |"
    )
    print("|---|---|---|---|---|---|---|")
    for row in report["results"]:
        print(
            f"| {row['files']} | {row['tests']} | {row['pytest_collect_s']:.2f}s "
            + f"| {row['pytest_run_s']:.2f}s "
            + f"| {row['rustest_run_s']:.2f}s | {row['rustest_collect_s']:.2f}s "
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
            + f"| pytest {cell['pytest_run_s']:.2f}s "
            + f"| rustest {cell['rustest_run_s']:.2f}s |"
        )
    derived = report["derived"]
    print(f"pytest marginal overhead: {_fmt_overhead(derived['pytest_overhead_us_per_test'])}")
    print(
        "rustest v2 marginal overhead: "
        + f"{_fmt_overhead(derived['rustest_overhead_us_per_test'])}"
    )
    print()
    print(
        f"Marginal per-FILE cost, {PER_FILE_SIZES[0][0] * PER_FILE_SIZES[0][1]} tests held "
        + f"constant, medians of {PER_FILE_REPETITIONS}, sequential:"
    )
    for cell in report["per_file"]:
        print(
            f"| {cell['files']} files | {cell['tests']} tests "
            + f"| pytest {cell['pytest_run_s']:.2f}s | v2 {cell['rustest_run_s']:.2f}s "
            + f"| v2 collect {cell['rustest_collect_only_s']:.2f}s |"
        )
    print(f"pytest marginal per-file: {_fmt_per_file(derived['pytest_per_file_ms'])}")
    print(f"rustest v2 marginal per-file: {_fmt_per_file(derived['rustest_per_file_ms'])}")
    print(
        "rustest v2 marginal per-file, collection only: "
        + f"{_fmt_per_file(derived['rustest_per_file_collect_ms'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
