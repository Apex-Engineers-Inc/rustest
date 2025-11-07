from __future__ import annotations

from types import SimpleNamespace

from .helpers import ensure_rust_stub
from rustest.reporting import RunReport, TestResult

ensure_rust_stub()


class TestReportingConversion:
    def test_from_py_converts_nested_results(self) -> None:
        py_result = SimpleNamespace(
            name="test_sample",
            path="tests/test_sample.py",
            status="passed",
            duration=0.123,
            message=None,
            stdout="output",
            stderr=None,
        )
        py_report = SimpleNamespace(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=0.123,
            results=[py_result],
        )

        report = RunReport.from_py(py_report)

        assert report.total == 1
        assert report.passed == 1
        assert len(report.results) == 1
        result = report.results[0]
        assert isinstance(result, TestResult)
        assert result.name == "test_sample"
        assert result.stdout == "output"

    def test_iter_status_filters_results(self) -> None:
        passed = TestResult(
            name="test_ok",
            path="tests/test_module.py",
            status="passed",
            duration=0.01,
            message=None,
            stdout=None,
            stderr=None,
        )
        failed = TestResult(
            name="test_fail",
            path="tests/test_module.py",
            status="failed",
            duration=0.02,
            message="boom",
            stdout=None,
            stderr=None,
        )
        report = RunReport(
            total=2,
            passed=1,
            failed=1,
            skipped=0,
            duration=0.03,
            results=(passed, failed),
        )

        failures = list(report.iter_status("failed"))

        assert failures == [failed]
