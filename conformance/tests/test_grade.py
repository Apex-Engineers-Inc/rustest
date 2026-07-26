from __future__ import annotations

from pathlib import Path

from conformance.harness.grade import (
    grade_case,
    grade_collect_case,
    load_case_args,
    load_waivers,
)
from conformance.harness.runners import CollectResult, Outcomes, RunResult


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


def test_grade_diverge_on_errors_only() -> None:
    same_ids = {"test_a.py::test_x"}
    pytest_result = RunResult(ids=same_ids, outcomes=Outcomes(1, 0, 0, 1, 0, False))
    rustest_result = RunResult(ids=same_ids, outcomes=Outcomes(1, 0, 0, 0, 0, False))

    got = grade_case("area/case", pytest_result, rustest_result, {})

    assert got.status == "DIVERGE"
    assert "pytest=1/0/0/1" in got.detail
    assert "rustest=1/0/0/0" in got.detail


def test_grade_diverge_on_collection_error_only() -> None:
    """Same counts and exit code, but only one runner reported a collection error."""
    same_ids = {"test_a.py::test_x"}
    pytest_result = RunResult(ids=same_ids, outcomes=Outcomes(1, 0, 0, 0, 0, True))
    rustest_result = RunResult(ids=same_ids, outcomes=Outcomes(1, 0, 0, 0, 0, False))

    got = grade_case("area/case", pytest_result, rustest_result, {})

    assert got.status == "DIVERGE"
    assert "collection-error pytest=True rustest=False" in got.detail


def test_grade_stale_waiver() -> None:
    a = _result({"test_a.py::test_x"})

    got = grade_case("area/case", a, a, {"area/case": "known v1 gap"})

    assert got.status == "STALE-WAIVER"
    assert got.detail == "case matches but is waived: known v1 gap — remove the waiver"


def test_grade_collect_match() -> None:
    a = CollectResult(ids={"test_a.py::test_x"}, exit_code=0)
    assert grade_collect_case("area/case", a, a, {}).status == "MATCH"


def test_grade_collect_diverge_on_ids() -> None:
    got = grade_collect_case(
        "area/case",
        CollectResult(ids={"test_a.py::test_x[1]", "test_a.py::test_x[2]"}, exit_code=0),
        CollectResult(ids={"test_a.py::test_x"}, exit_code=0),
        {},
    )
    assert got.status == "DIVERGE"
    assert "missing from v2: ['test_a.py::test_x[1]', 'test_a.py::test_x[2]']" in got.detail
    assert "extra in v2: ['test_a.py::test_x']" in got.detail


def test_grade_collect_diverge_on_exit_code_only() -> None:
    """Identical (empty) id sets still diverge when the collection exit code differs.

    ``marks/deselect-all`` is exactly this shape under ``-m nosuchmark``: pytest
    deselects everything and exits 5. The exit code is half the graded contract, so
    it must fail on its own.
    """
    got = grade_collect_case(
        "area/case",
        CollectResult(ids=set(), exit_code=5),
        CollectResult(ids=set(), exit_code=0),
        {},
    )
    assert got.status == "DIVERGE"
    assert "exit codes pytest=5 v2=0" in got.detail


def test_grade_collect_ignores_run_outcomes_entirely() -> None:
    """The collect gate grades ids + exit code only -- nothing else exists to grade.

    ``CollectResult`` deliberately carries no pass/fail/skip counts: a collection-only
    surface has no outcomes, and inventing zeros for them would make every case look
    like it agreed on execution it never performed.
    """
    assert not hasattr(CollectResult(ids=set(), exit_code=0), "outcomes")


def test_grade_collect_waived() -> None:
    got = grade_collect_case(
        "area/case",
        CollectResult(ids={"test_a.py::test_x"}, exit_code=0),
        CollectResult(ids=set(), exit_code=5),
        {"area/case": "selection args land in 1b.2"},
    )
    assert got.status == "WAIVED"
    assert "selection args land in 1b.2" in got.detail


def test_grade_collect_stale_waiver() -> None:
    """Stale-waiver detection applies to the v2 ledger exactly as to the v1 one.

    Shrinking the ledger is the phase-gate metric, so a waiver that has gone inert
    must fail the run rather than quietly persist.
    """
    a = CollectResult(ids={"test_a.py::test_x"}, exit_code=0)

    got = grade_collect_case("area/case", a, a, {"area/case": "selection args land in 1b.2"})

    assert got.status == "STALE-WAIVER"
    assert got.detail == (
        "case matches but is waived: selection args land in 1b.2 — remove the waiver"
    )


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
