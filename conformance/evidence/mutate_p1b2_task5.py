"""Per-test mutation verification for P1b.2 Task 5 (the full-run conformance gate).

Each row applies one textual mutation to a conformance-harness source file (or to the
v2-run ledger) and runs ONLY the tests named for that row. A non-zero pytest exit is a
KILL. A zero exit is a SURVIVOR. A timeout (180s) is a SURVIVOR, never a kill. A row
whose anchor does not appear exactly once is reported as BAD ANCHOR rather than silently
skipped, so a reflow that moves an anchor cannot quietly reduce the table.

Every row here mutates the *harness*, not the runner under test: this table proves that
the new gate would notice if it stopped grading something, which is a different question
from whether `rustest` is correct (that is what the gate itself answers).

Run: `uv run python conformance/evidence/mutate_p1b2_task5.py [row ids...]`

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

RUN = "conformance/harness/runners.py"
GRD = "conformance/harness/grade.py"
MAIN = "conformance/__main__.py"
LEDGER = "conformance/waivers-run.toml"

T_RUN = "conformance/tests/test_runners.py"
T_GRD = "conformance/tests/test_grade.py"
T_MAIN = "conformance/tests/test_main.py"


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
    # ------------------------------------------------------------------ summary parsing
    Row(
        1,
        "summary",
        RUN,
        r'_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed|deselected)")',
        r'_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xpassed|deselected)")',
        [
            f"{T_RUN}::test_parse_pytest_summary_reads_all_six_buckets",
            f"{T_RUN}::test_parse_pytest_summary_does_not_read_xfailed_as_failed",
            f"{T_RUN}::test_parse_pytest_summary_matches_real_pytest_on_every_bucket",
            f"{T_RUN}::test_full_run_runners_agree_on_all_six_outcomes[run_pytest_full]",
        ],
        "xfailed token dropped: an expected failure is tallied as nothing",
    ),
    Row(
        2,
        "summary",
        RUN,
        r'_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed|deselected)")',
        r'_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|deselected)")',
        [
            f"{T_RUN}::test_parse_pytest_summary_reads_all_six_buckets",
            f"{T_RUN}::test_parse_pytest_summary_does_not_read_xpassed_as_passed",
        ],
        "xpassed token dropped",
    ),
    Row(
        3,
        "summary",
        RUN,
        '            counts["failed"] = found.get("failed", 0)',
        '            counts["failed"] = found.get("failed", 0) + found.get("xfailed", 0)',
        [f"{T_RUN}::test_parse_pytest_summary_does_not_read_xfailed_as_failed"],
        "xfailed folded into failed -- a green run reads as red",
    ),
    Row(
        4,
        "summary",
        RUN,
        '            counts["passed"] = found.get("passed", 0)',
        '            counts["passed"] = found.get("passed", 0) + found.get("xpassed", 0)',
        [f"{T_RUN}::test_parse_pytest_summary_does_not_read_xpassed_as_passed"],
        "xpassed folded into passed -- an X goes invisible",
    ),
    # ----------------------------------------------------------------- pytest run oracle
    Row(
        5,
        "pytest oracle",
        RUN,
        "    ids = [] if interrupted else parse_pytest_collect(collect.stdout)",
        "    ids = parse_pytest_collect(collect.stdout)",
        [
            f"{T_RUN}::test_full_run_runners_report_no_executed_ids_when_collection"
            + "_is_interrupted[run_pytest_full]"
        ],
        "Interrupted rule dropped: collected ids graded as executed ones",
    ),
    Row(
        6,
        "pytest oracle",
        RUN,
        "    interrupted = collect.returncode == _PYTEST_EXIT_INTERRUPTED",
        "    interrupted = collect.returncode != _PYTEST_EXIT_INTERRUPTED",
        [f"{T_RUN}::test_full_run_runners_agree_on_the_mini_suite[run_pytest_full]"],
        "Interrupted rule inverted: a healthy run reports no ids",
    ),
    Row(
        7,
        "pytest oracle",
        RUN,
        "        exit_code=run.returncode,\n    )",
        "        exit_code=collect.returncode,\n    )",
        [f"{T_RUN}::test_full_run_runners_agree_on_the_mini_suite[run_pytest_full]"],
        "exit code read off the collect pass, which never fails",
    ),
    Row(
        8,
        "pytest oracle",
        RUN,
        '        run = _run([*base, "-q", "--tb=no", *args], work)',
        '        run = _run([*base, "-q", "--tb=no"], work)',
        [f"{T_RUN}::test_full_run_runners_pass_case_args_through[run_pytest_full]"],
        "case args dropped from the run pass -- deselection never happens",
    ),
    Row(
        9,
        "pytest oracle",
        RUN,
        '        collect = _run([*base, "--collect-only", "-q", *args], work)',
        '        collect = _run([*base, "--collect-only", "-q"], work)',
        [f"{T_RUN}::test_full_run_runners_pass_case_args_through[run_pytest_full]"],
        "case args dropped from the collect pass -- ids grow back",
    ),
    Row(
        10,
        "pytest oracle",
        RUN,
        "        work = _isolate_case(case_dir.resolve(), Path(tmp))\n"
        + '        base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]',
        "        work = case_dir.resolve()\n"
        + '        base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]',
        [f"{T_RUN}::test_full_run_runners_ignore_a_surrounding_project_config[run_pytest_full]"],
        "isolation dropped: a config above the case leaks in",
    ),
    # -------------------------------------------------------------------- v2 run reading
    Row(
        11,
        "v2 report",
        RUN,
        '        ids=[str(test["id"]) for test in tests],',
        '        ids=sorted(str(test["id"]) for test in tests),',
        [
            f"{T_RUN}::test_full_run_runners_agree_on_all_six_outcomes[run_rustest_run]",
            f"{T_RUN}::test_full_run_runners_agree_on_the_mini_suite[run_rustest_run]",
        ],
        "report ids sorted -- execution order becomes ungradeable",
    ),
    Row(
        12,
        "v2 report",
        RUN,
        '            errors=summary["error"] + len(collection_errors),',
        '            errors=summary["error"],',
        [
            f"{T_RUN}::test_full_run_runners_report_no_executed_ids_when_collection"
            + "_is_interrupted[run_rustest_run]"
        ],
        "collection errors no longer folded into the error bucket",
    ),
    Row(
        13,
        "v2 report",
        RUN,
        "        exit_code=proc.returncode,\n    )\n\n\ndef run_rustest(",
        '        exit_code=int(data["exit_code"]),\n    )\n\n\ndef run_rustest(',
        [f"{T_RUN}::test_run_rustest_run_grades_the_process_exit_code_not_the_reports_claim"],
        "grades v2's claim about its exit code instead of the observed one",
    ),
    Row(
        14,
        "v2 report",
        RUN,
        "        if not report_path.exists():\n            raise RuntimeError(\n"
        + '                f"rustest wrote no report',
        "        if False:\n            raise RuntimeError(\n"
        + '                f"rustest wrote no report',
        [f"{T_RUN}::test_run_rustest_run_raises_when_no_report_is_written"],
        "missing-report guard removed -- a dead run grades as data",
    ),
    Row(
        15,
        "v2 report",
        RUN,
        '            xfailed=summary["xfailed"],',
        "            xfailed=0,",
        [f"{T_RUN}::test_full_run_runners_agree_on_all_six_outcomes[run_rustest_run]"],
        "xfailed bucket never read off the report",
    ),
    Row(
        16,
        "v2 report",
        RUN,
        '            xpassed=summary["xpassed"],',
        "            xpassed=0,",
        [f"{T_RUN}::test_full_run_runners_agree_on_all_six_outcomes[run_rustest_run]"],
        "xpassed bucket never read off the report",
    ),
    Row(
        17,
        "v2 report",
        RUN,
        '            [sys.executable, "-m", "rustest", "--report-json", '
        + "str(report_path), *args],",
        '            [sys.executable, "-m", "rustest", "--report-json", str(report_path)],',
        [f"{T_RUN}::test_full_run_runners_pass_case_args_through[run_rustest_run]"],
        "case args dropped on the v2 side",
    ),
    Row(
        18,
        "v2 report",
        RUN,
        "        work = _isolate_case(case_dir.resolve(), Path(tmp))\n"
        + "        # Beside the copy, never inside it",
        "        work = case_dir.resolve()\n        # Beside the copy, never inside it",
        [f"{T_RUN}::test_full_run_runners_ignore_a_surrounding_project_config[run_rustest_run]"],
        "isolation dropped on the v2 side",
    ),
    # ------------------------------------------------------------------------- the grader
    Row(
        19,
        "grader",
        GRD,
        "    if pytest_result.ids != v2_result.ids:\n"
        + "        problems.append(_describe_first_id_divergence(pytest_result.ids, v2_result.ids))\n"
        + "    if pytest_result.outcomes != v2_result.outcomes:",
        "    if pytest_result.outcomes != v2_result.outcomes:",
        [
            f"{T_GRD}::test_grade_run_diverge_on_execution_order_alone",
            f"{T_GRD}::test_grade_run_diverge_on_a_duplicated_id",
        ],
        "ordered id comparison dropped",
    ),
    Row(
        20,
        "grader",
        GRD,
        "    if pytest_result.ids != v2_result.ids:\n"
        + "        problems.append(_describe_first_id_divergence(pytest_result.ids, v2_result.ids))\n"
        + "    if pytest_result.outcomes != v2_result.outcomes:",
        "    if set(pytest_result.ids) != set(v2_result.ids):\n"
        + "        problems.append(_describe_first_id_divergence(pytest_result.ids, v2_result.ids))\n"
        + "    if pytest_result.outcomes != v2_result.outcomes:",
        [
            f"{T_GRD}::test_grade_run_diverge_on_execution_order_alone",
            f"{T_GRD}::test_grade_run_diverge_on_a_duplicated_id",
        ],
        "ids compared as sets -- order and duplication go unseen",
    ),
    Row(
        21,
        "grader",
        GRD,
        "    if pytest_result.outcomes != v2_result.outcomes:",
        "    if False:",
        [
            f"{T_GRD}::test_grade_run_diverge_on_xfailed_alone",
            f"{T_GRD}::test_grade_run_diverge_on_xpassed_alone",
        ],
        "outcome tally never compared",
    ),
    Row(
        22,
        "grader",
        GRD,
        "    if pytest_result.outcomes != v2_result.outcomes:",
        "    if (\n"
        + "        pytest_result.outcomes.passed,\n"
        + "        pytest_result.outcomes.failed,\n"
        + "        pytest_result.outcomes.skipped,\n"
        + "        pytest_result.outcomes.errors,\n"
        + "    ) != (\n"
        + "        v2_result.outcomes.passed,\n"
        + "        v2_result.outcomes.failed,\n"
        + "        v2_result.outcomes.skipped,\n"
        + "        v2_result.outcomes.errors,\n"
        + "    ):",
        [
            f"{T_GRD}::test_grade_run_diverge_on_xfailed_alone",
            f"{T_GRD}::test_grade_run_diverge_on_xpassed_alone",
        ],
        "tally compared on v1's FOUR buckets -- the two X buckets go unseen",
    ),
    Row(
        23,
        "grader",
        GRD,
        "    if pytest_result.exit_code != v2_result.exit_code:\n"
        + '        problems.append(f"exit codes pytest={pytest_result.exit_code} '
        + 'v2={v2_result.exit_code}")\n'
        + "    return _adjudicate(name, problems, waivers)\n\n\ndef grade_case(",
        "    return _adjudicate(name, problems, waivers)\n\n\ndef grade_case(",
        [f"{T_GRD}::test_grade_run_diverge_on_exit_code_only"],
        "exit code never compared",
    ),
    Row(
        24,
        "grader",
        GRD,
        '        + f"{outcomes.xfailed}/{outcomes.xpassed}/{outcomes.errors}/"',
        '        + f"{outcomes.xpassed}/{outcomes.xfailed}/{outcomes.errors}/"',
        [
            f"{T_GRD}::test_grade_run_diverge_on_xfailed_alone",
            f"{T_GRD}::test_grade_run_diverge_on_xpassed_alone",
        ],
        "tally printed out of order against its own legend",
    ),
    Row(
        25,
        "grader",
        GRD,
        "      only field either side publishes that can tell the two apart.\n"
        + '    """\n'
        + "    problems: list[str] = []\n"
        + "    only_pytest = sorted(set(pytest_result.ids) - set(v2_result.ids))\n"
        + "    only_v2 = sorted(set(v2_result.ids) - set(pytest_result.ids))\n"
        + "    if only_pytest:\n"
        + '        problems.append(f"missing from v2: {only_pytest}")\n'
        + "    if only_v2:\n"
        + '        problems.append(f"extra in v2: {only_v2}")\n',
        "      only field either side publishes that can tell the two apart.\n"
        + '    """\n'
        + "    problems: list[str] = []\n",
        [f"{T_GRD}::test_grade_run_reports_both_set_diff_and_position"],
        "set difference no longer reported -- a missing test is unreadable",
    ),
    # ------------------------------------------------------------------------ the CLI
    Row(
        26,
        "cli",
        MAIN,
        "        pytest_result = run_pytest_fn(case_dir, case_args)\n"
        + "        v2_result = run_rustest_fn(case_dir, case_args)\n"
        + "        return grade_run_case(",
        "        pytest_result = run_pytest_fn(case_dir, case_args)\n"
        + "        v2_result = run_rustest_fn(case_dir, [])\n"
        + "        return grade_run_case(",
        [f"{T_MAIN}::test_grade_one_run_passes_case_args_to_both_runners"],
        "case args dropped for the v2 side -- two different questions compared",
    ),
    Row(
        27,
        "cli",
        MAIN,
        "        ledger: Path = RUN_WAIVERS",
        "        ledger: Path = WAIVERS",
        [f"{T_MAIN}::test_main_run_mode_grades_the_case_the_old_engine_silently_passed"],
        "--run graded against the V1 ledger",
    ),
    Row(
        28,
        "cli",
        MAIN,
        "        grade_one: Callable[[Path, str, dict[str, str]], CaseResult] = _grade_one_run",
        "        grade_one: Callable[[Path, str, dict[str, str]], CaseResult] = _grade_one",
        [f"{T_MAIN}::test_main_run_mode_grades_the_case_the_old_engine_silently_passed"],
        "--run dispatches the V1 runners",
    ),
    Row(
        29,
        "cli",
        MAIN,
        "    mode = parser.add_mutually_exclusive_group()",
        "    mode = parser.add_argument_group()",
        [f"{T_MAIN}::test_main_rejects_both_v2_modes_at_once"],
        "both v2 modes accepted at once",
    ),
    # ------------------------------------------- deselected (the gate-review false green)
    Row(
        31,
        "deselected",
        RUN,
        r'_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed|deselected)")',
        r'_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed)")',
        [
            f"{T_RUN}::test_parse_pytest_summary_counts_deselected",
            f"{T_RUN}::test_full_run_runners_pass_case_args_through[run_pytest_full]",
        ],
        "deselected token dropped on the PYTEST side -- count reads 0",
    ),
    Row(
        32,
        "deselected",
        RUN,
        '            counts["deselected"] = found.get("deselected", 0)',
        '            counts["deselected"] = found.get("skipped", 0)',
        [f"{T_RUN}::test_parse_pytest_summary_counts_deselected"],
        "deselected read off the WRONG token (skipped) -- the bucket it resembles",
    ),
    Row(
        33,
        "deselected",
        RUN,
        '            deselected=summary["deselected"],',
        "            deselected=0,",
        [f"{T_RUN}::test_full_run_runners_pass_case_args_through[run_rustest_run]"],
        "deselected never read off the v2 report -- count reads 0",
    ),
    Row(
        34,
        "deselected",
        GRD,
        '        + f"{outcomes.xfailed}/{outcomes.xpassed}/{outcomes.errors}/"\n'
        + '        + f"{outcomes.deselected}"',
        '        + f"{outcomes.xfailed}/{outcomes.xpassed}/{outcomes.errors}"',
        [f"{T_GRD}::test_grade_run_diverge_on_a_lost_deselected_sibling"],
        "deselected dropped from the printed tally -- the divergence is unreadable",
    ),
    Row(
        35,
        "deselected",
        RUN,
        "    passed: int\n"
        + "    failed: int\n"
        + "    skipped: int\n"
        + "    xfailed: int\n"
        + "    xpassed: int\n"
        + "    errors: int\n"
        + "    deselected: int",
        "    passed: int\n"
        + "    failed: int\n"
        + "    skipped: int\n"
        + "    xfailed: int\n"
        + "    xpassed: int\n"
        + "    errors: int\n"
        + "    deselected: int = 0\n"
        + "\n"
        + "    def __eq__(self, other: object) -> bool:\n"
        + "        if not isinstance(other, RunOutcomes):\n"
        + "            return NotImplemented\n"
        + "        return (\n"
        + "            self.passed,\n"
        + "            self.failed,\n"
        + "            self.skipped,\n"
        + "            self.xfailed,\n"
        + "            self.xpassed,\n"
        + "            self.errors,\n"
        + "        ) == (\n"
        + "            other.passed,\n"
        + "            other.failed,\n"
        + "            other.skipped,\n"
        + "            other.xfailed,\n"
        + "            other.xpassed,\n"
        + "            other.errors,\n"
        + "        )",
        [f"{T_GRD}::test_grade_run_diverge_on_a_lost_deselected_sibling"],
        "THE ORIGINAL FALSE GREEN: tally compared on the six outcomes, deselected ignored",
    ),
    # --------------------------------------------- the nothing-ran rule's keying pass
    Row(
        36,
        "interrupted",
        RUN,
        "    interrupted = collect.returncode == _PYTEST_EXIT_INTERRUPTED",
        "    interrupted = run.returncode == _PYTEST_EXIT_INTERRUPTED",
        [f"{T_RUN}::test_run_pytest_full_keeps_its_ids_when_a_test_calls_pytest_exit"],
        "nothing-ran rule rekeyed on the RUN pass -- pytest.exit() empties the ids",
    ),
    Row(
        30,
        "ledger",
        LEDGER,
        "[cases]\n",
        '[cases]\n"marks/xfail" = "a waiver nobody adjudicated"\n',
        [
            f"{T_MAIN}::test_run_ledger_is_empty",
            f"{T_MAIN}::test_main_run_mode_grades_the_case_the_old_engine_silently_passed",
        ],
        "an unadjudicated entry appears in the empty run ledger",
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
        print(f"[{row.id:>3}] {row.area:<14} {verdict:<18} {elapsed:5.1f}s  {row.note}", flush=True)

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
