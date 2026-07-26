from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conformance.__main__ import _grade_one, _load_waivers_or_exit, _summarize
from conformance.harness.runners import Outcomes, RunResult


def test_load_waivers_or_exit_reports_malformed_toml(tmp_path: Path) -> None:
    """A hand-edited waivers.toml with broken syntax must fail loudly but briefly.

    Previously a syntax error propagated as a raw ``tomllib.TOMLDecodeError``
    traceback out of ``main()``. It must instead become a one-line ``SystemExit``
    that names the offending file, so a bad edit is easy to locate and fix.
    """
    bad = tmp_path / "waivers.toml"
    bad.write_text("[cases\nfoo = \n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        _load_waivers_or_exit(bad)

    message = str(excinfo.value)
    assert str(bad) in message
    assert "\n" not in message


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


def test_stale_waiver_flows_into_summary_and_exit_code(tmp_path: Path) -> None:
    match = RunResult(
        ids={"test_a.py::test_x"},
        outcomes=Outcomes(1, 0, 0, 0, 0, False),
    )

    def _matching(case_dir: Path, args: list[str]) -> RunResult:
        return match

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    waivers = {"area/case": "known v1 gap"}

    result = _grade_one(
        case_dir,
        "area/case",
        waivers,
        run_pytest_fn=_matching,
        run_rustest_fn=_matching,
    )
    assert result.status == "STALE-WAIVER"

    summary, exit_code = _summarize([result])

    assert "1 stale-waivers" in summary
    assert exit_code == 1
