"""Conformance CLI: python -m conformance [--only PREFIX] [--v2-collect]"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from .harness.grade import (
    CaseResult,
    grade_case,
    grade_collect_case,
    load_case_args,
    load_waivers,
)
from .harness.runners import (
    CollectResult,
    RunResult,
    run_pytest,
    run_pytest_collect,
    run_rustest,
    run_rustest_v2_collect,
)

ROOT = Path(__file__).parent

# Two gates, two ledgers. The v1 ledger records where the shipping runner diverges from
# pytest end to end; the v2-collect ledger records only what `rustest --v2-collect-only`
# cannot yet reproduce about pytest's *collection*. They are kept apart because a v1
# entry says nothing about v2 and vice versa -- `collection/empty-suite`, for instance,
# is waived for v1 (exit 0 where pytest exits 5) and matches under v2.
WAIVERS = ROOT / "waivers.toml"
V2_COLLECT_WAIVERS = ROOT / "waivers-v2-collect.toml"


def _load_waivers_or_exit(path: Path) -> dict[str, str]:
    """Load waivers.toml, turning a malformed file into a one-line exit, not a traceback.

    waivers.toml is hand-edited; a syntax error in it is a routine mistake, not a
    harness bug, and shouldn't dump a raw tomllib.TOMLDecodeError traceback on the
    user. Naming the file and the parse error is enough to fix it.
    """
    try:
        return load_waivers(path)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"conformance: malformed waivers file {path}: {exc}") from exc


def _contained(name: str, waivers: dict[str, str], grade: Callable[[], CaseResult]) -> CaseResult:
    """Run *grade*, containing any harness failure to this one case.

    A malformed ``case.toml`` or a runner exception (including a subprocess
    timeout) must not abort the whole conformance run: it is reported as a
    divergence for this case only -- waivable by name like any other
    divergence -- and the caller continues on to the next case.
    """
    try:
        return grade()
    except Exception as exc:
        problem = f"harness error: {exc!r}"
        if name in waivers:
            return CaseResult(name, "WAIVED", f"{waivers[name]} :: {problem}")
        return CaseResult(name, "DIVERGE", problem)


def _grade_one(
    case_dir: Path,
    name: str,
    waivers: dict[str, str],
    run_pytest_fn: Callable[[Path, list[str]], RunResult] = run_pytest,
    run_rustest_fn: Callable[[Path, list[str]], RunResult] = run_rustest,
) -> CaseResult:
    """Grade a single case end to end (collection + execution), pytest vs rustest v1."""

    def grade() -> CaseResult:
        case_args = load_case_args(case_dir)
        pytest_result = run_pytest_fn(case_dir, case_args)
        rustest_result = run_rustest_fn(case_dir, case_args)
        return grade_case(name, pytest_result, rustest_result, waivers)

    return _contained(name, waivers, grade)


def _grade_one_collect(
    case_dir: Path,
    name: str,
    waivers: dict[str, str],
    run_pytest_fn: Callable[[Path, list[str]], CollectResult] = run_pytest_collect,
    run_v2_fn: Callable[[Path, list[str]], CollectResult] = run_rustest_v2_collect,
) -> CaseResult:
    """Grade a single case on collection only, pytest vs ``rustest --v2-collect-only``.

    ``case.toml`` args are passed to *both* runners unchanged, including the ones v2
    cannot honor yet (``-m``). Quietly dropping them for the v2 side would compare two
    different questions and turn a real 1b.2 gap into a fake MATCH; passing them
    through makes the gap show up as the divergence it is, waived by name.
    """

    def grade() -> CaseResult:
        case_args = load_case_args(case_dir)
        pytest_result = run_pytest_fn(case_dir, case_args)
        v2_result = run_v2_fn(case_dir, case_args)
        return grade_collect_case(name, pytest_result, v2_result, waivers)

    return _contained(name, waivers, grade)


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
    parser.add_argument(
        "--v2-collect",
        action="store_true",
        help=(
            "Grade collection only -- pytest's collected node ids and exit code against "
            "`rustest --v2-collect-only` -- using waivers-v2-collect.toml"
        ),
    )
    args = parser.parse_args()

    waivers = _load_waivers_or_exit(V2_COLLECT_WAIVERS if args.v2_collect else WAIVERS)
    grade_one = _grade_one_collect if args.v2_collect else _grade_one
    corpus = ROOT / "corpus"
    cases = sorted(
        d for d in corpus.glob("*/*/") if any(d.glob("test_*.py")) or (d / "case.toml").exists()
    )
    results: list[CaseResult] = []
    for case_dir in cases:
        name = f"{case_dir.parent.name}/{case_dir.name}"
        if not name.startswith(args.only):
            continue
        result = grade_one(case_dir, name, waivers)
        results.append(result)
        flag = {"MATCH": "ok", "WAIVED": "~~", "DIVERGE": "XX", "STALE-WAIVER": "!!"}[result.status]
        print(f"[{flag}] {result.name}" + (f"  ({result.detail})" if result.detail else ""))

    summary, exit_code = _summarize(results)
    print(f"\n{summary}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
