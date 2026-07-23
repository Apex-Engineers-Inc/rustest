"""Tests for LlmRenderer JSONL output. Assertions parse JSON, never match strings."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class FakeTestCompletedEvent:
    test_id: str
    file_path: str
    test_name: str
    status: str
    message: str | None = None
    duration: float = 0.0
    timestamp: float = 0.0


@dataclass
class FakeCollectionErrorEvent:
    path: str
    message: str
    timestamp: float = 0.0


@dataclass
class FakeTestResult:
    name: str
    path: str
    status: str
    message: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    duration: float = 0.0


@dataclass
class FakeRunReport:
    passed: int
    failed: int
    skipped: int
    duration: float
    results: tuple[Any, ...] = ()
    collection_errors: tuple[Any, ...] = ()
    total: int = 0


ROOT = "/proj"


def render(events: list[Any], report: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Drive the renderer and return the parsed JSONL objects."""
    from rustest.renderers.llm_renderer import LlmRenderer

    buf = io.StringIO()
    r = LlmRenderer(output=buf, root=ROOT, **kwargs)
    for ev in events:
        r.handle(ev)
    r.finalize(report)
    lines = [ln for ln in buf.getvalue().splitlines() if ln]
    return [json.loads(ln) for ln in lines]


def test_every_line_is_valid_ascii_json() -> None:
    objs = render([], FakeRunReport(passed=0, failed=0, skipped=0, duration=0.0))
    assert all(isinstance(o, dict) for o in objs)


def test_meta_line_is_first_with_version() -> None:
    objs = render([], FakeRunReport(passed=1, failed=0, skipped=0, duration=0.1))
    assert objs[0]["t"] == "meta"
    assert objs[0]["v"] == 1
    assert objs[0]["tool"] == "rustest"
    assert isinstance(objs[0]["version"], str)


def test_summary_line_is_last_and_counts_present() -> None:
    objs = render([], FakeRunReport(passed=30, failed=0, skipped=2, duration=1.2))
    summ = objs[-1]
    assert summ["t"] == "summary"
    assert summ["passed"] == 30
    assert summ["failed"] == 0
    assert summ["skipped"] == 2
    assert summ["errors"] == 0
    assert summ["duration"] == 1.2


def test_all_pass_emits_only_meta_and_summary() -> None:
    objs = render(
        [
            FakeTestCompletedEvent(f"{ROOT}/t.py::test_{i}", f"{ROOT}/t.py", f"test_{i}", "passed")
            for i in range(3)
        ],
        FakeRunReport(passed=3, failed=0, skipped=0, duration=0.05),
    )
    assert [o["t"] for o in objs] == ["meta", "summary"]
    assert "rerun" not in objs[-1]


def test_zero_collected_emits_meta_and_zero_summary() -> None:
    objs = render([], FakeRunReport(passed=0, failed=0, skipped=0, duration=0.0))
    assert [o["t"] for o in objs] == ["meta", "summary"]
    assert objs[-1]["passed"] == 0


def _fail_event(name: str, line_msg: str) -> FakeTestCompletedEvent:
    msg = (
        "Traceback (most recent call last):\n"
        f'  File "{ROOT}/t.py", line {line_msg}\n'
        "AssertionError: boom\n"
    )
    return FakeTestCompletedEvent(f"{ROOT}/t.py::{name}", f"{ROOT}/t.py", name, "failed", msg)


def test_failure_line_shape() -> None:
    ev = _fail_event("test_login", "42, in test_login")
    objs = render([ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1))
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["id"] == "t.py::test_login"
    assert fail["line"] == 42
    assert fail["error"] == "AssertionError"
    assert fail["msg"] == "boom"


def test_failures_sorted_by_file_then_line() -> None:
    a = FakeTestCompletedEvent(
        f"{ROOT}/b.py::t1",
        f"{ROOT}/b.py",
        "t1",
        "failed",
        'Traceback (most recent call last):\n  File "%s/b.py", line 9, in t1\nAssertionError\n'
        % ROOT,
    )
    b = FakeTestCompletedEvent(
        f"{ROOT}/a.py::t2",
        f"{ROOT}/a.py",
        "t2",
        "failed",
        'Traceback (most recent call last):\n  File "%s/a.py", line 3, in t2\nAssertionError\n'
        % ROOT,
    )
    objs = render([a, b], FakeRunReport(passed=0, failed=2, skipped=0, duration=0.1))
    ids = [o["id"] for o in objs if o["t"] == "fail"]
    assert ids == ["a.py::t2", "b.py::t1"]


