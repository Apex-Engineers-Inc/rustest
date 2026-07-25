# Phase 0: Conformance Corpus & Benchmark Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the differential conformance harness (pytest vs rustest), seed corpus, waiver system, and benchmark suite that every later v2 phase is graded against.

**Architecture:** A self-contained `conformance/` package runs each corpus case through real pytest and real rustest as subprocesses, diffs collected test IDs and outcome counts, applies waivers, and reports MATCH/DIVERGE/WAIVED. rustest gains a `--report-json` flag (Python-only change) as its machine-readable interface. A generator-based benchmark suite produces the three canonical numbers: collection time, per-test overhead, full-run time.

**Tech Stack:** Python (no Rust changes in this phase), pytest as **dev-dependency only**, tomllib (stdlib), uv + poe tasks, GitHub Actions. The rustest package keeps its 3.10 floor; the conformance harness itself requires Python >= 3.11 (tomllib) and its CI job pins accordingly.

## Global Constraints

- Development machine is Windows: all paths via `pathlib`; IDs normalized to posix form; subprocesses via `sys.executable`, never bare `python`.
- pytest must never become a runtime dependency of rustest — it is imported only inside `conformance/` and CI.
- All new Python must pass `uv run ruff format --check python conformance`, `uv run ruff check python conformance`, `uv run basedpyright python conformance` (add `conformance` to the checked paths in `pyproject.toml` tool configs as part of Task 2).
- Run all commands from the repo root `C:\Users\JeffreyMBloss\local-repos\rustest`.
- Rebuild not required: Phase 0 touches no Rust. If `rustest` isn't importable run `uv sync --all-extras && uv run maturin develop` once.
- Commit after every task on branch `v2/phase0-conformance`.

---

### Task 1: `--report-json` flag for rustest v1

**Files:**
- Create: `python/rustest/json_report.py`
- Modify: `python/rustest/cli.py` (add argument + write call in `main`)
- Test: `python/tests/test_json_report.py`

**Interfaces:**
- Consumes: `RunReport`, `TestResult`, `CollectionError` from `python/rustest/reporting.py` (fields: `total, passed, failed, skipped, duration, results, collection_errors`; result fields `name, path, status, duration, message, stdout, stderr`).
- Produces: `write_json_report(report: RunReport, path: str | os.PathLike[str]) -> None` writing schema v1: `{"version": 1, "summary": {"total": int, "passed": int, "failed": int, "skipped": int, "duration": float}, "tests": [{"id": "<path>::<name>", "name": str, "path": str, "status": str, "duration": float, "message": str|null}], "collection_errors": [{"path": str, "message": str}]}`. CLI flag `--report-json PATH`. Task 2's runner depends on exactly this schema.

- [ ] **Step 1: Write the failing tests**

```python
# python/tests/test_json_report.py
"""Tests for the machine-readable JSON report."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rustest.json_report import write_json_report
from rustest.reporting import CollectionError, RunReport, TestResult


def _report() -> RunReport:
    return RunReport(
        total=2,
        passed=1,
        failed=1,
        skipped=0,
        duration=0.5,
        results=(
            TestResult(
                name="test_ok",
                path="tests/test_a.py",
                status="passed",
                duration=0.1,
                message=None,
                stdout=None,
                stderr=None,
            ),
            TestResult(
                name="test_bad[1]",
                path="tests/test_a.py",
                status="failed",
                duration=0.2,
                message="boom",
                stdout=None,
                stderr=None,
            ),
        ),
        collection_errors=(CollectionError(path="tests/test_b.py", message="SyntaxError"),),
    )


def test_write_json_report_schema(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    write_json_report(_report(), out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 0,
        "duration": 0.5,
    }
    assert data["tests"][0]["id"] == "tests/test_a.py::test_ok"
    assert data["tests"][1]["id"] == "tests/test_a.py::test_bad[1]"
    assert data["tests"][1]["message"] == "boom"
    assert data["collection_errors"] == [{"path": "tests/test_b.py", "message": "SyntaxError"}]


def test_cli_writes_report_json(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_mini.py").write_text(
        "def test_one():\n    assert True\n\n\ndef test_two():\n    assert False\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "rustest", str(suite), "--report-json", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["passed"] == 1
    assert data["summary"]["failed"] == 1
    ids = {t["id"] for t in data["tests"]}
    assert any(i.endswith("test_mini.py::test_one") for i in ids)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest python/tests/test_json_report.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'rustest.json_report'`

