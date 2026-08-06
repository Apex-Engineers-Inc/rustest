"""Grade a corpus case by diffing pytest and rustest results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .runners import CollectResult, FullRunResult, RunOutcomes

try:
    import tomllib
except ImportError as exc:  # pragma: no cover - guards Python < 3.12
    raise SystemExit("conformance harness requires Python >= 3.12 (tomllib)") from exc


@dataclass(frozen=True)
class CaseResult:
    name: str
    status: str  # "MATCH" | "DIVERGE" | "WAIVED" | "STALE-WAIVER" | "HARNESS-ERROR"
    detail: str


def load_waivers(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.get("cases", {}).items()}


def load_case_args(case_dir: Path) -> list[str]:
    config = case_dir / "case.toml"
    if not config.exists():
        return []
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    return [str(a) for a in data.get("case", {}).get("args", [])]


def _adjudicate(name: str, problems: list[str], waivers: dict[str, str]) -> CaseResult:
    """Turn a case's problem list into a verdict, applying the waiver ledger.

    Shared by both gates so the waiver discipline -- including STALE-WAIVER detection,
    the check that keeps an inert waiver from quietly surviving a fix -- is written
    once and cannot drift between the collect and run ledgers.
    """
    if not problems:
        if name in waivers:
            return CaseResult(
                name,
                "STALE-WAIVER",
                f"case matches but is waived: {waivers[name]} — remove the waiver",
            )
        return CaseResult(name, "MATCH", "")
    if name in waivers:
        return CaseResult(name, "WAIVED", f"{waivers[name]} :: {'; '.join(problems)}")
    return CaseResult(name, "DIVERGE", "; ".join(problems))


def _describe_first_id_divergence(pytest_ids: list[str], v2_ids: list[str]) -> str:
    """Locate the first position at which two ordered id lists disagree.

    Reported alongside the set difference rather than instead of it: the set diff says
    *what* is wrong with the membership, the index says *where* the sequences part
    company -- and for a pure ordering or duplication defect, where both sides carry
    exactly the same ids, the index is the only thing that says anything at all.
    """
    for index, (from_pytest, from_v2) in enumerate(zip(pytest_ids, v2_ids)):
        if from_pytest != from_v2:
            return f"id order: first divergence at index {index} (pytest={from_pytest!r}, v2={from_v2!r})"
    # No positional disagreement in the overlap, so one list is a prefix of the other.
    index = min(len(pytest_ids), len(v2_ids))
    from_pytest = pytest_ids[index] if index < len(pytest_ids) else "<end>"
    from_v2 = v2_ids[index] if index < len(v2_ids) else "<end>"
    return (
        f"id count: pytest={len(pytest_ids)} v2={len(v2_ids)}, diverging at index {index} "
        + f"(pytest={from_pytest!r}, v2={from_v2!r})"
    )


def grade_collect_case(
    name: str,
    pytest_result: CollectResult,
    v2_result: CollectResult,
    waivers: dict[str, str],
) -> CaseResult:
    """Grade a case on collection alone: the ordered node-id list and the exit code.

    Those two *are* the whole ``--collect-only`` contract. There are no outcome
    counts to compare (nothing is executed) and no separate collection-error flag to
    compare (pytest signals that as exit 2, and so does v2). Stderr is not read on
    either side -- see ``run_rustest_v2_collect``.

    The id comparison is **ordered**. pytest's collection order is observable
    behaviour that v2 reproduces deliberately -- Task 3's name-sorted interleaved walk
    descends a directory at the position its own name sorts to -- and a set comparison
    is blind to it, as it is to a duplicated id. Set differences are still reported,
    because they are the readable form of a membership defect; the positional report
    is what catches everything a set cannot see.
    """
    problems: list[str] = []
    only_pytest = sorted(set(pytest_result.ids) - set(v2_result.ids))
    only_v2 = sorted(set(v2_result.ids) - set(pytest_result.ids))
    if only_pytest:
        problems.append(f"missing from v2: {only_pytest}")
    if only_v2:
        problems.append(f"extra in v2: {only_v2}")
    if pytest_result.ids != v2_result.ids:
        problems.append(_describe_first_id_divergence(pytest_result.ids, v2_result.ids))
    if pytest_result.exit_code != v2_result.exit_code:
        problems.append(f"exit codes pytest={pytest_result.exit_code} v2={v2_result.exit_code}")
    return _adjudicate(name, problems, waivers)


_RUN_TALLY_LEGEND = "passed/failed/skipped/xfailed/xpassed/errors/deselected"


def _format_run_tally(outcomes: RunOutcomes) -> str:
    """Seven numbers on one line, in the order the legend names.

    Printed with the legend rather than bare, because seven anonymous slash-separated
    integers are unreadable at exactly the moment someone needs to read them -- when a
    gate has just gone red.
    """
    return (
        f"{outcomes.passed}/{outcomes.failed}/{outcomes.skipped}/"
        + f"{outcomes.xfailed}/{outcomes.xpassed}/{outcomes.errors}/"
        + f"{outcomes.deselected}"
    )


def grade_run_case(
    name: str,
    pytest_result: FullRunResult,
    v2_result: FullRunResult,
    waivers: dict[str, str],
) -> CaseResult:
    """Grade a case on a full run: ordered ids, the seven graded counts, the exit code.

    Those three *are* the whole ``rustest`` contract as a machine reader sees it.
    Nothing else is compared, and each omission is deliberate:

    * **stdout/stderr prose** -- worded differently by design on the v2 side, and worker
      stderr legitimately carries boundary teardown output on a green run.
    * **durations** -- not a claim about behaviour.
    * **``teardown_errors``** -- not compared as a list, but it cannot hide: each entry
      counts as a failure for ``exit_code`` (``src/v2/execute.rs`` ``finish``), and the
      exit code *is* graded. It is deliberately **not** folded into ``errors`` the way
      collection errors are, because v2's own summary line does not fold it either
      (``core.py::_run_summary`` takes only ``collection_errors``). pytest does count
      such a teardown in its error bucket, so a case with an unattributable teardown
      failure will diverge on the tally -- loudly, which is the point. No corpus case has
      that shape today; when one arrives it is a real adjudication, not a mapping to add.
    * **a separate collection-error flag** -- that is exit 2 on both sides, so comparing
      it would grade the same fact twice under two names.

    Ids are compared **positionally**, like the collect gate's, and for one more reason
    here: execution order is observable behaviour, since a module-scoped fixture is torn
    down when the runner leaves the file. Set differences are still reported because they
    are the readable form of a membership defect; the positional report is what catches
    a pure ordering or duplication defect, which a set cannot see at all.

    The tally is compared as a **seven-tuple**:

    * Folding ``xfailed`` into ``skipped`` or ``xpassed`` into ``passed`` -- schema v1's
      shape -- would make the two cases this gate exists to prove (``marks/xfail``,
      ``marks/xfail-strict``) match for the wrong reason.
    * ``deselected`` is graded because **the id list cannot stand in for it.** An earlier
      version of this docstring claimed it could -- "a deselected test is simply absent
      from the list" -- and that was a false green, not a shortcut. Absent-because-
      deselected and absent-because-never-collected produce byte-identical id lists,
      identical outcome buckets and an identical exit code. A v2 that lost a deselected
      sibling outright graded MATCH under ``marks/mark-filter``. ``deselected`` is the
      only field either side publishes that can tell the two apart.
    """
    problems: list[str] = []
    only_pytest = sorted(set(pytest_result.ids) - set(v2_result.ids))
    only_v2 = sorted(set(v2_result.ids) - set(pytest_result.ids))
    if only_pytest:
        problems.append(f"missing from v2: {only_pytest}")
    if only_v2:
        problems.append(f"extra in v2: {only_v2}")
    if pytest_result.ids != v2_result.ids:
        problems.append(_describe_first_id_divergence(pytest_result.ids, v2_result.ids))
    if pytest_result.outcomes != v2_result.outcomes:
        problems.append(
            f"outcomes ({_RUN_TALLY_LEGEND}) "
            + f"pytest={_format_run_tally(pytest_result.outcomes)} "
            + f"v2={_format_run_tally(v2_result.outcomes)}"
        )
    if pytest_result.exit_code != v2_result.exit_code:
        problems.append(f"exit codes pytest={pytest_result.exit_code} v2={v2_result.exit_code}")
    return _adjudicate(name, problems, waivers)