def test_collection_error_line_and_count() -> None:
    ev = FakeCollectionErrorEvent(f"{ROOT}/broken.py", "SyntaxError: unexpected indent (line 15)")
    objs = render(
        [ev],
        FakeRunReport(
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            collection_errors=(FakeTestResult("", f"{ROOT}/broken.py", "error"),),
        ),
    )
    err = next(o for o in objs if o["t"] == "error")
    assert err["path"] == "broken.py"
    assert err["error"] == "SyntaxError"
    assert err["msg"] == "unexpected indent (line 15)"
    assert objs[-1]["errors"] == 1


def test_expected_actual_populated_when_present() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        f'  File "{ROOT}/t.py", line 5, in test_x\n'
        "AssertionError\n\n__RUSTEST_ASSERTION_VALUES__\nExpected: 200\nReceived: 401"
    )
    ev = FakeTestCompletedEvent(f"{ROOT}/t.py::test_x", f"{ROOT}/t.py", "test_x", "failed", msg)
    objs = render([ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1))
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["expected"] == "200"
    assert fail["actual"] == "401"


def test_stdout_attached_to_correct_failure() -> None:
    ev = _fail_event("test_login", "42, in test_login")
    result = FakeTestResult("test_login", f"{ROOT}/t.py", "failed", stdout="hello\n")
    objs = render(
        [ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1, results=(result,))
    )
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["stdout"] == "hello"


def test_rerun_lists_failures_and_error_paths() -> None:
    fail_ev = _fail_event("test_login", "42, in test_login")
    err_ev = FakeCollectionErrorEvent(f"{ROOT}/broken.py", "SyntaxError: bad")
    objs = render(
        [fail_ev, err_ev],
        FakeRunReport(
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            collection_errors=(FakeTestResult("", f"{ROOT}/broken.py", "error"),),
        ),
    )
    rerun = objs[-1]["rerun"]
    assert "t.py::test_login" in rerun
    assert "broken.py" in rerun


def _multiframe_fail() -> FakeTestCompletedEvent:
    msg = (
        "Traceback (most recent call last):\n"
        f'  File "{ROOT}/t.py", line 42, in test_login\n'
        "    get_status()\n"
        f'  File "{ROOT}/app/client.py", line 88, in get_status\n'
        "    assert ok\n"
        "AssertionError\n"
    )
    return FakeTestCompletedEvent(
        f"{ROOT}/t.py::test_login", f"{ROOT}/t.py", "test_login", "failed", msg
    )


def test_default_has_no_code_or_frames() -> None:
    objs = render([_multiframe_fail()], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1))
    fail = next(o for o in objs if o["t"] == "fail")
    assert "code" not in fail and "frames" not in fail


def test_v_adds_code_line() -> None:
    objs = render(
        [_multiframe_fail()],
        FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1),
        verbosity=1,
    )
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["code"] == "assert ok"
    assert "frames" not in fail


def test_vv_adds_frames_outermost_first() -> None:
    objs = render(
        [_multiframe_fail()],
        FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1),
        verbosity=2,
    )
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["frames"] == [
        {"file": "t.py", "line": 42, "fn": "test_login"},
        {"file": "app/client.py", "line": 88, "fn": "get_status"},
    ]


def test_v_emits_skip_lines() -> None:
    skip = FakeTestCompletedEvent(
        f"{ROOT}/t.py::test_wip", f"{ROOT}/t.py", "test_wip", "skipped", "not ready"
    )
    objs = render([skip], FakeRunReport(passed=0, failed=0, skipped=1, duration=0.0), verbosity=1)
    skips = [o for o in objs if o["t"] == "skip"]
    assert skips == [{"t": "skip", "id": "t.py::test_wip", "reason": "not ready"}]


def test_default_omits_skip_lines() -> None:
    skip = FakeTestCompletedEvent(
        f"{ROOT}/t.py::test_wip", f"{ROOT}/t.py", "test_wip", "skipped", "not ready"
    )
    objs = render([skip], FakeRunReport(passed=0, failed=0, skipped=1, duration=0.0))
    assert not [o for o in objs if o["t"] == "skip"]
    assert objs[-1]["skipped"] == 1
