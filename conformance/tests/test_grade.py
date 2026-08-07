from __future__ import annotations

from pathlib import Path

from conformance.harness.grade import (
    grade_collect_case,
    grade_run_case,
    load_case_args,
    load_waivers,
)
from conformance.harness.runners import CollectResult, FullRunResult, RunOutcomes


def test_grade_collect_match() -> None:
    a = CollectResult(ids=["test_a.py::test_x"], exit_code=0)
    assert grade_collect_case("area/case", a, a, {}).status == "MATCH"


def test_grade_collect_diverge_on_ids() -> None:
    got = grade_collect_case(
        "area/case",
        CollectResult(ids=["test_a.py::test_x[1]", "test_a.py::test_x[2]"], exit_code=0),
        CollectResult(ids=["test_a.py::test_x"], exit_code=0),
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
        CollectResult(ids=[], exit_code=5),
        CollectResult(ids=[], exit_code=0),
        {},
    )
    assert got.status == "DIVERGE"
    assert "exit codes pytest=5 v2=0" in got.detail


def test_grade_collect_diverge_on_order_alone() -> None:
    """Identical id SETS in a different order is a real divergence, not a match.

    v2 reproduces pytest's collection order deliberately (the name-sorted interleaved
    walk descends a directory at the position its own name sorts to). A set
    comparison is blind to this shape, so it is the one the ordered comparison exists
    for -- and the set-diff problems must stay silent, leaving the positional report
    to say everything.
    """
    got = grade_collect_case(
        "area/case",
        CollectResult(ids=["sub/test_b.py::test_x", "test_a.py::test_y"], exit_code=0),
        CollectResult(ids=["test_a.py::test_y", "sub/test_b.py::test_x"], exit_code=0),
        {},
    )
    assert got.status == "DIVERGE"
    assert "missing from v2" not in got.detail
    assert "extra in v2" not in got.detail
    assert (
        "id order: first divergence at index 0 "
        "(pytest='sub/test_b.py::test_x', v2='test_a.py::test_y')" in got.detail
    )


def test_grade_collect_diverge_on_a_duplicated_id() -> None:
    """A duplicate collapses into a set silently; the ordered list reports it.

    Same membership on both sides, different cardinality -- reported as a count
    divergence naming the index where the sequences part company.
    """
    got = grade_collect_case(
        "area/case",
        CollectResult(ids=["test_a.py::test_x"], exit_code=0),
        CollectResult(ids=["test_a.py::test_x", "test_a.py::test_x"], exit_code=0),
        {},
    )
    assert got.status == "DIVERGE"
    assert "missing from v2" not in got.detail
    assert "extra in v2" not in got.detail
    assert "id count: pytest=1 v2=2, diverging at index 1" in got.detail
    assert "pytest='<end>', v2='test_a.py::test_x'" in got.detail


def test_grade_collect_reports_both_set_diff_and_position() -> None:
    """A membership defect gets the readable set diff *and* the positional anchor."""
    got = grade_collect_case(
        "area/case",
        CollectResult(ids=["test_a.py::test_x", "test_a.py::test_y"], exit_code=0),
        CollectResult(ids=["test_a.py::test_y"], exit_code=0),
        {},
    )
    assert got.status == "DIVERGE"
    assert "missing from v2: ['test_a.py::test_x']" in got.detail
    assert "id order: first divergence at index 0" in got.detail


def test_grade_collect_ignores_run_outcomes_entirely() -> None:
    """The collect gate grades ids + exit code only -- nothing else exists to grade.

    ``CollectResult`` deliberately carries no pass/fail/skip counts: a collection-only
    surface has no outcomes, and inventing zeros for them would make every case look
    like it agreed on execution it never performed.
    """
    assert not hasattr(CollectResult(ids=[], exit_code=0), "outcomes")


def test_grade_collect_waived() -> None:
    got = grade_collect_case(
        "area/case",
        CollectResult(ids=["test_a.py::test_x"], exit_code=0),
        CollectResult(ids=[], exit_code=5),
        {"area/case": "selection args land in 1b.2"},
    )
    assert got.status == "WAIVED"
    assert "selection args land in 1b.2" in got.detail