- [ ] **Step 3: Implement**

```python
# python/rustest/json_report.py
"""Machine-readable JSON report (schema v1) for tooling and conformance."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .reporting import RunReport

SCHEMA_VERSION = 1


def write_json_report(report: RunReport, path: str | os.PathLike[str]) -> None:
    """Serialize a RunReport to the schema-v1 JSON document at *path*."""
    payload = {
        "version": SCHEMA_VERSION,
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "duration": report.duration,
        },
        "tests": [
            {
                "id": f"{result.path}::{result.name}",
                "name": result.name,
                "path": result.path,
                "status": result.status,
                "duration": result.duration,
                "message": result.message,
            }
            for result in report.results
        ],
        "collection_errors": [
            {"path": error.path, "message": error.message}
            for error in report.collection_errors
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
```

In `python/rustest/cli.py`, inside `build_parser()` after the `--pytest-compat` argument:

```python
    _ = parser.add_argument(
        "--report-json",
        dest="report_json",
        metavar="PATH",
        help="Write a machine-readable JSON report (schema v1) to PATH.",
    )
```

and add `report_json=None` to the `parser.set_defaults(...)` call. In `main()`, immediately after `report = run(...)`:

```python
    if args.report_json:
        from .json_report import write_json_report

        write_json_report(report, args.report_json)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest python/tests/test_json_report.py -v`
Expected: 2 PASS. Then run gates: `uv run ruff format python && uv run ruff check python && uv run basedpyright python`.

- [ ] **Step 5: Commit**

```bash
git add python/rustest/json_report.py python/rustest/cli.py python/tests/test_json_report.py
git commit -m "feat: add --report-json machine-readable report (schema v1)"
```

---

### Task 2: Harness ID normalization and subprocess runners

**Files:**
- Create: `conformance/__init__.py` (empty), `conformance/harness/__init__.py` (empty), `conformance/harness/ids.py`, `conformance/harness/runners.py`, `conformance/tests/__init__.py` (empty)
- Modify: `pyproject.toml` — add `conformance` to ruff/basedpyright include paths (mirror how `python` is listed)
- Test: `conformance/tests/test_ids.py`, `conformance/tests/test_runners.py`

**Interfaces:**
- Consumes: Task 1's JSON schema; `pytest --collect-only -q` and `-q --tb=no -p no:cacheprovider` output formats.
- Produces (Task 3 depends on these exact names):
  - `ids.normalize_pytest_nodeid(nodeid: str) -> str` — posix path, class segments dropped: `tests\test_a.py::TestX::test_y[1-2]` → `tests/test_a.py::test_y[1-2]`
  - `ids.normalize_rustest_id(test_id: str, case_dir: Path) -> str` — path made relative to `case_dir`, posix, class segments dropped
  - `runners.Outcomes` dataclass: `passed: int, failed: int, skipped: int, errors: int, exit_code: int, collection_error: bool` (errors added during Task 2 review: pytest error outcomes parsed from summary; rustest counts report tests with status "error", structurally 0 in v1)
  - `runners.RunResult` dataclass: `ids: set[str], outcomes: Outcomes`
  - `runners.run_pytest(case_dir: Path, args: list[str]) -> RunResult`
  - `runners.run_rustest(case_dir: Path, args: list[str]) -> RunResult`
  - Pure parse helpers: `parse_pytest_collect(text: str) -> set[str]`, `parse_pytest_summary(text: str, exit_code: int) -> Outcomes`

- [ ] **Step 1: Write the failing tests**

```python
# conformance/tests/test_ids.py
from __future__ import annotations

from pathlib import Path

from conformance.harness.ids import normalize_pytest_nodeid, normalize_rustest_id


def test_normalize_pytest_nodeid_drops_class_and_posixifies() -> None:
    assert (
        normalize_pytest_nodeid("tests\\test_a.py::TestX::test_y[1-2]")
        == "tests/test_a.py::test_y[1-2]"
    )
    assert normalize_pytest_nodeid("test_a.py::test_y") == "test_a.py::test_y"


def test_normalize_rustest_id_relativizes(tmp_path: Path) -> None:
    abs_id = str(tmp_path / "test_a.py") + "::test_y[1]"
    assert normalize_rustest_id(abs_id, tmp_path) == "test_a.py::test_y[1]"
```

