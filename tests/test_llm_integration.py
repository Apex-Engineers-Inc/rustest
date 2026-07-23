# tests/test_llm_integration.py
"""End-to-end: `rustest --llm` over a fixture suite emits valid JSONL."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _write_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "suite_test.py"
    suite.write_text(
        "import rustest\n"
        "def test_ok():\n    assert True\n"
        "def test_bad():\n    assert 1 == 2\n"
        "@rustest.mark.skip(reason='wip')\n"
        "def test_wip():\n    assert False\n"
    )
    return suite


def _run(args: list[str]) -> list[dict[str, object]]:
    proc = subprocess.run(
        [sys.executable, "-m", "rustest", *args],
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]  # raises if any line is not JSON


def test_llm_jsonl_end_to_end(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path)
    objs = _run(["--llm", str(suite)])

    assert objs[0]["t"] == "meta"
    assert objs[-1]["t"] == "summary"
    summ = objs[-1]
    assert summ["passed"] == 1
    assert summ["failed"] == 1
    assert summ["skipped"] == 1

    fails = [o for o in objs if o["t"] == "fail"]
    assert len(fails) == 1
    assert fails[0]["id"].endswith("::test_bad")
    assert "test_wip" not in json.dumps(objs)  # skip not emitted by default


def test_llm_verbose_emits_skip_and_code(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path)
    objs = _run(["--llm", "-v", str(suite)])
    assert any(o["t"] == "skip" and o["id"].endswith("::test_wip") for o in objs)
    assert any(o["t"] == "fail" and "code" in o for o in objs)


def test_llm_no_ansi_or_nonascii(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "rustest", "--llm", str(suite)],
        capture_output=True,
        text=True,
    )
    assert "\x1b" not in proc.stdout
    assert proc.stdout.isascii()


def test_llm_pytest_compat_stdout_stays_pure_jsonl(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "rustest", "--llm", "--pytest-compat", str(suite)],
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, "expected at least one stdout line"
    objs = [json.loads(ln) for ln in lines]  # raises if any line is not JSON
    assert objs[0]["t"] == "meta"
