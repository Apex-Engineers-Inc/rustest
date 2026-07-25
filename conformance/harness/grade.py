"""Grade a corpus case by diffing pytest and rustest results."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .runners import RunResult


@dataclass(frozen=True)
class CaseResult:
    name: str
    status: str  # "MATCH" | "DIVERGE" | "WAIVED"
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
    if not problems:
        return CaseResult(name, "MATCH", "")
    if name in waivers:
        return CaseResult(name, "WAIVED", f"{waivers[name]} :: {'; '.join(problems)}")
    return CaseResult(name, "DIVERGE", "; ".join(problems))