```python
# conformance/tests/test_runners.py
from __future__ import annotations

import textwrap
from pathlib import Path

from conformance.harness.runners import (
    parse_pytest_collect,
    parse_pytest_summary,
    run_pytest,
    run_rustest,
)

COLLECT_OUTPUT = textwrap.dedent(
    """\
    test_a.py::test_one
    test_a.py::TestBox::test_two[x]

    2 tests collected in 0.01s
    """
)


def test_parse_pytest_collect() -> None:
    assert parse_pytest_collect(COLLECT_OUTPUT) == {
        "test_a.py::test_one",
        "test_a.py::TestBox::test_two[x]",
    }


def test_parse_pytest_summary() -> None:
    out = parse_pytest_summary("1 failed, 2 passed, 1 skipped in 0.05s\n", exit_code=1)
    assert (out.passed, out.failed, out.skipped) == (2, 1, 1)
    assert out.exit_code == 1
    assert out.collection_error is False
    err = parse_pytest_summary("no tests ran in 0.01s\n", exit_code=2)
    assert err.collection_error is True


def _write_mini_suite(root: Path) -> None:
    (root / "test_mini.py").write_text(
        "def test_one():\n    assert True\n\n\ndef test_two():\n    assert False\n",
        encoding="utf-8",
    )


def test_run_pytest_integration(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)
    result = run_pytest(tmp_path, [])
    assert result.ids == {"test_mini.py::test_one", "test_mini.py::test_two"}
    assert (result.outcomes.passed, result.outcomes.failed) == (1, 1)


def test_run_rustest_integration(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)
    result = run_rustest(tmp_path, [])
    assert result.ids == {"test_mini.py::test_one", "test_mini.py::test_two"}
    assert (result.outcomes.passed, result.outcomes.failed) == (1, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest conformance/tests -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'conformance.harness.ids'`

- [ ] **Step 3: Implement**

```python
# conformance/harness/ids.py
"""Test-ID normalization so pytest and rustest IDs are comparable."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def _posix(path_part: str) -> str:
    return str(PurePosixPath(PureWindowsPath(path_part).as_posix()))


def normalize_pytest_nodeid(nodeid: str) -> str:
    """Posixify the path segment and drop intermediate class segments.

    v1 rustest report IDs carry only path::name, so class segments are removed
    from both sides for comparison. v2's report will restore full fidelity.
    """
    parts = nodeid.split("::")
    return f"{_posix(parts[0])}::{parts[-1]}" if len(parts) > 1 else _posix(parts[0])


def normalize_rustest_id(test_id: str, case_dir: Path) -> str:
    path_part, sep, name = test_id.partition("::")
    candidate = Path(path_part)
    if candidate.is_absolute():
        try:
            path_part = str(candidate.relative_to(case_dir))
        except ValueError:
            pass
    normalized_path = _posix(path_part)
    if not sep:
        return normalized_path
    return f"{normalized_path}::{name.split('::')[-1]}"
```

```python
# conformance/harness/runners.py
"""Run pytest and rustest as subprocesses over a corpus case directory."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or "::" not in line or line.startswith(("=", "warning", "ERROR")):
            continue
        ids.add(line)
    return ids


def parse_pytest_summary(text: str, exit_code: int) -> Outcomes:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for line in reversed(text.splitlines()):
        found = dict((kind, int(n)) for n, kind in _SUMMARY_RE.findall(line))
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
        data = json.loads(report_path.read_text(encoding="utf-8"))
    summary = data["summary"]
    return RunResult(
        ids={normalize_rustest_id(t["id"], case_dir) for t in data["tests"]},
        outcomes=Outcomes(
            passed=summary["passed"],
            failed=summary["failed"],
            skipped=summary["skipped"],
            exit_code=proc.returncode,
            collection_error=bool(data["collection_errors"]),
        ),
    )
```

In `pyproject.toml`, extend the ruff and basedpyright path lists that currently name `python` to also include `conformance`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest conformance/tests -v`
Expected: 6 PASS (2 ids, 2 parse, 2 integration). Run the format/lint/type gates on `conformance`.

- [ ] **Step 5: Commit**

```bash
git add conformance pyproject.toml
git commit -m "feat(conformance): differential runners and ID normalization"
```

---

### Task 3: Grader, waivers, case config, and `python -m conformance` CLI

**Files:**
- Create: `conformance/harness/grade.py`, `conformance/__main__.py`, `conformance/waivers.toml`, `conformance/corpus/.gitkeep`
- Test: `conformance/tests/test_grade.py`

**Interfaces:**
- Consumes: `RunResult`, `Outcomes` from Task 2.
- Produces:
  - `grade.CaseResult` dataclass: `name: str, status: str  # "MATCH" | "DIVERGE" | "WAIVED", detail: str`
  - `grade.grade_case(name: str, pytest_result: RunResult, rustest_result: RunResult, waivers: dict[str, str]) -> CaseResult`
  - `grade.load_waivers(path: Path) -> dict[str, str]` reading `waivers.toml` table `[cases]` mapping case name → reason
  - `grade.load_case_args(case_dir: Path) -> list[str]` reading optional `case.toml` key `case.args`
  - CLI: `python -m conformance [--only PREFIX]` — iterates `conformance/corpus/*/*/`, prints one line per case plus summary, exit 1 iff any unwaived DIVERGE.
