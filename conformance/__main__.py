"""Conformance CLI: python -m conformance [--only PREFIX] [--v2-collect | --v2-run]"""

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
    grade_run_case,
    load_case_args,
    load_waivers,
)
from .harness.runners import (
    CollectResult,
    FullRunResult,
    RunResult,
    run_pytest,
    run_pytest_collect,
    run_pytest_full,
    run_rustest,
    run_rustest_v2_collect,
    run_rustest_v2_run,
)

ROOT = Path(__file__).parent

# Three gates, three ledgers. The v1 ledger records where the shipping runner diverges
# from pytest end to end; the v2-collect ledger records only what
# `rustest --v2-collect-only` cannot reproduce about pytest's *collection*; the v2-run
# ledger the same for `rustest --v2`'s *execution*. They are kept apart because an entry
# in one says nothing about the others -- `collection/empty-suite`, for instance, is
# waived for v1 (exit 0 where pytest exits 5) and matches under both v2 gates, and
# `marks/xfail-strict` is waived for v1 (no xfail concept at all) and matches under both.
WAIVERS = ROOT / "waivers.toml"
V2_COLLECT_WAIVERS = ROOT / "waivers-v2-collect.toml"
V2_RUN_WAIVERS = ROOT / "waivers-v2-run.toml"
CORPUS = ROOT / "corpus"


def discover_cases(corpus: Path = CORPUS) -> list[tuple[str, Path]]:
    """Every corpus case as ``(name, directory)``, sorted -- the gate's input set.

    A directory counts as a case when it holds ``test_*.py`` files or declares a
    ``case.toml``. Note what the first half of that does *not* cover: a case whose
    only test files match the second default ``python_files`` pattern (``*_test.py``)
    is picked up via its sibling ``test_*.py`` files or its ``case.toml``, not by the
    glob alone.
    """
    return sorted(
        (f"{d.parent.name}/{d.name}", d)
        for d in corpus.glob("*/*/")
        if any(d.glob("test_*.py")) or (d / "case.toml").exists()
    )


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


def _grade_one_run(
    case_dir: Path,
    name: str,
    waivers: dict[str, str],
    run_pytest_fn: Callable[[Path, list[str]], FullRunResult] = run_pytest_full,
    run_v2_fn: Callable[[Path, list[str]], FullRunResult] = run_rustest_v2_run,
) -> CaseResult:
    """Grade a single case on a full run, pytest vs ``rustest --v2``.

    ``case.toml`` args go to *both* runners unchanged, for the same reason they do in the
    collect gate: passing them to one side only asks the two runners different questions
    and turns a real selection defect into an accidental match.
    """

    def grade() -> CaseResult:
        case_args = load_case_args(case_dir)
        pytest_result = run_pytest_fn(case_dir, case_args)
        v2_result = run_v2_fn(case_dir, case_args)
        return grade_run_case(name, pytest_result, v2_result, waivers)

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
    # Mutually exclusive because the two v2 gates grade different contracts against
    # different ledgers; asking for both is a mistake to refuse, not a precedence rule
    # the caller has to memorize.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--v2-collect",
        action="store_true",
        help=(
            "Grade collection only -- pytest's collected node ids and exit code against "
            "`rustest --v2-collect-only` -- using waivers-v2-collect.toml"
        ),
    )
    mode.add_argument(
        "--v2-run",
        action="store_true",
        help=(
            "Grade a full run -- pytest's ordered node ids, six-value outcome tally and "
            "exit code against `rustest --v2 --report-json` -- using waivers-v2-run.toml"
        ),
    )
    args = parser.parse_args()

    if args.v2_run:
        ledger: Path = V2_RUN_WAIVERS
        grade_one: Callable[[Path, str, dict[str, str]], CaseResult] = _grade_one_run
    elif args.v2_collect:
        ledger, grade_one = V2_COLLECT_WAIVERS, _grade_one_collect
    else:
        ledger, grade_one = WAIVERS, _grade_one
    waivers = _load_waivers_or_exit(ledger)
    results: list[CaseResult] = []
    for name, case_dir in discover_cases():
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
