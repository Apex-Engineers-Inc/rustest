from __future__ import annotations

from pathlib import Path

from conformance.harness.grade import grade_case, load_case_args, load_waivers
from conformance.harness.runners import Outcomes, RunResult


def _result(ids: set[str], passed: int = 1, failed: int = 0) -> RunResult:
    return RunResult(
        ids=ids,
        outcomes=Outcomes(passed, failed, 0, 0, 1 if failed else 0, False),
    )


def test_grade_match() -> None:
    a = _result({"test_a.py::test_x"})
    assert grade_case("area/case", a, a, {}).status == "MATCH"


def test_grade_diverge_on_ids() -> None:
    got = grade_case(
        "area/case",
        _result({"test_a.py::test_x", "test_a.py::testfoo"}),
        _result({"test_a.py::test_x"}),
        {},
    )
    assert got.status == "DIVERGE"
    assert "testfoo" in got.detail


def test_grade_waived() -> None:
    got = grade_case(
        "area/case",
        _result({"test_a.py::test_x"}),
        _result(set(), passed=0),
        {"area/case": "known v1 gap"},
    )
    assert got.status == "WAIVED"
    assert "known v1 gap" in got.detail


def test_load_waivers_and_case_args(tmp_path: Path) -> None:
    (tmp_path / "waivers.toml").write_text(
        '[cases]\n"area/case" = "reason here"\n', encoding="utf-8"
    )
    assert load_waivers(tmp_path / "waivers.toml") == {"area/case": "reason here"}
    case = tmp_path / "case"
    case.mkdir()
    assert load_case_args(case) == []
    (case / "case.toml").write_text('[case]\nargs = ["-m", "smoke"]\n', encoding="utf-8")
    assert load_case_args(case) == ["-m", "smoke"]
