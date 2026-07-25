from __future__ import annotations

import subprocess
from pathlib import Path

from conformance.__main__ import _grade_one
from conformance.harness.runners import RunResult


def test_grade_one_survives_malformed_case_toml(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "case.toml").write_text("[case\nargs = [1, 2", encoding="utf-8")

    result = _grade_one(case_dir, "area/case", {})

    assert result.status == "DIVERGE"
    assert "harness error" in result.detail


def test_grade_one_survives_runner_exception(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    def _raise(case_dir: Path, args: list[str]) -> RunResult:
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    result = _grade_one(case_dir, "area/case", {}, run_pytest_fn=_raise)

    assert result.status == "DIVERGE"
    assert "harness error" in result.detail
