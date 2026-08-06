"""Conformance CLI: python -m conformance [--only PREFIX] [--collect | --run | --real NAME]"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from .harness.grade import (
    CaseResult,
    grade_collect_case,
    grade_run_case,
    load_case_args,
    load_waivers,
)
from .harness.real import available_targets, main_real
from .harness.runners import (
    CollectResult,
    FullRunResult,
    run_pytest_collect,
    run_pytest_full,
    run_rustest_collect,
    run_rustest_run,
)

ROOT = Path(__file__).parent

# Two gates, two ledgers. The collect ledger records only what
# `rustest --collect-only` cannot reproduce about pytest's *collection*; the run ledger
# the same for a flagless `rustest`'s *execution*. They are kept apart because an entry in
# one says nothing about the other: `fixtures/session-scope` and `marks/pytest-exit` are
# waived for the run and MATCH on collect, because both diverge only once something is
# executed.
#
# **There were three.** The first graded pytest against the v1 engine end to end, against
# `waivers.toml` -- 24 entries, every one of them a v1 bug with a fixed-in-v2 citation. v1
# was deleted in Phase 4 Task 2 and the gate went with it; the ledger is ARCHIVED, not
# discarded -- it lived at `docs/superpowers/history/2026-07-29-v1-conformance-ledger/`
# until the SDD artifacts were removed from the tree before 1.0.0, and remains in git
# history, because it is the record of what the rewrite was *for*.
COLLECT_WAIVERS = ROOT / "waivers-collect.toml"
RUN_WAIVERS = ROOT / "waivers-run.toml"
CORPUS = ROOT / "corpus"

#: The two-character flag printed for each case status. ``EE`` is deliberately not a
#: variation on ``XX``: a harness error and a divergence are different *kinds* of red, and
#: the pair has to be distinguishable at a glance in a wall of output -- see
#: :func:`_contained` for what each one means and why a waiver moves neither.
_FLAGS: Final[Mapping[str, str]] = {
    "MATCH": "ok",
    "WAIVED": "~~",
    "DIVERGE": "XX",
    "STALE-WAIVER": "!!",
    "HARNESS-ERROR": "EE",
}


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
    """Load a waiver ledger, turning a malformed file into a one-line exit, not a traceback.

    A ledger is hand-edited; a syntax error in it is a routine mistake, not a
    harness bug, and shouldn't dump a raw tomllib.TOMLDecodeError traceback on the
    user. Naming the file and the parse error is enough to fix it.
    """
    try:
        return load_waivers(path)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"conformance: malformed waivers file {path}: {exc}") from exc


def _contained(name: str, waivers: dict[str, str], grade: Callable[[], CaseResult]) -> CaseResult:
    """Run *grade*, containing any harness failure to this one case as ``HARNESS-ERROR``.

    A malformed ``case.toml`` or a runner exception (including a subprocess timeout) must
    not abort the whole conformance run: it is reported for this case only and the caller
    continues on to the next case. It still fails the run -- an unanswered question is not
    a green one.

    **It is its own status, not a DIVERGE, and that is the point of the status.** The two
    say completely different things: DIVERGE means *the runners disagreed*, and
    HARNESS-ERROR means *the harness could not ask the question*. They used to print the
    same ``[XX]``, with only the detail text between them, and that ambiguity cost a real
    verification run -- P1b.2 Task 5 report Sec 10.7 records a `1 diverged` reading that
    was a subprocess timeout under concurrent load, misread as a conformance defect, with
    the evidence destroyed by a `tail -2`. That entry recommended exactly this change.

    **A waiver does not apply**, which is the second half of the same argument. A waiver is
    a recorded judgement about a *known divergence* -- "pytest and rustest disagree here,
    for this reason". A harness fault produced no comparison for that judgement to be about,
    so honouring the waiver would convert an instrument failure into a green ``[~~]`` on
    the strength of a sentence written about something else. The waiver text is still
    printed, because knowing the case was expected to diverge anyway is useful context for
    whoever reads the failure -- it is context, not a verdict.
    """
    try:
        return grade()
    except Exception as exc:
        problem = f"harness error: {exc!r}"
        if name in waivers:
            problem += (
                f" [case is waived ({waivers[name]}) -- the waiver cannot apply: "
                + "the harness never asked the question]"
            )
        return CaseResult(name, "HARNESS-ERROR", problem)


def _grade_one_collect(
    case_dir: Path,
    name: str,
    waivers: dict[str, str],
    run_pytest_fn: Callable[[Path, list[str]], CollectResult] = run_pytest_collect,
    run_rustest_fn: Callable[[Path, list[str]], CollectResult] = run_rustest_collect,
) -> CaseResult:
    """Grade a single case on collection only, pytest vs ``rustest --collect-only``.

    ``case.toml`` args are passed to *both* runners unchanged, including the ones v2
    cannot honor yet (``-m``). Quietly dropping them for the v2 side would compare two
    different questions and turn a real 1b.2 gap into a fake MATCH; passing them
    through makes the gap show up as the divergence it is, waived by name.
    """

    def grade() -> CaseResult:
        case_args = load_case_args(case_dir)
        pytest_result = run_pytest_fn(case_dir, case_args)
        v2_result = run_rustest_fn(case_dir, case_args)
        return grade_collect_case(name, pytest_result, v2_result, waivers)

    return _contained(name, waivers, grade)


def _grade_one_run(
    case_dir: Path,
    name: str,
    waivers: dict[str, str],
    run_pytest_fn: Callable[[Path, list[str]], FullRunResult] = run_pytest_full,
    run_rustest_fn: Callable[[Path, list[str]], FullRunResult] = run_rustest_run,
) -> CaseResult:
    """Grade a single case on a full run, pytest vs a flagless ``rustest``.

    ``case.toml`` args go to *both* runners unchanged, for the same reason they do in the
    collect gate: passing them to one side only asks the two runners different questions
    and turns a real selection defect into an accidental match.
    """

    def grade() -> CaseResult:
        case_args = load_case_args(case_dir)
        pytest_result = run_pytest_fn(case_dir, case_args)
        v2_result = run_rustest_fn(case_dir, case_args)
        return grade_run_case(name, pytest_result, v2_result, waivers)

    return _contained(name, waivers, grade)


def _summarize(results: list[CaseResult]) -> tuple[str, int]:
    """Build the trailing summary line and the process exit code for *results*.

    A STALE-WAIVER (a waiver whose case now matches) fails the run exactly
    like an unwaived DIVERGE: shrinking the ledger is the phase-gate metric, so
    a waiver that has gone silently inert must not go unnoticed.

    A HARNESS-ERROR fails the run too, and gets its **own count** on the line rather than
    being folded into ``diverged``. The count is the reason the status exists: a reader
    scanning the last line has to be able to tell "the runners disagree about three cases"
    from "the instrument fell over on three cases", and a fold makes those two readings
    identical. The term is emitted unconditionally, unlike a zero bucket in pytest's own
    summary, because ``0 harness-errors`` is a positive statement that the instrument was
    healthy -- which is exactly what a reader wants confirmed when the rest of the line is
    red.
    """
    diverged = [r for r in results if r.status == "DIVERGE"]
    stale = [r for r in results if r.status == "STALE-WAIVER"]
    harness_errors = [r for r in results if r.status == "HARNESS-ERROR"]
    matched = sum(r.status == "MATCH" for r in results)
    waived = sum(r.status == "WAIVED" for r in results)
    summary = (
        f"{len(results)} cases: {matched} match, {waived} waived, "
        + f"{len(stale)} stale-waivers, {len(diverged)} diverged, "
        + f"{len(harness_errors)} harness-errors"
    )
    exit_code = 1 if diverged or stale or harness_errors else 0
    return summary, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(prog="conformance")
    parser.add_argument("--only", default="", help="Only run cases whose name starts with PREFIX")
    # Mutually exclusive because the two gates grade different contracts against
    # different ledgers; asking for both is a mistake to refuse, not a precedence rule
    # the caller has to memorize.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--collect",
        action="store_true",
        help=(
            "Grade collection only -- pytest's collected node ids and exit code against "
            "`rustest --collect-only` -- using waivers-collect.toml"
        ),
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help=(
            "Grade a full run -- pytest's ordered node ids, six-value outcome tally and "
            "exit code against `rustest --report-json` -- using waivers-run.toml"
        ),
    )
    mode.add_argument(
        "--real",
        default="",
        metavar="NAME",
        help=(
            "Run a real-world suite (conformance/real/NAME.toml, or 'all') under pytest and "
            + "then under flagless rustest, and diff per-test statuses, the tally and the "
            + f"exit code against that repo's ledger. Targets: {', '.join(available_targets())}"
        ),
    )
    parser.add_argument(
        "--real-setup-only",
        action="store_true",
        help="With --real: clone and provision the isolated environment, then stop.",
    )
    parser.add_argument(
        "--real-rebuild-env",
        action="store_true",
        help="With --real: delete and rebuild the target's isolated venv before running.",
    )
    args = parser.parse_args()

    if args.real:
        return main_real(
            args.real,
            setup_only=args.real_setup_only,
            rebuild_env=args.real_rebuild_env,
        )

    if args.run:
        ledger: Path = RUN_WAIVERS
        grade_one: Callable[[Path, str, dict[str, str]], CaseResult] = _grade_one_run
    elif args.collect:
        ledger, grade_one = COLLECT_WAIVERS, _grade_one_collect
    else:
        # A bare `python -m conformance` used to be the **v1 end-to-end gate**. That gate is
        # gone with the engine it measured, and the one thing this must not do is quietly
        # answer a different question: an old CI file, or a maintainer's habit, would get a
        # green from a gate it never asked for. Naming both surviving gates costs one line
        # and cannot be misread.
        print(
            "conformance: choose a gate -- --collect (collection) or --run (a full"
            + " run). A bare invocation was the end-to-end gate for the previous engine,"
            + " retired with it; its ledger is archived in git history.",
            file=sys.stderr,
        )
        return 4
    waivers = _load_waivers_or_exit(ledger)
    cases = discover_cases()
    results: list[CaseResult] = []
    for name, case_dir in cases:
        if not name.startswith(args.only):
            continue
        result = grade_one(case_dir, name, waivers)
        results.append(result)
        flag = _FLAGS[result.status]
        print(f"[{flag}] {result.name}" + (f"  ({result.detail})" if result.detail else ""))

    if not results:
        # Grading nothing used to print "0 cases: ..." and exit **0** -- a green run that
        # proved nothing. `--only` is normally typed while chasing one case, so a typo in
        # the prefix would answer "all clear" to a question that was never asked; in CI a
        # renamed case would do the same silently and forever. Both are refusals now.
        if args.only:
            print(
                f"conformance: --only {args.only!r} matched no cases "
                + f"(the corpus has {len(cases)}: {', '.join(name for name, _ in cases)})",
                file=sys.stderr,
            )
        else:
            print(f"conformance: no corpus cases found under {CORPUS}", file=sys.stderr)
        return 1

    summary, exit_code = _summarize(results)
    print(f"\n{summary}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
