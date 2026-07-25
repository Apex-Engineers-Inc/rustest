"""Conformance CLI: python -m conformance [--only PREFIX]"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from .harness.grade import CaseResult, grade_case, load_case_args, load_waivers
from .harness.runners import RunResult, run_pytest, run_rustest

ROOT = Path(__file__).parent


def _grade_one(
    case_dir: Path,
    name: str,
    waivers: dict[str, str],
    run_pytest_fn: Callable[[Path, list[str]], RunResult] = run_pytest,
    run_rustest_fn: Callable[[Path, list[str]], RunResult] = run_rustest,
) -> CaseResult:
    """Grade a single case, containing any harness failure to that case.

    A malformed ``case.toml`` or a runner exception (including a subprocess
    timeout) must not abort the whole conformance run: it is reported as a
    divergence for this case only -- waivable by name like any other
    divergence -- and the caller continues on to the next case.
    """
    try:
        case_args = load_case_args(case_dir)
        pytest_result = run_pytest_fn(case_dir, case_args)
        rustest_result = run_rustest_fn(case_dir, case_args)
        return grade_case(name, pytest_result, rustest_result, waivers)
    except Exception as exc:
        problem = f"harness error: {exc!r}"
        if name in waivers:
            return CaseResult(name, "WAIVED", f"{waivers[name]} :: {problem}")
        return CaseResult(name, "DIVERGE", problem)


def _summarize(results: list[CaseResult]) -> tuple[str, int]:
    """Build the trailing summary line and the process exit code for *results*.

    A STALE-WAIVER (a waiver whose case now matches) fails the run exactly
    like an unwaived DIVERGE: shrinking waivers.toml is the v2 phase-gate
    metric, so a waiver that has gone silently inert must not go unnoticed.
    """
    diverged = [r for r in results if r.status == "DIVERGE"]
    stale = [r for r in results if r.status == "STALE-WAIVER"]
    matched = sum(r.status == "MATCH" for r in results)
    waived = sum(r.status == "WAIVED" for r in results)
    summary = (
        f"{len(results)} cases: {matched} match, {waived} waived, "
        + f"{len(stale)} stale-waivers, {len(diverged)} diverged"
    )
    exit_code = 1 if diverged or stale else 0
    return summary, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(prog="conformance")
    parser.add_argument("--only", default="", help="Only run cases whose name starts with PREFIX")
    args = parser.parse_args()

    waivers = load_waivers(ROOT / "waivers.toml")
    corpus = ROOT / "corpus"
    cases = sorted(
        d for d in corpus.glob("*/*/") if any(d.glob("test_*.py")) or (d / "case.toml").exists()
    )
    results: list[CaseResult] = []
    for case_dir in cases:
        name = f"{case_dir.parent.name}/{case_dir.name}"
        if not name.startswith(args.only):
            continue
        result = _grade_one(case_dir, name, waivers)
        results.append(result)
        flag = {"MATCH": "ok", "WAIVED": "~~", "DIVERGE": "XX", "STALE-WAIVER": "!!"}[result.status]
        print(f"[{flag}] {result.name}" + (f"  ({result.detail})" if result.detail else ""))

    summary, exit_code = _summarize(results)
    print(f"\n{summary}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
