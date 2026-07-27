"""Per-test mutation verification for P1c Task 2's gate-instrument change.

Task 2 added a fifth case status, ``HARNESS-ERROR`` (flag ``[EE]``), and made an
``--only`` prefix that matches nothing exit 1. Both are **grading logic** -- they change
what the instrument concludes -- so each branch of the new routing gets a row here.

Each row applies one textual mutation and runs ONLY the tests named for that row. A
non-zero pytest exit is a KILL. A zero exit is a SURVIVOR. A timeout (180s) is a SURVIVOR,
never a kill. A row whose anchor does not appear exactly once is reported as BAD ANCHOR
rather than silently skipped, so a reflow that moves an anchor cannot quietly shrink the
table.

Nothing here mutates the *runner* under test. These rows answer "would the gate still
notice?", which is a different question from "is rustest correct?" -- the gate itself
answers that one.

Run: `uv run python conformance/evidence/mutate_p1c_task2.py [row ids...]`

WARNING: this edits tracked sources in place and restores them afterwards. Do not run it
with unsaved work in the files it touches.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TIMEOUT = 180

MAIN = "conformance/__main__.py"
T_MAIN = "conformance/tests/test_main.py"

DISTINCT = f"{T_MAIN}::test_a_harness_error_is_not_a_divergence"
WAIVER = f"{T_MAIN}::test_a_waiver_does_not_downgrade_a_harness_error"
COUNTED = f"{T_MAIN}::test_a_harness_error_fails_the_run_and_is_counted_separately"
CLEAN = f"{T_MAIN}::test_a_clean_run_states_zero_harness_errors"
FLAG = f"{T_MAIN}::test_the_harness_error_flag_is_ee_and_distinct_from_every_other"
ONLY = f"{T_MAIN}::test_only_matching_no_cases_exits_1_and_says_so"
CONTAINED = [
    f"{T_MAIN}::test_grade_one_survives_malformed_case_toml",
    f"{T_MAIN}::test_grade_one_survives_runner_exception",
    f"{T_MAIN}::test_grade_one_collect_survives_runner_exception",
    f"{T_MAIN}::test_grade_one_run_survives_runner_exception",
]


@dataclass
class Row:
    id: int
    area: str
    file: str
    old: str
    new: str
    tests: list[str]
    note: str = ""
    extra: list[tuple[str, str, str]] = field(default_factory=list)


ROWS: list[Row] = [
    # -------------------------------------------------------------- the status itself
    Row(
        1,
        "status",
        MAIN,
        'return CaseResult(name, "HARNESS-ERROR", problem)',
        'return CaseResult(name, "DIVERGE", problem)',
        [DISTINCT, *CONTAINED],
        "the pre-Task-2 behaviour restored: a harness fault reported as a divergence",
    ),
    Row(
        2,
        "waiver",
        MAIN,
        "if name in waivers:\n            problem += (",
        'if name in waivers:\n            return CaseResult(name, "WAIVED", problem)\n        if name in waivers:\n            problem += (',
        [WAIVER],
        "a waived case's harness fault downgraded to a green [~~] -- the worst shape",
    ),
    Row(
        3,
        "waiver",
        MAIN,
        'f" [case is waived ({waivers[name]}) -- the waiver cannot apply: "',
        'f" [case is waived -- the waiver cannot apply: "',
        [WAIVER],
        "the waiver's own text dropped from the detail, losing the reader's context",
    ),
    # ---------------------------------------------------------------- the summary line
    Row(
        4,
        "summary",
        MAIN,
        'harness_errors = [r for r in results if r.status == "HARNESS-ERROR"]',
        'harness_errors = [r for r in results if r.status == "NEVER-EMITTED"]',
        [COUNTED],
        "harness errors counted as zero -- the count exists but never fires",
    ),
    Row(
        5,
        "summary",
        MAIN,
        '+ f"{len(harness_errors)} harness-errors"',
        '+ f"{len(diverged)} harness-errors"',
        [COUNTED],
        "the harness-error count read off the wrong bucket",
    ),
    Row(
        6,
        "summary",
        MAIN,
        '+ f"{len(stale)} stale-waivers, {len(diverged)} diverged, "\n        + f"{len(harness_errors)} harness-errors"',
        '+ f"{len(stale)} stale-waivers, {len(diverged) + len(harness_errors)} diverged"',
        [COUNTED, CLEAN],
        "harness errors FOLDED into diverged -- the exact ambiguity the status removes",
    ),
    Row(
        7,
        "exit-code",
        MAIN,
        "exit_code = 1 if diverged or stale or harness_errors else 0",
        "exit_code = 1 if diverged or stale else 0",
        [COUNTED],
        "a harness error stops failing the run: the instrument breaks and CI goes green",
    ),
    # ----------------------------------------------------------------------- the flag
    Row(
        8,
        "flag",
        MAIN,
        '"HARNESS-ERROR": "EE",',
        '"HARNESS-ERROR": "XX",',
        [FLAG],
        "[EE] collapsed back onto [XX] -- distinguishable status, indistinguishable output",
    ),
    # ------------------------------------------------------------- --only with no match
    Row(
        9,
        "only",
        MAIN,
        "        return 1\n\n    summary, exit_code = _summarize(results)",
        "        return 0\n\n    summary, exit_code = _summarize(results)",
        [ONLY],
        "a prefix matching nothing exits 0 again -- all-clear for a question never asked",
    ),
    Row(
        10,
        "only",
        MAIN,
        'f"conformance: --only {args.only!r} matched no cases "',
        'f"conformance: --only matched no cases "',
        [ONLY],
        "the message stops naming the prefix, which is the one thing the reader needs",
    ),
    Row(
        11,
        "only",
        MAIN,
        "+ f\"(the corpus has {len(cases)}: {', '.join(name for name, _ in cases)})\",",
        '+ f"(the corpus has {len(cases)})",',
        [ONLY],
        "the corpus listing dropped, so a typo cannot be corrected from the message",
    ),
]


def run_tests(tests: list[str]) -> tuple[int, str]:
    cmd = ["uv", "run", "pytest", "-q", "--no-header", *tests]
    try:
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    return proc.returncode, (proc.stdout + proc.stderr)[-400:]


def main() -> int:
    only = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    killed: list[int] = []
    survived: list[tuple[int, str]] = []
    bad: list[tuple[int, str]] = []

    for row in ROWS:
        if only is not None and row.id not in only:
            continue
        edits = [(row.file, row.old, row.new), *row.extra]
        originals: dict[str, str] = {}
        ok = True
        for path, old, new in edits:
            target = REPO / path
            if path not in originals:
                originals[path] = target.read_text(encoding="utf-8")
            current = target.read_text(encoding="utf-8")
            hits = current.count(old)
            if hits != 1:
                bad.append((row.id, f"anchor appears {hits}x in {path}"))
                ok = False
                break
            target.write_text(current.replace(old, new), encoding="utf-8")
        if not ok:
            for path, text in originals.items():
                (REPO / path).write_text(text, encoding="utf-8")
            continue

        started = time.time()
        code, tail = run_tests(row.tests)
        elapsed = time.time() - started
        for path, text in originals.items():
            (REPO / path).write_text(text, encoding="utf-8")

        if code == -1:
            survived.append((row.id, f"TIMEOUT after {TIMEOUT}s"))
            verdict = "SURVIVED (timeout)"
        elif code != 0:
            killed.append(row.id)
            verdict = "killed"
        else:
            survived.append((row.id, tail))
            verdict = "SURVIVED"
        print(f"[{row.id:>3}] {row.area:<10} {verdict:<18} {elapsed:5.1f}s  {row.note}", flush=True)

    total = len(killed) + len(survived)
    print(f"\n{len(killed)}/{total} killed")
    if survived:
        print("\nSURVIVORS:")
        for rid, tail in survived:
            print(f"  row {rid}: {tail[:400]}")
    if bad:
        print("\nBAD ANCHORS:")
        for rid, why in bad:
            print(f"  row {rid}: {why}")
    return 0 if not survived and not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
