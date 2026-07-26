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

# A pytest nodeid: a path segment, one or more "::"-separated name segments, and
# an optional trailing "[...]" parametrize suffix (which may itself contain
# colons or nested brackets, e.g. "test_x.py::test_f[a:b]" or "[a[b]]"). No
# segment before the trailing suffix may contain a bare colon, and the whole
# thing must start at column 0 -- both are true of every real nodeid pytest
# prints and false of most non-nodeid lines (traceback frames, source excerpts,
# "E   ..." assertion text are indented, and most contain a single ":" ahead of
# any incidental "::", e.g. "AssertionError: ...::..."). This is a per-line
# filter, not a complete guard on its own: an "E   ..." line that verbatim
# echoes offending source containing a slice (e.g. "E       x = data[::2") has
# no such preceding colon and DOES match this shape despite sitting at column
# 0. See parse_pytest_collect, which stops scanning before any such line is
# ever reached.
_NODEID_RE = re.compile(r"^[^\s:][^:\n]*(::[^\s:][^:\n]*)+(\[[^\n]*\])?$")

# A pytest -q --collect-only report is structurally two parts: every collected
# nodeid, printed contiguously first, followed -- only if collection hit
# trouble -- by an "=== TITLE ===" section header (ERRORS, short test summary
# info, warnings summary) and its body. Verified against real pytest (see the
# probe in the Task 7 report): even when the broken file sorts alphabetically
# *before* a valid sibling, pytest still front-loads every successfully
# collected nodeid from every file before any error block -- collection
# errors never interleave with the nodeid list. So stopping at the first
# boundary line loses no real ids and is a structural guard against any
# "E   ..." echoed-source line, not just the ones _NODEID_RE's shape happens
# to reject. Two boundary shapes are recognized: the "=== TITLE ==="
# section-header line itself (what actually fires in every real case probed),
# and a bare "E " prefix as a defensive backstop in case an "E   ..." line
# were ever reached without a preceding recognized header.
_SECTION_BOUNDARY_RE = re.compile(r"^=+ .+ =+$")


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
    """Extract nodeids from ``pytest --collect-only -q`` output.

    Stops at the first section-boundary line (an "=== TITLE ===" header, or a
    defensive fallback on a bare "E " prefix) -- see _SECTION_BOUNDARY_RE --
    since real nodeids are only ever printed before any such boundary.
    Matched against the *raw* line (only trailing whitespace stripped): real
    nodeids are always flush at column 0, while every other pre-boundary line
    is indented by pytest. Stripping leading whitespace before matching (the
    previous heuristic did) throws that signal away and lets an indented line
    that happens to contain a literal "::" -- e.g. a quoted path or
    Rust-style module reference inside an assertion message -- read as a
    phantom test id.

    The boundary check is the load-bearing guard: an "E   ..." line that
    echoes a SyntaxError's offending source verbatim can contain a slice
    (e.g. "E       x = data[::2") and would otherwise match _NODEID_RE's
    per-line shape despite sitting at column 0, since a slice's "::" has no
    preceding bare colon to disqualify it.
    """
    ids: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _SECTION_BOUNDARY_RE.match(line) or line.startswith("E "):
            break
        if _NODEID_RE.match(line):
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
    interrupted) are legitimate case outcomes the corpus grades on. So is 5 (no
    tests collected, e.g. ``-m nosuchmark`` deselecting everything): it is a real,
    comparable outcome under the v2 exit-code contract (spec: "Contracts are
    pytest's: exit codes 0-5"), not a harness fault -- rustest exiting 0 for the
    same invocation is exactly the kind of divergence the corpus exists to catch.
    Codes 3 (internal error) and 4 (usage error, e.g. a bad rootdir) mean pytest
    itself could not do its job, and silently parsing an empty summary out of
    those would fabricate a 0/0/0/0 result. Raising routes them to
    ``_grade_one``'s harness-error channel instead.
    """
    if proc.returncode >= 3 and proc.returncode != 5:
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