- Case layout contract (Tasks 4–6 depend on it): each case is a directory `conformance/corpus/<area>/<case-name>/` containing `test_*.py` files, optional `conftest.py`, optional `case.toml`.

- [ ] **Step 1: Write the failing tests**

```python
# conformance/tests/test_grade.py
from __future__ import annotations

from pathlib import Path

from conformance.harness.grade import grade_case, load_case_args, load_waivers
from conformance.harness.runners import Outcomes, RunResult


def _result(ids: set[str], passed: int = 1, failed: int = 0) -> RunResult:
    return RunResult(
        ids=ids,
        outcomes=Outcomes(passed, failed, 0, 0, 1 if failed else 0, False),
    )


def test_grade_match() -> None:
    a = _result({"test_a.py::test_x"})
    assert grade_case("area/case", a, a, {}).status == "MATCH"


def test_grade_diverge_on_ids() -> None:
    got = grade_case(
        "area/case",
        _result({"test_a.py::test_x", "test_a.py::testfoo"}),
        _result({"test_a.py::test_x"}),
        {},
    )
    assert got.status == "DIVERGE"
    assert "testfoo" in got.detail


def test_grade_waived() -> None:
    got = grade_case(
        "area/case",
        _result({"test_a.py::test_x"}),
        _result(set(), passed=0),
        {"area/case": "known v1 gap"},
    )
    assert got.status == "WAIVED"
    assert "known v1 gap" in got.detail


def test_load_waivers_and_case_args(tmp_path: Path) -> None:
    (tmp_path / "waivers.toml").write_text(
        '[cases]\n"area/case" = "reason here"\n', encoding="utf-8"
    )
    assert load_waivers(tmp_path / "waivers.toml") == {"area/case": "reason here"}
    case = tmp_path / "case"
    case.mkdir()
    assert load_case_args(case) == []
    (case / "case.toml").write_text('[case]\nargs = ["-m", "smoke"]\n', encoding="utf-8")
    assert load_case_args(case) == ["-m", "smoke"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest conformance/tests/test_grade.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'conformance.harness.grade'`

- [ ] **Step 3: Implement**

```python
# conformance/harness/grade.py
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
        problems.append(
            f"outcomes pytest={po.passed}/{po.failed}/{po.skipped}/{po.errors} "
            f"rustest={ro.passed}/{ro.failed}/{ro.skipped}/{ro.errors}"
        )
    if po.exit_code != ro.exit_code:
        problems.append(f"exit codes pytest={po.exit_code} rustest={ro.exit_code}")
    if not problems:
        return CaseResult(name, "MATCH", "")
    if name in waivers:
        return CaseResult(name, "WAIVED", f"{waivers[name]} :: {'; '.join(problems)}")
    return CaseResult(name, "DIVERGE", "; ".join(problems))
```

