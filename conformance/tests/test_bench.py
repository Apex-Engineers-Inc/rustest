from __future__ import annotations

from pathlib import Path

from conformance.bench.bench import BenchRow, derive_overhead, run_benchmarks
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
    # One size cannot yield a slope.
    assert report["derived"] == {
        "pytest_overhead_us_per_test": None,
        "rustest_overhead_us_per_test": None,
        "rustest_v2_overhead_us_per_test": None,
    }


def test_derive_overhead_uses_two_largest_sizes() -> None:
    """Slope between the two largest sizes, in microseconds per test."""
    derived = derive_overhead(
        [
            _row(10, pytest_run_s=0.5, rustest_run_s=0.4, rustest_v2_run_s=0.6),
            _row(100, pytest_run_s=1.0, rustest_run_s=0.6, rustest_v2_run_s=0.9),  # small
            _row(500, pytest_run_s=3.0, rustest_run_s=1.0, rustest_v2_run_s=1.7),  # big
        ]
    )
    # (3.0 - 1.0) / 400 * 1e6 = 5000 us ; (1.0 - 0.6) / 400 * 1e6 = 1000 us
    # (1.7 - 0.9) / 400 * 1e6 = 2000 us
    assert derived["pytest_overhead_us_per_test"] == 5000.0
    assert derived["rustest_overhead_us_per_test"] == 1000.0
    assert derived["rustest_v2_overhead_us_per_test"] == 2000.0


def test_derive_overhead_needs_two_sizes() -> None:
    assert derive_overhead([_row(10, 0.5, 0.4)]) == {
        "pytest_overhead_us_per_test": None,
        "rustest_overhead_us_per_test": None,
        "rustest_v2_overhead_us_per_test": None,
    }
    assert derive_overhead([]) == {
        "pytest_overhead_us_per_test": None,
        "rustest_overhead_us_per_test": None,
        "rustest_v2_overhead_us_per_test": None,
    }
