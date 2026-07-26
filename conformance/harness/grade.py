"""Grade a corpus case by diffing pytest and rustest results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .runners import CollectResult, RunResult

try:
    import tomllib
except ImportError as exc:  # pragma: no cover - guards Python < 3.12
    raise SystemExit("conformance harness requires Python >= 3.12 (tomllib)") from exc


@dataclass(frozen=True)
class CaseResult:
    name: str
    status: str  # "MATCH" | "DIVERGE" | "WAIVED" | "STALE-WAIVER"
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
    once and cannot drift between the v1 and v2-collect ledgers.
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


def grade_collect_case(
    name: str,
    pytest_result: CollectResult,
    v2_result: CollectResult,
    waivers: dict[str, str],
) -> CaseResult:
    """Grade a case on collection alone: the node-id sets and the exit code.

    Those two *are* the whole ``--v2-collect-only`` contract. There are no outcome
    counts to compare (nothing is executed) and no separate collection-error flag to
    compare (pytest signals that as exit 2, and so does v2). Stderr is not read on
    either side -- see ``run_rustest_v2_collect``.
    """
    problems: list[str] = []
    only_pytest = sorted(pytest_result.ids - v2_result.ids)
    only_v2 = sorted(v2_result.ids - pytest_result.ids)
    if only_pytest:
        problems.append(f"missing from v2: {only_pytest}")
    if only_v2:
        problems.append(f"extra in v2: {only_v2}")
    if pytest_result.exit_code != v2_result.exit_code:
        problems.append(f"exit codes pytest={pytest_result.exit_code} v2={v2_result.exit_code}")
    return _adjudicate(name, problems, waivers)


def grade_case(
    name: str,
    pytest_result: RunResult,
    rustest_result: RunResult,
    waivers: dict[str, str],
) -> CaseResult:
    problems: list[str] = []
    only_pytest = sorted(pytest_result.ids - rustest_result.ids)
    only_rustest = sorted(rustest_result.ids - pytest_result.ids)
    if only_pytest:
        problems.append(f"missing from rustest: {only_pytest}")
    if only_rustest:
        problems.append(f"extra in rustest: {only_rustest}")
    po, ro = pytest_result.outcomes, rustest_result.outcomes
    if (po.passed, po.failed, po.skipped, po.errors) != (
        ro.passed,
        ro.failed,
        ro.skipped,
        ro.errors,
    ):
        pytest_counts = f"{po.passed}/{po.failed}/{po.skipped}/{po.errors}"
        rustest_counts = f"{ro.passed}/{ro.failed}/{ro.skipped}/{ro.errors}"
        problems.append(f"outcomes pytest={pytest_counts} rustest={rustest_counts}")
    if po.exit_code != ro.exit_code:
        problems.append(f"exit codes pytest={po.exit_code} rustest={ro.exit_code}")
    if po.collection_error != ro.collection_error:
        problems.append(
            f"collection-error pytest={po.collection_error} rustest={ro.collection_error}"
        )
    return _adjudicate(name, problems, waivers)