```python
# conformance/__main__.py
"""Conformance CLI: python -m conformance [--only PREFIX]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .harness.grade import grade_case, load_case_args, load_waivers
from .harness.runners import run_pytest, run_rustest

ROOT = Path(__file__).parent


def main() -> int:
    parser = argparse.ArgumentParser(prog="conformance")
    parser.add_argument("--only", default="", help="Only run cases whose name starts with PREFIX")
    args = parser.parse_args()

    waivers = load_waivers(ROOT / "waivers.toml")
    corpus = ROOT / "corpus"
    cases = sorted(
        d for d in corpus.glob("*/*/") if any(d.glob("test_*.py")) or (d / "case.toml").exists()
    )
    results = []
    for case_dir in cases:
        name = f"{case_dir.parent.name}/{case_dir.name}"
        if not name.startswith(args.only):
            continue
        case_args = load_case_args(case_dir)
        result = grade_case(
            name, run_pytest(case_dir, case_args), run_rustest(case_dir, case_args), waivers
        )
        results.append(result)
        flag = {"MATCH": "ok", "WAIVED": "~~", "DIVERGE": "XX"}[result.status]
        print(f"[{flag}] {result.name}" + (f"  ({result.detail})" if result.detail else ""))

    diverged = [r for r in results if r.status == "DIVERGE"]
    print(
        f"\n{len(results)} cases: "
        f"{sum(r.status == 'MATCH' for r in results)} match, "
        f"{sum(r.status == 'WAIVED' for r in results)} waived, "
        f"{len(diverged)} diverged"
    )
    return 1 if diverged else 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `conformance/waivers.toml` containing only the header comment and empty table:

```toml
# Documented divergences between pytest and rustest v1.
# Every entry MUST have a reason. v2 phase gates shrink this file.
[cases]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest conformance/tests -v` → all PASS. Then `uv run python -m conformance` → prints `0 cases: 0 match, 0 waived, 0 diverged`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add conformance
git commit -m "feat(conformance): grader, waivers, and CLI"
```

---

### Task 4: Corpus — collection semantics (6 cases)

**Files:** create under `conformance/corpus/collection/`:
- `naming-testfoo/test_naming.py`, `naming-underscore/test_underscore.py`, `class-collection/test_classes.py`, `nested-function/test_nested.py`, `conftest-visibility/conftest.py` + `conftest-visibility/test_uses_fixture.py`, `unittest-basic/test_unittest.py`
- Modify: `conformance/waivers.toml` (seed known v1 divergences found by running)

**Interfaces:** Consumes the case layout contract from Task 3. Produces corpus data only.

- [ ] **Step 1: Create the case files**

```python
# conformance/corpus/collection/naming-testfoo/test_naming.py
def test_proper():
    assert True


def testfoo():  # pytest does NOT collect this (python_functions = "test_*")
    assert True
```

```python
# conformance/corpus/collection/naming-underscore/test_underscore.py
def _test_hidden():
    raise AssertionError("must not be collected")


def test_visible():
    assert True
```

```python
# conformance/corpus/collection/class-collection/test_classes.py
class TestBox:
    def test_method(self):
        assert True


class Helper:  # not collected: name doesn't match Test*
    def test_ignored(self):
        raise AssertionError("must not run")


class TestWithInit:  # pytest skips classes with __init__
    def __init__(self):
        pass

    def test_ignored(self):
        raise AssertionError("must not run")
```

```python
# conformance/corpus/collection/nested-function/test_nested.py
def test_outer():
    def test_inner():  # not collected: nested
        raise AssertionError("must not run")

    assert True
```

```python
# conformance/corpus/collection/conftest-visibility/conftest.py
import pytest


@pytest.fixture
def shared_value():
    return 42
```

```python
# conformance/corpus/collection/conftest-visibility/test_uses_fixture.py
def test_conftest_fixture(shared_value):
    assert shared_value == 42
```

```python
# conformance/corpus/collection/unittest-basic/test_unittest.py
import unittest


class TestLegacy(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(1 + 1, 2)

    def test_failure(self):
        self.assertEqual(1, 2)
```

- [ ] **Step 2: Run the conformance CLI**

Run: `uv run python -m conformance --only collection/`
Expected: each case prints. Any `XX` line is a real v1 divergence (naming-testfoo is expected to diverge per the audit).

- [ ] **Step 3: Waive genuine v1 gaps with reasons**

For every DIVERGE whose detail matches a known v1 audit finding, add to `conformance/waivers.toml`, e.g.:

```toml
"collection/naming-testfoo" = "v1 uses starts_with('test'); pytest requires test_*. Fixed by v2 config subsystem (spec: naming rules)."
```

Re-run until exit code is 0. A DIVERGE that is NOT explainable by the audit is a new bug: record it in the waiver with prefix `NEW-BUG:` so Phase 1 picks it up.

- [ ] **Step 4: Commit**

```bash
git add conformance/corpus/collection conformance/waivers.toml
git commit -m "test(conformance): collection semantics corpus"
```

---

### Task 5: Corpus — fixture semantics (6 cases)

**Files:** create under `conformance/corpus/fixtures/`:
- `scope-function/test_scope_function.py`, `scope-module/test_scope_module.py`, `yield-teardown/test_teardown.py`, `autouse/conftest.py` + `autouse/test_autouse.py`, `override-nearest/conftest.py` + `override-nearest/test_override.py`, `parametrized-fixture/test_fixture_params.py`

