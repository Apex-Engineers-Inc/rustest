"""The live event stream ``rust.run(..., on_event=...)`` delivers while a run is in progress.

Driven through the real extension and the real workers: the sequence shape, agreement with
the final report at one and four workers, ``-x``, and a callback that raises -- which must
surface from ``rust.run`` promptly and leave no worker process behind.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import pytest

from .helpers import ensure_rust_stub, write

#: The statuses whose ``message`` is a reason rather than a traceback (``core.py``'s rule).
_REASON_STATUSES = frozenset({"skipped", "xfailed", "xpassed"})

_MIXED = """\
import pytest


def test_pass():
    pass


def test_fail():
    assert 1 == 2


@pytest.mark.skip(reason='nope\\nsecond line')
def test_skip():
    pass


@pytest.mark.xfail(reason='known')
def test_xfail():
    assert False


@pytest.mark.xfail(reason='surprise')
def test_xpass():
    pass


@pytest.fixture
def boom():
    raise ValueError('setup boom')


def test_error(boom):
    pass
"""

Event = tuple[str, object]


def _run(
    root: Path, workers: int, on_event: object, *, fail_fast: bool = False
) -> dict[str, object]:
    rust = ensure_rust_stub()
    payload: str = rust.run(
        os.fspath(root),
        [],
        sys.executable,
        workers,
        None,
        None,
        fail_fast,
        on_event=on_event,
    )
    return json.loads(payload)


def _timeless(report: dict[str, object]) -> dict[str, object]:
    """The report with its wall-clock fields removed, for comparing two runs."""
    summary: dict[str, object] = report["summary"]  # pyright: ignore[reportAssignmentType]
    tests: list[dict[str, object]] = report["tests"]  # pyright: ignore[reportAssignmentType]
    return {
        **report,
        "summary": {k: v for k, v in summary.items() if k != "duration"},
        "tests": [{k: v for k, v in test.items() if k != "duration"} for test in tests],
    }


def _recorder() -> tuple[list[Event], object]:
    events: list[Event] = []

    def record(kind: str, payload: object) -> None:
        events.append((kind, payload))

    return events, record


def _mixed_tree(root: Path) -> Path:
    write(root / "pytest.ini", "[pytest]\n")
    write(root / "test_mixed.py", _MIXED)
    for index in range(5):
        body = "".join(f"def test_{n}():\n    pass\n\n\n" for n in range(3))
        write(root / f"test_more_{index}.py", body)
    return root


@pytest.mark.parametrize("workers", [1, 4])
def test_the_stream_is_bracketed_and_agrees_with_the_report(tmp_path: Path, workers: int) -> None:
    events, record = _recorder()
    report = _run(_mixed_tree(tmp_path), workers, record)
    tests: list[dict[str, str]] = report["tests"]  # pyright: ignore[reportAssignmentType]

    kind, collected = events[0]
    assert kind == "collected"
    ids, rootdir = collected  # pyright: ignore[reportGeneralTypeIssues]
    assert ids == [test["id"] for test in tests]
    assert rootdir == report["rootdir"]
    assert events[-1] == ("done", False)
    assert all(kind == "results" for kind, _ in events[1:-1])

    delivered: list[tuple[int, str, str | None]] = [
        item
        for _, burst in events[1:-1]
        for item in burst  # pyright: ignore[reportGeneralTypeIssues]
    ]
    slots = [slot for slot, _, _ in delivered]
    assert sorted(slots) == list(range(len(ids))), "every slot exactly once"
    assert Counter((ids[slot], status) for slot, status, _ in delivered) == Counter(
        (test["id"], test["status"]) for test in tests
    )

    # The reason is the report's raw message for the three reason statuses, else nothing.
    for slot, status, reason in delivered:
        expected = tests[slot].get("message") if status in _REASON_STATUSES else None
        assert reason == expected, (ids[slot], status)
    reasons = {ids[slot]: reason for slot, _, reason in delivered}
    assert reasons["test_mixed.py::test_skip"] == "nope\nsecond line"
    assert reasons["test_mixed.py::test_fail"] is None

    # Observing a run does not change it.
    assert _timeless(report) == _timeless(_run(tmp_path, workers, None))


def test_fail_fast_ends_with_done_stopped(tmp_path: Path) -> None:
    events, record = _recorder()
    report = _run(_mixed_tree(tmp_path), 1, record, fail_fast=True)

    ids: list[str] = events[0][1][0]  # pyright: ignore[reportIndexIssue, reportUnknownVariableType]
    delivered = [item for kind, burst in events if kind == "results" for item in burst]  # pyright: ignore[reportGeneralTypeIssues]
    assert events[-1] == ("done", True)
    assert len(delivered) < len(ids)
    assert len(delivered) == len(report["tests"])  # pyright: ignore[reportArgumentType]


def test_fail_fast_on_the_last_test_still_ends_with_done_stopped(tmp_path: Path) -> None:
    """Every slot produced a result, but `-x` was reached: pytest's `shouldfail` is set, so
    the run counts as stopped (no final percent) exactly as when tests are left over."""
    write(tmp_path / "pytest.ini", "[pytest]\n")
    write(tmp_path / "test_last.py", "def test_a():\n    pass\n\n\ndef test_b():\n    assert 0\n")
    events, record = _recorder()
    report = _run(tmp_path, 1, record, fail_fast=True)

    delivered = [item for kind, burst in events if kind == "results" for item in burst]  # pyright: ignore[reportGeneralTypeIssues]
    assert len(delivered) == len(report["tests"]) == 2  # pyright: ignore[reportArgumentType]
    assert events[-1] == ("done", True)


# --------------------------------------------------------------------------------------
# A callback that raises
# --------------------------------------------------------------------------------------

_SLOW = """\
import os
import pathlib
import time

pathlib.Path({pids!r}, str(os.getpid())).touch()


{tests}
"""


def _slow_tree(root: Path) -> tuple[Path, Path, Path]:
    """Six files of five 50 ms tests; each test leaves a marker, each worker its pid."""
    pids, ran = root / "pids", root / "ran"
    pids.mkdir()
    ran.mkdir()
    tree = root / "tree"
    write(tree / "pytest.ini", "[pytest]\n")
    for index in range(6):
        tests = "".join(
            f"def test_{n}():\n"
            f"    time.sleep(0.05)\n"
            f"    pathlib.Path({os.fspath(ran)!r}, 'f{index}-{n}').touch()\n\n\n"
            for n in range(5)
        )
        write(tree / f"test_slow_{index}.py", _SLOW.format(pids=os.fspath(pids), tests=tests))
    return tree, pids, ran


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            _ = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            _ = kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.parametrize("error", [RuntimeError("the renderer broke"), KeyboardInterrupt()])
def test_a_raising_callback_stops_the_run_and_reraises(
    tmp_path: Path, error: BaseException
) -> None:
    tree, pids, ran = _slow_tree(tmp_path)
    calls: list[str] = []

    def explode(kind: str, payload: object) -> None:
        calls.append(kind)
        if kind == "results":
            raise error

    with pytest.raises(type(error)) as caught:
        _ = _run(tree, 2, explode)

    assert caught.value is error
    assert calls == ["collected", "results"], "never called again after raising"
    assert len(list(ran.iterdir())) < 30, "dispatch stopped"
    workers = [int(path.name) for path in pids.iterdir()]
    assert workers, "the workers recorded themselves"
    assert not [pid for pid in workers if _alive(pid)], "no worker outlives rust.run"