def test_grade_collect_stale_waiver() -> None:
    """Stale-waiver detection applies to the collect ledger exactly as to the run one.

    Shrinking the ledger is the phase-gate metric, so a waiver that has gone inert
    must fail the run rather than quietly persist.
    """
    a = CollectResult(ids=["test_a.py::test_x"], exit_code=0)

    got = grade_collect_case("area/case", a, a, {"area/case": "selection args land in 1b.2"})

    assert got.status == "STALE-WAIVER"
    assert got.detail == (
        "case matches but is waived: selection args land in 1b.2 — remove the waiver"
    )


# --------------------------------------------------------------------------------------
# grade_run_case -- the full-run gate
# --------------------------------------------------------------------------------------


def _run_result(
    ids: list[str],
    *,
    passed: int = 1,
    failed: int = 0,
    skipped: int = 0,
    xfailed: int = 0,
    xpassed: int = 0,
    errors: int = 0,
    deselected: int = 0,
    exit_code: int = 0,
) -> FullRunResult:
    return FullRunResult(
        ids=ids,
        outcomes=RunOutcomes(passed, failed, skipped, xfailed, xpassed, errors, deselected),
        exit_code=exit_code,
    )


def test_grade_run_match() -> None:
    a = _run_result(["test_a.py::test_x"])
    assert grade_run_case("area/case", a, a, {}).status == "MATCH"


TWO_IDS = ["test_a.py::test_x", "test_a.py::test_y"]


def test_grade_run_diverge_on_xfailed_alone() -> None:
    """``xfailed`` is the ONLY thing that differs -- ids, exit code and the other five
    buckets all agree -- and it still has to fail.

    Deliberately *not* the "xfail graded as a pass" shape (pytest ``0 passed, 1
    xfailed`` vs ``1 passed``): that one also moves ``passed``, so a grader comparing
    only v1's four buckets would catch it by accident and the assertion would prove
    nothing about ``xfailed``. Here the four v1 buckets are identical on both sides, so
    a four-bucket comparison reports MATCH and only the six-value one reports the truth.
    This is the realistic tally bug too: a bucket that is computed but never
    incremented.
    """
    got = grade_run_case(
        "area/case",
        _run_result(TWO_IDS, passed=1, xfailed=1),
        _run_result(TWO_IDS, passed=1, xfailed=0),
        {},
    )

    assert got.status == "DIVERGE"
    assert "pytest=1/0/0/1/0/0/0 " in got.detail
    assert "v2=1/0/0/0/0/0/0" in got.detail
    assert "passed/failed/skipped/xfailed/xpassed/errors" in got.detail


def test_grade_run_diverge_on_xpassed_alone() -> None:
    """The same isolation for ``xpassed``, the bucket where nothing else can see it.

    Both sides are green, the exit code is 0 either way, the ids match and the four v1
    buckets match. A bucket of its own is the *only* thing separating ``1 xpassed``
    from a silently dropped X.
    """
    got = grade_run_case(
        "area/case",
        _run_result(TWO_IDS, passed=1, xpassed=1),
        _run_result(TWO_IDS, passed=1, xpassed=0),
        {},
    )

    assert got.status == "DIVERGE"
    assert "pytest=1/0/0/0/1/0/0 " in got.detail
    assert "v2=1/0/0/0/0/0/0" in got.detail


def test_grade_run_diverge_on_a_lost_deselected_sibling() -> None:
    """The false green this gate shipped with, pinned as a regression.

    The scenario, from the gate review: `marks/mark-filter` runs with ``-m smoke``.
    pytest selects one test and **deselects** its sibling. A v2 that never collected the
    sibling at all -- lost it outright -- publishes:

    * the same ordered ids (the sibling was never in the graded list either way),
    * the same six outcome buckets (it did not run under pytest either),
    * the same exit code (0 both sides).

    Every other graded field is blind to it, and the case graded MATCH. ``deselected``
    is the one field either side publishes that separates "chosen against" from "never
    seen", which is why it is compared rather than inferred from the id list.
    """
    got = grade_run_case(
        "marks/mark-filter",
        _run_result(["test_marks.py::test_selected"], passed=1, deselected=1),
        _run_result(["test_marks.py::test_selected"], passed=1, deselected=0),
        {},
    )

    assert got.status == "DIVERGE"
    assert "missing from v2" not in got.detail
    assert "id order" not in got.detail
    assert "exit codes" not in got.detail
    assert "pytest=1/0/0/0/0/0/1 " in got.detail
    assert "v2=1/0/0/0/0/0/0" in got.detail
    assert "passed/failed/skipped/xfailed/xpassed/errors/deselected" in got.detail