**Interfaces:** corpus data only.

- [ ] **Step 1: Create the case files**

```python
# conformance/corpus/fixtures/scope-function/test_scope_function.py
import itertools

import pytest

counter = itertools.count()


@pytest.fixture
def fresh():
    return next(counter)


def test_first(fresh):
    assert fresh == 0


def test_second(fresh):
    assert fresh == 1  # function scope: new instance per test
```

```python
# conformance/corpus/fixtures/scope-module/test_scope_module.py
import itertools

import pytest

counter = itertools.count()


@pytest.fixture(scope="module")
def shared():
    return next(counter)


def test_first(shared):
    assert shared == 0


def test_second(shared):
    assert shared == 0  # module scope: same instance
```

```python
# conformance/corpus/fixtures/yield-teardown/test_teardown.py
import pytest

events = []


@pytest.fixture
def resource():
    events.append("setup")
    yield "value"
    events.append("teardown")


def test_uses_resource(resource):
    assert resource == "value"
    assert events == ["setup"]


def test_teardown_ran_after_previous_test():
    assert events == ["setup", "teardown"]
```

```python
# conformance/corpus/fixtures/autouse/conftest.py
import pytest

applied = []


@pytest.fixture(autouse=True)
def always():
    applied.append(1)
```

```python
# conformance/corpus/fixtures/autouse/test_autouse.py
from conftest import applied


def test_autouse_applied():
    assert len(applied) == 1
```

```python
# conformance/corpus/fixtures/override-nearest/conftest.py
import pytest


@pytest.fixture
def value():
    return "conftest"
```

```python
# conformance/corpus/fixtures/override-nearest/test_override.py
import pytest


@pytest.fixture
def value():
    return "module"  # module fixture shadows conftest fixture


def test_nearest_wins(value):
    assert value == "module"
```

```python
# conformance/corpus/fixtures/parametrized-fixture/test_fixture_params.py
import pytest


@pytest.fixture(params=[1, 2])
def number(request):
    return request.param


def test_number(number):
    assert number in (1, 2)  # collects as two tests
```

- [ ] **Step 2: Run, waive, verify exit 0**

Run: `uv run python -m conformance --only fixtures/`. `parametrized-fixture` is expected to DIVERGE (v1 compat mode does not support fixture params — documented in `compat/pytest.py`); waive it:

```toml
"fixtures/parametrized-fixture" = "v1 compat mode lacks @pytest.fixture(params=...) and request.param. Hard v2 requirement."
```

Waive any other DIVERGE with an audit-grounded or `NEW-BUG:` reason; re-run to exit 0.

- [ ] **Step 3: Commit**

```bash
git add conformance/corpus/fixtures conformance/waivers.toml
git commit -m "test(conformance): fixture semantics corpus"
```

---

### Task 6: Corpus — parametrize and marks (6 cases)

**Files:** create under `conformance/corpus/parametrize/` and `conformance/corpus/marks/`:
- `parametrize/basic-ids/test_basic.py`, `parametrize/explicit-ids/test_ids.py`, `parametrize/stacking/test_stacking.py`
- `marks/skip-and-skipif/test_skip.py`, `marks/xfail/test_xfail.py`, `marks/mark-filter/test_marks.py` + `marks/mark-filter/case.toml`

**Interfaces:** corpus data only; `mark-filter` exercises Task 3's `case.toml` args support.

- [ ] **Step 1: Create the case files**

```python
# conformance/corpus/parametrize/basic-ids/test_basic.py
import pytest


@pytest.mark.parametrize("value", [1, 2, 3])
def test_value(value):
    assert value > 0
```

```python
# conformance/corpus/parametrize/explicit-ids/test_ids.py
import pytest


@pytest.mark.parametrize("value", [1, 2], ids=["one", "two"])
def test_named(value):
    assert value in (1, 2)
```

```python
# conformance/corpus/parametrize/stacking/test_stacking.py
import pytest


@pytest.mark.parametrize("a", [1, 2])
@pytest.mark.parametrize("b", ["x", "y"])
def test_grid(a, b):
    assert (a, b)  # collects 4 cases: [x-1] [x-2] [y-1] [y-2]
```

```python
# conformance/corpus/marks/skip-and-skipif/test_skip.py
import pytest


@pytest.mark.skip(reason="always skipped")
def test_skipped():
    raise AssertionError("must not run")


@pytest.mark.skipif(1 + 1 == 2, reason="condition true")
def test_skipped_conditionally():
    raise AssertionError("must not run")


@pytest.mark.skipif(1 + 1 == 3, reason="condition false")
def test_runs():
    assert True
```

