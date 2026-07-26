"""Run pytest and rustest as subprocesses over a corpus case directory."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ids import normalize_pytest_nodeid, normalize_rustest_id

_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors)")


@dataclass(frozen=True)
class Outcomes:
    passed: int
    failed: int
    skipped: int
    errors: int
    exit_code: int
    collection_error: bool


@dataclass(frozen=True)
class RunResult:
    ids: set[str]
    outcomes: Outcomes


def parse_pytest_collect(text: str) -> set[str]:
    """Extract nodeids from ``pytest --collect-only -q`` output."""
    ids: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "::" not in line or line.startswith(("=", "warning", "ERROR")):
            continue
        ids.add(line)
    return ids


def parse_pytest_summary(text: str, exit_code: int) -> Outcomes:
    """Extract pass/fail/skip/error counts from a pytest terminal summary line."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for line in reversed(text.splitlines()):
        found: dict[str, int] = {}
        for match in _SUMMARY_RE.finditer(line):
            number: str = match.group(1)
            kind: str = match.group(2)
            found[kind] = int(number)
        if found:
            counts["passed"] = found.get("passed", 0)
            counts["failed"] = found.get("failed", 0)
            counts["skipped"] = found.get("skipped", 0)
            # pytest writes "1 error" but "2 errors"; both mean the same bucket.
            counts["errors"] = found.get("error", 0) + found.get("errors", 0)
            break
    return Outcomes(
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        errors=counts["errors"],
        exit_code=exit_code,
        collection_error=exit_code == 2,
    )


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)


def _check_pytest_exit(proc: subprocess.CompletedProcess[str], phase: str) -> None:
    """Raise on a pytest *harness* fault, leaving real test outcomes alone.

    pytest exit codes 0 (all passed), 1 (tests failed) and 2 (collection error /
    interrupted) are legitimate case outcomes the corpus grades on. Codes >= 3
    mean pytest itself could not do its job -- 3 internal error, 4 usage error
    (e.g. a bad rootdir), 5 no tests collected -- and silently parsing an empty
    summary out of those would fabricate a 0/0/0/0 result. Raising routes them to
    ``_grade_one``'s harness-error channel instead.
    """
    if proc.returncode >= 3:
        raise RuntimeError(f"pytest {phase} failed (exit {proc.returncode}): {proc.stderr[-500:]}")


def run_pytest(case_dir: Path, args: list[str]) -> RunResult:
    """Collect and run *case_dir* with real pytest, returning normalized results.

    The case runs under pure pytest defaults: an empty ini file is forced with
    ``-c`` and the rootdir is pinned to *case_dir*, so pytest never walks up and
    adopts a surrounding project's ``[tool.pytest.ini_options]`` (this repo's own
    ``pyproject.toml`` would otherwise apply to every corpus case).

    *case_dir* is resolved first: ``--rootdir`` is interpreted relative to
    pytest's own cwd, so a relative case directory would point pytest at a
    nonexistent rootdir and abort with a usage error (exit 4).
    """
    case_dir = case_dir.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        empty_ini = Path(tmp) / "pytest.ini"
        empty_ini.write_text("[pytest]\n", encoding="utf-8")
        base = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(empty_ini),
            f"--rootdir={case_dir}",
        ]
        collect = _run([*base, "--collect-only", "-q", *args], case_dir)
        _check_pytest_exit(collect, "collect")
        raw_ids = parse_pytest_collect(collect.stdout)
        run = _run([*base, "-q", "--tb=no", *args], case_dir)
        _check_pytest_exit(run, "run")
    outcomes = parse_pytest_summary(run.stdout, run.returncode)
    return RunResult(
        ids={normalize_pytest_nodeid(i) for i in raw_ids},
        outcomes=outcomes,
    )


def run_rustest(case_dir: Path, args: list[str]) -> RunResult:
    """Run *case_dir* with real rustest, returning normalized results.

    *case_dir* is resolved so the subprocess cwd and the ID normalization base
    are absolute, matching ``run_pytest``.

    A missing report file means rustest died before it could write one (bad
    argv, crash, import-time abort). That is a harness fault, not a case
    outcome, so it raises rather than returning a fabricated all-zeros result
    that would silently grade as a divergence with no explanation.
    """
    case_dir = case_dir.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        # TODO(phase1): --pytest-compat is deleted in v2 (compat-by-default); update this invocation.
        cmd = [
            sys.executable,
            "-m",
            "rustest",
            ".",
            "--pytest-compat",
            "--color",
            "never",
            "--report-json",
            str(report_path),
            *args,
        ]
        proc = _run(cmd, case_dir)
        if not report_path.exists():
            raise RuntimeError(
                f"rustest wrote no report (exit {proc.returncode}): {proc.stderr[-500:]}"
            )
        data: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    summary: dict[str, int] = data["summary"]
    tests: list[dict[str, Any]] = data["tests"]
    return RunResult(
        ids={normalize_rustest_id(str(test["id"]), case_dir) for test in tests},
        outcomes=Outcomes(
            passed=summary["passed"],
            failed=summary["failed"],
            skipped=summary["skipped"],
            errors=sum(1 for test in tests if test.get("status") == "error"),
            exit_code=proc.returncode,
            collection_error=bool(data["collection_errors"]),
        ),
    )
