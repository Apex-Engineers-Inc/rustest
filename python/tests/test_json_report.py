"""Tests for the machine-readable JSON report."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rustest.json_report import write_json_report
from rustest.reporting import CollectionError, RunReport, TestResult


def _report() -> RunReport:
    return RunReport(
        total=2,
        passed=1,
        failed=1,
        skipped=0,
        duration=0.5,
        results=(
            TestResult(
                name="test_ok",
                path="tests/test_a.py",
                status="passed",
                duration=0.1,
                message=None,
                stdout=None,
                stderr=None,
            ),
            TestResult(
                name="test_bad[1]",
                path="tests/test_a.py",
                status="failed",
                duration=0.2,
                message="boom",
                stdout=None,
                stderr=None,
            ),
        ),
        collection_errors=(CollectionError(path="tests/test_b.py", message="SyntaxError"),),
    )


def test_write_json_report_schema(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    write_json_report(_report(), out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 0,
        "duration": 0.5,
    }
    assert data["tests"][0]["id"] == "tests/test_a.py::test_ok"
    assert data["tests"][1]["id"] == "tests/test_a.py::test_bad[1]"
    assert data["tests"][1]["message"] == "boom"
    assert data["collection_errors"] == [{"path": "tests/test_b.py", "message": "SyntaxError"}]


def test_cli_writes_report_json(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_mini.py").write_text(
        "def test_one():\n    assert True\n\n\ndef test_two():\n    assert False\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "rustest", str(suite), "--report-json", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["passed"] == 1
    assert data["summary"]["failed"] == 1
    ids = {t["id"] for t in data["tests"]}
    assert any(i.endswith("test_mini.py::test_one") for i in ids)