```python
# conformance/corpus/marks/xfail/test_xfail.py
import pytest


@pytest.mark.xfail(reason="known broken")
def test_expected_failure():
    assert False


def test_normal():
    assert True
```

```python
# conformance/corpus/marks/mark-filter/test_marks.py
import pytest


@pytest.mark.smoke
def test_smoke_only():
    assert True


def test_unmarked():
    assert True
```

```toml
# conformance/corpus/marks/mark-filter/case.toml
[case]
args = ["-m", "smoke"]
```

- [ ] **Step 2: Run, waive, verify exit 0**

Run: `uv run python -m conformance --only parametrize/` then `--only marks/`. Expected waiver: `xfail` if v1 lacks xfail outcome handling (README never mentions it); mark-filter deselection counts may differ. Every waiver gets a reason; re-run to exit 0, then run the full corpus: `uv run python -m conformance` → exit 0.

- [ ] **Step 3: Commit**

```bash
git add conformance/corpus/parametrize conformance/corpus/marks conformance/waivers.toml
git commit -m "test(conformance): parametrize and marks corpus"
```

---

### Task 7: Benchmark suite — the three canonical numbers

**Files:**
- Create: `conformance/bench/__init__.py` (empty), `conformance/bench/gen.py`, `conformance/bench/bench.py`
- Test: `conformance/tests/test_bench.py`

**Interfaces:**
- Produces: `gen.generate_suite(root: Path, files: int, tests_per_file: int) -> None`; `bench.run_benchmarks(sizes: list[tuple[int, int]], quick: bool) -> dict` returning `{"results": [{"files": int, "tests": int, "pytest_collect_s": float, "pytest_run_s": float, "rustest_run_s": float, "rustest_collect_s": float | None}]}`; CLI `python -m conformance.bench.bench [--quick] [--out PATH]` printing a markdown table and writing JSON.
- `rustest_collect_s` is `None` in Phase 0 (v1 has no collect-only); Phase 2 fills it — the schema reserves the slot now.

- [ ] **Step 1: Write the failing test**

```python
# conformance/tests/test_bench.py
from __future__ import annotations

from pathlib import Path

from conformance.bench.bench import run_benchmarks
from conformance.bench.gen import generate_suite


def test_generate_suite(tmp_path: Path) -> None:
    generate_suite(tmp_path, files=3, tests_per_file=4)
    files = sorted(tmp_path.glob("test_gen_*.py"))
    assert len(files) == 3
    assert files[0].read_text(encoding="utf-8").count("def test_") == 4


def test_run_benchmarks_quick() -> None:
    report = run_benchmarks(sizes=[(2, 2)], quick=True)
    row = report["results"][0]
    assert row["files"] == 2 and row["tests"] == 4
    assert row["pytest_collect_s"] > 0
    assert row["pytest_run_s"] > 0
    assert row["rustest_run_s"] > 0
    assert row["rustest_collect_s"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest conformance/tests/test_bench.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'conformance.bench'`

- [ ] **Step 3: Implement**

```python
# conformance/bench/gen.py
"""Generate synthetic trivial test suites for benchmarking framework overhead."""

from __future__ import annotations

from pathlib import Path


def generate_suite(root: Path, files: int, tests_per_file: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for file_index in range(files):
        lines = []
        for test_index in range(tests_per_file):
            lines.append(f"def test_case_{test_index}():")
            lines.append(f"    assert {test_index} + 1 == {test_index + 1}")
            lines.append("")
        (root / f"test_gen_{file_index:04d}.py").write_text(
            "\n".join(lines), encoding="utf-8"
        )
```

