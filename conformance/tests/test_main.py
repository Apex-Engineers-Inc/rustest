from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import conformance.__main__ as conformance_main
from conformance.__main__ import (
    V2_COLLECT_WAIVERS,
    V2_RUN_WAIVERS,
    WAIVERS,
    _FLAGS,
    _grade_one,
    _grade_one_collect,
    _grade_one_run,
    _load_waivers_or_exit,
    _summarize,
    discover_cases,
    main,
)
from conformance.harness.grade import CaseResult
from conformance.harness.runners import (
    CollectResult,
    FullRunResult,
    Outcomes,
    RunOutcomes,
    RunResult,
)

# Every entry either v2 ledger is allowed to hold, named one by one. Both were EMPTY at
# the close of Phase 1b.2 and the entries below all arrived with Phase 1c Task 2's four
# corpus additions -- shapes the earlier corpus did not contain, each adjudicated on probed
# evidence in that task's report. The point of asserting the *set* rather than the size is
# that a divergence cannot be papered over by editing a ledger: a new key fails here until
# someone has written down what it is.
ADJUDICATED_V2_COLLECT_WAIVERS: set[str] = {
    # pytest's `reorder_items` groups items by a higher-scoped parametrized fixture; v2 has
    # no session-wide pass, so the collected ORDER differs while the id set does not.
    "fixtures/module-param-reorder",
}

ADJUDICATED_V2_RUN_WAIVERS: set[str] = {
    # The collect-gate entry above, which diverges identically once the tests are run.
    "fixtures/module-param-reorder",
    # A conftest session fixture is rebuilt per FILE, not per worker: `build_registry` makes
    # fresh `FixtureDef` objects and the runner's cache is keyed on their identity.
    "fixtures/session-scope",
    # `pytest.exit()` reaches the compat shim's catch-all `__getattr__` and is a silent
    # no-op, so the session never stops.
    "marks/pytest-exit",
}


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

    assert result.status == "HARNESS-ERROR"
    assert "harness error" in result.detail


def test_grade_one_survives_runner_exception(tmp_path: Path) -> None:
    def _raise(case_dir: Path, args: list[str]) -> RunResult:
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    case_dir = tmp_path / "case"
    case_dir.mkdir()

    result = _grade_one(case_dir, "area/case", {}, run_pytest_fn=_raise)

    assert result.status == "HARNESS-ERROR"
    assert "harness error" in result.detail


def test_grade_one_collect_survives_runner_exception(tmp_path: Path) -> None:
    """A collect-runner blowup is contained to its own case, like the v1 path's."""

    def _raise(case_dir: Path, args: list[str]) -> CollectResult:
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    case_dir = tmp_path / "case"
    case_dir.mkdir()

    result = _grade_one_collect(case_dir, "area/case", {}, run_pytest_fn=_raise)

    assert result.status == "HARNESS-ERROR"
    assert "harness error" in result.detail


# --------------------------------------------------------------------------------------
# HARNESS-ERROR: a status of its own, and a flag of its own
# --------------------------------------------------------------------------------------


def _blowing_up_grader(tmp_path: Path, waivers: dict[str, str]) -> CaseResult:
    """Grade one case whose pytest runner raises, under *waivers*."""

    def _raise(case_dir: Path, args: list[str]) -> RunResult:
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    case_dir = tmp_path / "case"
    case_dir.mkdir(exist_ok=True)
    return _grade_one(case_dir, "area/case", waivers, run_pytest_fn=_raise)


def test_a_harness_error_is_not_a_divergence(tmp_path: Path) -> None:
    """The distinction the status exists for, asserted as an inequality.

    ``DIVERGE`` is a claim about the *runners* -- pytest and rustest answered differently.
    A harness fault is a claim about the *instrument*: no comparison happened at all. They
    printed the same ``[XX]`` until now, and P1b.2 Task 5 Sec 10.7 records the cost of
    that: a subprocess timeout under concurrent load read as a conformance regression, and
    the evidence that would have said otherwise was piped away.
    """
    result = _blowing_up_grader(tmp_path, {})

    assert result.status == "HARNESS-ERROR"
    assert result.status != "DIVERGE"


def test_a_waiver_does_not_downgrade_a_harness_error(tmp_path: Path) -> None:
    """A waived case that blows up is still ``HARNESS-ERROR``, never ``WAIVED``.

    This is the regression that matters most. A waiver records a judgement about a *known
    divergence*; a harness fault produced no comparison for that judgement to be about, so
    honouring the waiver would turn an instrument failure into a green ``[~~]`` on the
    strength of a sentence written about something else -- and the run would exit 0.

    The waiver text is still carried in the detail, as context for the reader, alongside an
    explicit statement that it does not apply.
    """
    waived = _blowing_up_grader(tmp_path, {"area/case": "known v1 gap"})

    assert waived.status == "HARNESS-ERROR"
    assert "harness error" in waived.detail
    assert "known v1 gap" in waived.detail
    assert "cannot apply" in waived.detail


