from __future__ import annotations

from pathlib import Path

import pytest

from conformance.bench.bench import (
    DEFAULT_SIZES,
    OVERHEAD_SIZES,
    BenchRow,
    OverheadRow,
    derive_overhead,
    run_benchmarks,
)
from conformance.bench.gen import generate_suite


def _row(
    tests: int,
    pytest_run_s: float,
    rustest_run_s: float,
    rustest_v2_run_s: float = 0.0,
) -> BenchRow:
    return {
        "files": tests // 10,
        "tests": tests,
        "pytest_collect_s": 0.1,
        "pytest_run_s": pytest_run_s,
        "rustest_run_s": rustest_run_s,
        "rustest_v2_run_s": rustest_v2_run_s,
        "rustest_collect_s": 0.05,
        "rustest_collect_warm_s": 0.01,
    }


def _overhead_row(
    tests: int,
    pytest_run_s: float,
    rustest_run_s: float,
    rustest_v2_run_s: float = 0.0,
    files: int = 100,
) -> OverheadRow:
    """A cell of the fixed-file-count measurement; `files` is constant by construction."""
    return {
        "files": files,
        "tests": tests,
        "repetitions": 5,
        "pytest_run_s": pytest_run_s,
        "rustest_run_s": rustest_run_s,
        "rustest_v2_run_s": rustest_v2_run_s,
    }


def test_generate_suite(tmp_path: Path) -> None:
    generate_suite(tmp_path, files=3, tests_per_file=4)
    files = sorted(tmp_path.glob("test_gen_*.py"))
    assert len(files) == 3
    assert files[0].read_text(encoding="utf-8").count("def test_") == 4


def test_run_benchmarks_quick() -> None:
    report = run_benchmarks(sizes=[(2, 2)], quick=True)
    row = report["results"][0]
    assert row["files"] == 2 and row["tests"] == 4
    assert row["pytest_collect_s"] > 0
    assert row["pytest_run_s"] > 0
    assert row["rustest_run_s"] > 0
    # The two fields Phase 1c Task 3 wired up: the v2 default path, and v2 collect-only
    # (previously reserved/always None).
    assert row["rustest_v2_run_s"] > 0
    assert row["rustest_collect_s"] > 0
    # Phase 2 Task 2's column: the same command with the manifest cache the cold run wrote.
    # Only asserted to exist and be positive -- a 4-test suite is far too small for the warm
    # run to be reliably *faster*, since both are dominated by interpreter start-up, and a
    # `warm < cold` assertion here would be a flaky test masquerading as a performance gate.
    # The gate number is measured on the 5 000-test suite and recorded in the task report.
    assert row["rustest_collect_warm_s"] > 0
    # `--quick` measures one overhead cell, and one point has no slope.
    assert report["derived"] == {
        "pytest_overhead_us_per_test": None,
        "rustest_overhead_us_per_test": None,
        "rustest_v2_overhead_us_per_test": None,
    }
    # ...but the cell it did measure is published, so a `None` slope is still traceable to
    # the timings behind it rather than being an unexplained blank.
    assert len(report["overhead"]) == 1
    cell = report["overhead"][0]
    assert cell["repetitions"] >= 1
    assert cell["pytest_run_s"] > 0 and cell["rustest_v2_run_s"] > 0


def test_derive_overhead_is_the_slope_between_the_two_cells() -> None:
    """Slope in microseconds per test, over the *test* delta only."""
    derived = derive_overhead(
        [
            _overhead_row(1000, pytest_run_s=1.0, rustest_run_s=0.6, rustest_v2_run_s=0.9),
            _overhead_row(5000, pytest_run_s=3.0, rustest_run_s=1.0, rustest_v2_run_s=1.7),
        ]
    )
    # (3.0 - 1.0) / 4000 * 1e6 = 500 us ; (1.0 - 0.6) / 4000 * 1e6 = 100 us
    # (1.7 - 0.9) / 4000 * 1e6 = 200 us.  Compared with a tolerance rather than for equality:
    # `1.7 - 0.9` is 0.7999999999999998 in binary floating point, and a benchmark metric that
    # demanded exact decimal arithmetic would be asserting something it does not need.
    assert derived["pytest_overhead_us_per_test"] == pytest.approx(500.0)
    assert derived["rustest_overhead_us_per_test"] == pytest.approx(100.0)
    assert derived["rustest_v2_overhead_us_per_test"] == pytest.approx(200.0)


def test_derive_overhead_is_order_independent() -> None:
    """Cells are sorted by test count, so a caller cannot invert the sign by mistake."""
    cells = [
        _overhead_row(5000, pytest_run_s=3.0, rustest_run_s=1.0, rustest_v2_run_s=1.7),
        _overhead_row(1000, pytest_run_s=1.0, rustest_run_s=0.6, rustest_v2_run_s=0.9),
    ]
    assert derive_overhead(cells) == derive_overhead(list(reversed(cells)))


def test_the_overhead_sizes_hold_the_file_count_constant() -> None:
    """The point of the rewrite, guarded as data rather than described in a comment.

    The previous derivation used the 100-file and 500-file rows of ``DEFAULT_SIZES``, so its
    "per test" figure carried the cost of importing 400 extra modules -- ~2.5 ms each. Holding
    files constant is what makes the delta about tests. The pair is data, and data drifts.
    """
    assert len(OVERHEAD_SIZES) >= 2
    assert len({files for files, _ in OVERHEAD_SIZES}) == 1, "file count must be constant"
    assert len({tests for _, tests in OVERHEAD_SIZES}) == len(
        OVERHEAD_SIZES
    ), "the cells must differ in tests per file, or there is no slope"
    # ...and it must not quietly become the row the **collect gate** measures. That is the
    # largest `DEFAULT_SIZES` entry, and a metric sharing a cell with a gate cannot be
    # retuned without moving the gate. Overlapping with a *smaller* row is harmless --
    # `measure_overhead` generates its own suites either way -- so the guard names the one
    # row that matters rather than forbidding all coincidence.
    gate_size = max(DEFAULT_SIZES, key=lambda size: size[0] * size[1])
    assert gate_size not in OVERHEAD_SIZES


def test_derive_overhead_needs_two_distinct_cells() -> None:
    none: dict[str, float | None] = {
        "pytest_overhead_us_per_test": None,
        "rustest_overhead_us_per_test": None,
        "rustest_v2_overhead_us_per_test": None,
    }
    assert derive_overhead([_overhead_row(1000, 0.5, 0.4)]) == none
    assert derive_overhead([]) == none
    # Two cells with the same test count have no slope, and dividing by the zero delta would
    # produce an infinity that reads like a measurement.
    assert derive_overhead([_overhead_row(1000, 0.5, 0.4), _overhead_row(1000, 0.9, 0.7)]) == none
