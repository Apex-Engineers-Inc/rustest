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
    """Extract pass/fail/skip counts from a pytest terminal summary line."""
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for line in reversed(text.splitlines()):
        found: dict[str, int] = {}
        for match in _SUMMARY_RE.finditer(line):
            number: str = match.group(1)
            kind: str = match.group(2)
            found[kind] = int(number)
        if found:
            for key in counts:
                counts[key] = found.get(key, 0)
            break
    return Outcomes(
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        exit_code=exit_code,
        collection_error=exit_code == 2,
    )


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)


def run_pytest(case_dir: Path, args: list[str]) -> RunResult:
    """Collect and run *case_dir* with real pytest, returning normalized results."""
    base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]
    collect = _run([*base, "--collect-only", "-q", *args], case_dir)
    raw_ids = parse_pytest_collect(collect.stdout)
    run = _run([*base, "-q", "--tb=no", *args], case_dir)
    outcomes = parse_pytest_summary(run.stdout, run.returncode)
    return RunResult(
        ids={normalize_pytest_nodeid(i) for i in raw_ids},
        outcomes=outcomes,
    )


def run_rustest(case_dir: Path, args: list[str]) -> RunResult:
    """Run *case_dir* with real rustest, returning normalized results."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
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
            return RunResult(
                ids=set(),
                outcomes=Outcomes(0, 0, 0, proc.returncode, collection_error=True),
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
            exit_code=proc.returncode,
            collection_error=bool(data["collection_errors"]),
        ),
    )