def test_a_harness_error_fails_the_run_and_is_counted_separately() -> None:
    """It reddens the run, and it is counted under its own name on the summary line.

    Folding it into ``diverged`` would restore the exact ambiguity the status removes --
    a reader scanning the last line could not tell three disagreeing cases from three
    broken subprocesses.
    """
    results = [
        CaseResult("area/ok", "MATCH", ""),
        CaseResult("area/broken", "HARNESS-ERROR", "harness error: TimeoutExpired()"),
    ]

    summary, exit_code = _summarize(results)

    assert "1 harness-errors" in summary
    assert "0 diverged" in summary
    assert exit_code == 1


def test_a_clean_run_states_zero_harness_errors() -> None:
    """The bucket is printed even at zero: on a red line, "the instrument was healthy" is
    a positive fact the reader needs, not noise to be omitted."""
    summary, exit_code = _summarize([CaseResult("area/ok", "MATCH", "")])

    assert summary == "1 cases: 1 match, 0 waived, 0 stale-waivers, 0 diverged, 0 harness-errors"
    assert exit_code == 0


def test_the_harness_error_flag_is_ee_and_distinct_from_every_other(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[EE]``, printed -- and no two statuses share a flag.

    The uniqueness check is the load-bearing half: a flag table that mapped
    ``HARNESS-ERROR`` back onto ``XX`` would satisfy every other assertion in this file.
    """
    assert _FLAGS["HARNESS-ERROR"] == "EE"
    assert len(set(_FLAGS.values())) == len(_FLAGS)

    def _broken(case_dir: Path, name: str, waivers: dict[str, str]) -> CaseResult:
        return CaseResult(name, "HARNESS-ERROR", "harness error: TimeoutExpired()")

    # Injected at the grader rather than staged as a real broken case: the flag routing is
    # what is under test, and a corpus case that has to keep failing to prove it would be a
    # permanently red gate.
    monkeypatch.setattr(conformance_main, "_grade_one", _broken)
    monkeypatch.setattr(sys, "argv", ["conformance", "--only", "collection/class-collection"])

    exit_code = main()

    out = capsys.readouterr().out
    assert "[EE] collection/class-collection" in out, out
    assert "[XX]" not in out, out
    assert "1 harness-errors" in out, out
    assert exit_code == 1


# --------------------------------------------------------------------------------------
# `--only` that selects nothing
# --------------------------------------------------------------------------------------


def test_only_matching_no_cases_exits_1_and_says_so(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prefix that selects nothing is a refusal, not a green run.

    It used to print ``0 cases: 0 match, ...`` and exit **0** -- an all-clear for a
    question nobody asked. ``--only`` is typed while chasing one case, so a typo answered
    "fine"; in CI a renamed case would answer "fine" silently and forever. The message
    names the prefix *and* lists the corpus, because the next thing anyone does after this
    error is look for the name they meant to type.
    """
    monkeypatch.setattr(sys, "argv", ["conformance", "--only", "no/such-case"])

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "matched no cases" in captured.err, captured.err
    assert "no/such-case" in captured.err, captured.err
    assert "collection/class-collection" in captured.err, captured.err
    assert "0 cases:" not in captured.out, captured.out


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
    assert len(discover_cases()) == 27


def test_every_ledger_key_names_a_real_case() -> None:
    """A waiver for a case that no longer exists is dead weight, and hides a rename.

    Stale-waiver detection only fires for cases the run actually grades, so a waiver
    whose case was renamed or deleted is invisible to it -- it is never graded, never
    matches, and never gets reported. Checking both ledgers against the discovered
    names closes that hole from the other side.
    """
    known = {name for name, _ in discover_cases()}

    for ledger in (WAIVERS, V2_COLLECT_WAIVERS, V2_RUN_WAIVERS):
        unknown = set(_load_waivers_or_exit(ledger)) - known
        assert not unknown, f"{ledger.name} waives cases that do not exist: {sorted(unknown)}"


def test_v2_collect_ledger_holds_only_adjudicated_waivers() -> None:
    """The v2 collection ledger names nothing that has not been written up.

    A new entry appearing here means an unadjudicated divergence was papered over, which is
    precisely what a governed ledger is supposed to prevent. The check is on the *set*, not
    the count: swapping one waiver for another is exactly as much of a change as adding one.

    Every entry must also carry its own **mechanism**, not just a verdict. The proxy for
    that is a ``python/rustest/`` or ``_pytest/`` source reference, which is the thing a
    reader needs and the thing a hurried entry omits.
    """
    waived = _load_waivers_or_exit(V2_COLLECT_WAIVERS)

    assert set(waived) == ADJUDICATED_V2_COLLECT_WAIVERS
    for name, reason in waived.items():
        # Hoisted out of the assert so that both the repo's ruff and pre-commit's pinned
        # one format this identically -- they disagree about multi-line assert messages.
        cites_a_mechanism = "python/rustest/" in reason or "_pytest/" in reason
        assert cites_a_mechanism, f"{name} waiver states a verdict but cites no mechanism"


def test_v2_run_ledger_holds_only_adjudicated_waivers() -> None:
    """The v2 run ledger names nothing that has not been written up.

    This ledger was **empty** at the close of Phase 1b.2 -- none of the six v1 execution
    bugs (#129, #130, #131, xfail, strict xfail, the exit-5 gap) survived into v2 -- and the
    three entries it now holds all arrived with Phase 1c Task 2's corpus additions, each
    adjudicated on probed evidence. None of them is one of those six coming back, and this
    test is where that claim is enforced: a new key fails until it is named here, at which
    point somebody has to say what it is.
    """
    waived = _load_waivers_or_exit(V2_RUN_WAIVERS)

    assert set(waived) == ADJUDICATED_V2_RUN_WAIVERS
    for name, reason in waived.items():
        # Hoisted out of the assert so that both the repo's ruff and pre-commit's pinned
        # one format this identically -- they disagree about multi-line assert messages.
        cites_a_mechanism = "python/rustest/" in reason or "_pytest/" in reason
        assert cites_a_mechanism, f"{name} waiver states a verdict but cites no mechanism"


def test_grade_one_run_survives_runner_exception(tmp_path: Path) -> None:
    """A full-run blowup is contained to its own case, like both other gates'."""
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    def _raise(case_dir: Path, args: list[str]) -> FullRunResult:
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    result = _grade_one_run(case_dir, "area/case", {}, run_pytest_fn=_raise)

    assert result.status == "HARNESS-ERROR"
    assert "harness error" in result.detail


def test_grade_one_run_passes_case_args_to_both_runners(tmp_path: Path) -> None:
    """``case.toml`` args reach both runners unchanged, as in the other two gates.

    ``marks/mark-filter`` and ``marks/deselect-all`` are graded on what ``-m`` selects;
    dropping the args for one side would compare two different runs and turn a real
    selection defect into a match.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "case.toml").write_text('[case]\nargs = ["-m", "smoke"]\n', encoding="utf-8")
    seen: dict[str, list[str]] = {}

    def _record(key: str) -> Callable[[Path, list[str]], FullRunResult]:
        def runner(case_dir: Path, args: list[str]) -> FullRunResult:
            seen[key] = args
            return FullRunResult(ids=[], outcomes=RunOutcomes(0, 0, 0, 0, 0, 0, 0), exit_code=5)

        return runner

    _grade_one_run(
        case_dir,
        "area/case",
        {},
        run_pytest_fn=_record("pytest"),
        run_v2_fn=_record("v2"),
    )

    assert seen == {"pytest": ["-m", "smoke"], "v2": ["-m", "smoke"]}


def test_main_v2_run_mode_grades_the_case_v1_silently_passes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the real CLI on the worst v1 bug the corpus found.

    ``collection/unittest-basic`` is #129: v1 discards ``TestCase.run()``'s result, so a
    genuinely failing ``unittest`` suite reports **all green**. It is waived in the v1
    ledger and must MATCH here -- a red run that v2 reports as red is the entire point
    of the execution gate.
    """
    monkeypatch.setattr(sys, "argv", ["conformance", "--v2-run", "--only", "collection/unittest"])

    exit_code = main()

    out = capsys.readouterr().out
    assert "[ok] collection/unittest-basic" in out
    assert "1 cases: 1 match, 0 waived, 0 stale-waivers, 0 diverged" in out
    assert exit_code == 0


def test_main_rejects_both_v2_modes_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--v2-collect`` and ``--v2-run`` grade different contracts against different
    ledgers, so asking for both is a mistake argparse must refuse rather than a
    silent precedence rule the caller has to know.

    ``--only`` names nothing so that a build which *accepts* both flags grades zero
    cases and returns promptly. Without it, the failure mode of this test would be a
    full corpus run -- minutes of subprocesses -- rather than an assertion.
    """
    monkeypatch.setattr(
        sys, "argv", ["conformance", "--v2-collect", "--v2-run", "--only", "no/such-case"]
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 2


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
