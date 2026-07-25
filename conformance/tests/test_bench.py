from __future__ import annotations

from pathlib import Path

from conformance.bench.bench import run_benchmarks
from conformance.bench.gen import generate_suite


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
    assert row["rustest_collect_s"] is None