```python
# conformance/bench/bench.py
"""Benchmark pytest vs rustest: collection, full run. Usage: python -m conformance.bench.bench"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .gen import generate_suite

DEFAULT_SIZES = [(10, 10), (100, 10), (500, 10)]


def _time_cmd(cmd: list[str], cwd: Path) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    return time.perf_counter() - start


def run_benchmarks(sizes: list[tuple[int, int]], quick: bool) -> dict:
    results = []
    for files, tests_per_file in sizes:
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / "suite"
            generate_suite(suite, files, tests_per_file)
            pytest_base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
            row = {
                "files": files,
                "tests": files * tests_per_file,
                "pytest_collect_s": _time_cmd([*pytest_base, "--collect-only"], suite),
                "pytest_run_s": _time_cmd([*pytest_base, "--tb=no"], suite),
                "rustest_run_s": _time_cmd(
                    [sys.executable, "-m", "rustest", ".", "--color", "never"], suite
                ),
                "rustest_collect_s": None,  # v1 has no collect-only; Phase 2 fills this
            }
            results.append(row)
        if quick:
            break
    return {"results": results}


def main() -> int:
    parser = argparse.ArgumentParser(prog="conformance-bench")
    parser.add_argument("--quick", action="store_true", help="Smallest size only")
    parser.add_argument("--out", default="conformance/bench_results.json")
    args = parser.parse_args()
    report = run_benchmarks(DEFAULT_SIZES[:1] if args.quick else DEFAULT_SIZES, args.quick)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("| files | tests | pytest collect | pytest run | rustest run |")
    print("|---|---|---|---|---|")
    for row in report["results"]:
        print(
            f"| {row['files']} | {row['tests']} | {row['pytest_collect_s']:.2f}s "
            f"| {row['pytest_run_s']:.2f}s | {row['rustest_run_s']:.2f}s |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests, then a real quick benchmark**

Run: `uv run pytest conformance/tests/test_bench.py -v` → 2 PASS.
Run: `uv run python -m conformance.bench.bench --quick` → markdown table prints, `conformance/bench_results.json` written (do not commit the JSON; add `conformance/bench_results.json` to `.gitignore`).

- [ ] **Step 5: Commit**

```bash
git add conformance/bench conformance/tests/test_bench.py .gitignore
git commit -m "feat(conformance): benchmark suite for collection and run times"
```

---

### Task 8: CI wiring and conformance README

**Files:**
- Create: `.github/workflows/conformance.yml`, `conformance/README.md`

**Interfaces:** consumes the CLI entry points from Tasks 3 and 7.

- [ ] **Step 1: Create the workflow**

```yaml
# .github/workflows/conformance.yml
name: Conformance

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
  schedule:
    - cron: "0 6 * * 1" # weekly benchmark run

jobs:
  conformance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --all-extras
      - run: uv run maturin develop --release
      - run: uv run pytest conformance/tests -v
      - run: uv run python -m conformance
      - name: Benchmarks (scheduled/manual only)
        if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
        run: uv run python -m conformance.bench.bench --out bench_results.json
      - name: Upload benchmark results
        if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
        uses: actions/upload-artifact@v4
        with:
          name: bench-results
          path: bench_results.json
```

- [ ] **Step 2: Write `conformance/README.md`**

```markdown
# Conformance & Benchmarks

The fitness function for the rustest v2 rewrite
(see `docs/superpowers/specs/2026-07-25-rustest-v2-architecture-design.md`).

- `python -m conformance` — run every corpus case through real pytest and real
  rustest, diff collected IDs + outcome counts + exit codes. Exit 1 on any
  unwaived divergence. `--only PREFIX` filters cases.
- `conformance/corpus/<area>/<case>/` — one directory per case: `test_*.py`
  files, optional `conftest.py`, optional `case.toml` (`[case] args = [...]`).
- `conformance/waivers.toml` — every known divergence with a mandatory reason.
  Phase gates are defined as this file shrinking. `NEW-BUG:` prefix marks
  divergences discovered by the corpus that the v1 audit didn't predict.
- `python -m conformance.bench.bench [--quick]` — the three canonical numbers
  (pytest collect / pytest run / rustest run; rustest collect arrives in
  Phase 2).

pytest is a dev-dependency only. It never ships with rustest.
```

- [ ] **Step 3: Verify everything end-to-end**

Run: `uv run pytest conformance/tests python/tests/test_json_report.py -v` → all PASS.
Run: `uv run python -m conformance` → exit 0 (matches + waivers only).
Run: `uv run pre-commit run --all-files` → clean.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/conformance.yml conformance/README.md
git commit -m "ci: conformance and benchmark workflow"
```

---

## Definition of done (Phase 0)

1. `uv run python -m conformance` exits 0 with ≥18 corpus cases; every waiver has a reason.
2. `uv run python -m conformance.bench.bench` produces the three numbers across three suite sizes.
3. The Conformance CI workflow passes on the PR.
4. Waivers prefixed `NEW-BUG:` are filed as GitHub issues (use the repo's issue conventions) so Phase 1 inherits them.

Phase 1's plan is written only after this gate is green, using the corpus results as its requirements input.