def test_grade_run_diverge_on_exit_code_only() -> None:
    """Identical ids and identical tallies still diverge when the exit code differs.

    ``collection/empty-suite`` and ``marks/deselect-all`` are exactly this shape under
    v1 -- everything empty on both sides, pytest 5 and rustest 0 -- so the exit code
    has to fail on its own or those two cases would have read as matches.
    """
    got = grade_run_case(
        "area/case",
        _run_result([], passed=0, exit_code=5),
        _run_result([], passed=0, exit_code=0),
        {},
    )

    assert got.status == "DIVERGE"
    assert "exit codes pytest=5 v2=0" in got.detail


def test_grade_run_diverge_on_execution_order_alone() -> None:
    """Same ids, same tally, different order -- a divergence the set diff cannot see.

    Execution order is observable behaviour (a module-scoped fixture is torn down when
    the runner leaves the file), so the run gate compares ids positionally exactly as
    the collect gate does.
    """
    got = grade_run_case(
        "area/case",
        _run_result(["test_a.py::test_x", "test_b.py::test_y"], passed=2),
        _run_result(["test_b.py::test_y", "test_a.py::test_x"], passed=2),
        {},
    )

    assert got.status == "DIVERGE"
    assert "missing from v2" not in got.detail
    assert "extra in v2" not in got.detail
    assert "id order: first divergence at index 0" in got.detail


def test_grade_run_diverge_on_a_duplicated_id() -> None:
    """A test reported twice collapses into a set silently; the ordered list reports it."""
    got = grade_run_case(
        "area/case",
        _run_result(["test_a.py::test_x"]),
        _run_result(["test_a.py::test_x", "test_a.py::test_x"], passed=2),
        {},
    )

    assert got.status == "DIVERGE"
    assert "id count: pytest=1 v2=2, diverging at index 1" in got.detail


def test_grade_run_reports_both_set_diff_and_position() -> None:
    """A membership defect gets the readable set diff *and* the positional anchor.

    The set diff names the id; the index says where the two sequences part company. A
    grader that reported only the position would make a plain missing test unreadable,
    and one that reported only the diff would be blind to order.
    """
    got = grade_run_case(
        "area/case",
        _run_result(["test_a.py::test_x", "test_a.py::test_y"], passed=2),
        _run_result(["test_a.py::test_y"]),
        {},
    )

    assert got.status == "DIVERGE"
    assert "missing from v2: ['test_a.py::test_x']" in got.detail
    assert "id order: first divergence at index 0" in got.detail


def test_grade_run_waived() -> None:
    got = grade_run_case(
        "area/case",
        _run_result(["test_a.py::test_x"], passed=0, failed=1, exit_code=1),
        _run_result(["test_a.py::test_x"]),
        {"area/case": "documented reduction property"},
    )

    assert got.status == "WAIVED"
    assert "documented reduction property" in got.detail


def test_grade_run_stale_waiver() -> None:
    """Stale-waiver detection applies to the run ledger too -- it is the gate's metric.

    The v2-run ledger is expected to be *empty*; a waiver that survives a fix would be
    the one way that expectation could rot unnoticed.
    """
    a = _run_result(["test_a.py::test_x"])

    got = grade_run_case("area/case", a, a, {"area/case": "was broken once"})

    assert got.status == "STALE-WAIVER"
    assert got.detail == "case matches but is waived: was broken once — remove the waiver"


def test_run_outcomes_is_a_tally_and_carries_no_run_level_fields() -> None:
    """``RunOutcomes`` is the six-value tally plus ``deselected``, and nothing else.

    ``exit_code`` and ``collection_error`` belong to the *run*, and live on
    ``FullRunResult``. They were fields on the retired v1 gate's ``Outcomes``, which is now
    only ``parse_pytest_summary``'s return type; letting them back onto the tally would
    grade the same fact twice under two names.
    """
    assert not hasattr(RunOutcomes(0, 0, 0, 0, 0, 0, 0), "exit_code")
    assert not hasattr(RunOutcomes(0, 0, 0, 0, 0, 0, 0), "collection_error")


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
