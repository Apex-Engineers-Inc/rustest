from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from conformance.__main__ import (
    V2_COLLECT_WAIVERS,
    WAIVERS,
    _grade_one,
    _grade_one_collect,
    _load_waivers_or_exit,
    _summarize,
    discover_cases,
    main,
)
from conformance.harness.runners import CollectResult, Outcomes, RunResult

# The only divergences Phase 1b.1 pre-authorizes. Everything else must be adjudicated
# in the report and named here deliberately -- the point of the gate is that the ledger
# cannot grow silently.
PREAUTHORIZED_V2_COLLECT_WAIVERS: set[str] = set()


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


def test_grade_one_collect_survives_runner_exception(tmp_path: Path) -> None:
    """A collect-runner blowup is contained to its own case, like the v1 path's."""
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    def _raise(case_dir: Path, args: list[str]) -> CollectResult:
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    result = _grade_one_collect(case_dir, "area/case", {}, run_pytest_fn=_raise)

    assert result.status == "DIVERGE"
    assert "harness error" in result.detail


def test_grade_one_collect_grades_ids_and_exit_code(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    def _pytest(case_dir: Path, args: list[str]) -> CollectResult:
        return CollectResult(ids=["test_a.py::test_x"], exit_code=0)

    def _v2(case_dir: Path, args: list[str]) -> CollectResult:
        return CollectResult(ids=[], exit_code=5)

    result = _grade_one_collect(case_dir, "area/case", {}, run_pytest_fn=_pytest, run_v2_fn=_v2)

    assert result.status == "DIVERGE"
    assert "missing from v2" in result.detail
    assert "exit codes pytest=0 v2=5" in result.detail


def test_grade_one_collect_passes_case_args_to_both_runners(tmp_path: Path) -> None:
    """``case.toml`` args reach v2 unchanged, including ones it cannot honor yet.

    Dropping ``-m`` for the v2 side "to be fair" would ask the two runners different
    questions: pytest would deselect and v2 would not, yet the id sets would agree by
    accident and ``marks/mark-filter`` would grade as a MATCH -- turning a real 1b.2
    gap into a silent pass, and its waiver into a phantom STALE-WAIVER.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "case.toml").write_text('[case]\nargs = ["-m", "smoke"]\n', encoding="utf-8")
    seen: dict[str, list[str]] = {}

    def _record(key: str) -> Callable[[Path, list[str]], CollectResult]:
        def runner(case_dir: Path, args: list[str]) -> CollectResult:
            seen[key] = args
            return CollectResult(ids=[], exit_code=0)

        return runner

    _grade_one_collect(
        case_dir,
        "area/case",
        {},
        run_pytest_fn=_record("pytest"),
        run_v2_fn=_record("v2"),
    )

    assert seen == {"pytest": ["-m", "smoke"], "v2": ["-m", "smoke"]}


def test_corpus_case_count_is_pinned() -> None:
    """The gate's input set is asserted, not merely whatever happens to be on disk.

    Both gates report "N cases: ..." and exit 0 on a *shrinking* corpus exactly as
    happily as on a full one, so a deleted or accidentally renamed case would quietly
    weaken the gate while every summary still read green. Pinning the count makes that
    a test failure. Bump this number in the same commit that adds or removes a case.
    """
    assert len(discover_cases()) == 21


def test_every_ledger_key_names_a_real_case() -> None:
    """A waiver for a case that no longer exists is dead weight, and hides a rename.

    Stale-waiver detection only fires for cases the run actually grades, so a waiver
    whose case was renamed or deleted is invisible to it -- it is never graded, never
    matches, and never gets reported. Checking both ledgers against the discovered
    names closes that hole from the other side.
    """
    known = {name for name, _ in discover_cases()}

    for ledger in (WAIVERS, V2_COLLECT_WAIVERS):
        unknown = set(_load_waivers_or_exit(ledger)) - known
        assert not unknown, f"{ledger.name} waives cases that do not exist: {sorted(unknown)}"


def test_v2_collect_ledger_holds_only_preauthorized_waivers() -> None:
    """The 1b.1 target state, enforced: the v2 ledger names nothing unplanned.

    The plan pre-authorizes exactly two deferrals -- selection args (``-m``, which the
    ``--v2-collect-only`` surface cannot honor until 1b.2) and the fixture-closure
    parametrize ids. A new entry appearing here means an unadjudicated divergence was
    papered over, which is precisely what a shrinking ledger is supposed to prevent.
    """
    waived = _load_waivers_or_exit(V2_COLLECT_WAIVERS)

    assert set(waived) == PREAUTHORIZED_V2_COLLECT_WAIVERS
    for name, reason in waived.items():
        assert "1b.2" in reason, f"{name} waiver must name the phase that closes it"


def test_main_v2_collect_mode_grades_a_real_case(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the real CLI on the case this whole sub-phase exists for.

    ``collection/class-collection`` is the case v1 cannot get right (it collects
    methods on a class with ``__init__``; pytest refuses them) and is waived in the
    v1 ledger. Under ``--v2-collect`` it must MATCH -- the headline result of 1b.1.
    """
    monkeypatch.setattr(sys, "argv", ["conformance", "--v2-collect", "--only", "collection/class"])

    exit_code = main()

    out = capsys.readouterr().out
    assert "[ok] collection/class-collection" in out
    assert "1 cases: 1 match, 0 waived, 0 stale-waivers, 0 diverged" in out
    assert exit_code == 0


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
