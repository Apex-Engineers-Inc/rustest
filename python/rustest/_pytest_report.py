"""A pytest plugin that writes rustest's schema-v2 report, for the pytest fallback.

Loaded with ``-p rustest._pytest_report`` in the **child** pytest only, never in rustest's own
process.  It writes one JSON object to the path in ``RUSTEST_PYTEST_REPORT``; the parent adds
``exit_code`` and ``pytest_fallback`` and renders it exactly as it renders an engine report.

Each test gets one reduced status, by the precedence the worker uses, which differs from
pytest's tally in one place: a passing body whose teardown raises is one ``error`` here and
``1 passed, 1 error`` in pytest's own summary.

Fields that cannot be filled faithfully from pytest's side, and are therefore left out:
``teardown_errors`` and ``worker_stderr`` (there are no workers, and pytest attributes a
teardown failure to its test), and ``session_exit`` (``pytest.exit`` reaches the plugin only
as ``stopped_early``).  ``message`` of a failure is pytest's own rendering of the traceback,
so its frames are worded pytest's way rather than rustest's (and follow the user's ``--tb``).
A body that fails or passes with a teardown that raises is one ``error`` either way, where
pytest counts ``1 failed, 1 error`` or ``1 passed, 1 error``.  Only the controller writes
under pytest-xdist, where the workers' deselection counts are not aggregated.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - typing only
    from _pytest.main import Session
    from _pytest.reports import CollectReport, TestReport

#: Strongest first; a test's status is the strongest any of its phases produced.
_PRECEDENCE: Final = ("error", "failed", "xpassed", "xfailed", "skipped", "passed")

#: One entry per item run, in order: a node id collected twice (`pytest a.py a.py`) is two.
_tests: list[dict[str, Any]] = []
_open: dict[str, dict[str, Any]] = {}  # node id -> the entry its current run fills
_collected: list[str] = []
_errors: list[dict[str, str]] = []
_deselected = 0
_module_skips = 0  # a module that skipped itself at import: pytest counts it, the engine too
_started = time.perf_counter()


def _status(report: TestReport) -> str | None:
    """The status *report* contributes, or ``None`` for a phase that says nothing."""
    if report.outcome not in ("passed", "failed", "skipped"):  # e.g. a rerun's "rerun"
        return None
    if report.when == "call":
        if hasattr(report, "wasxfail"):
            return "xfailed" if report.skipped else "xpassed" if report.passed else "failed"
        return "passed" if report.passed else "failed" if report.failed else "skipped"
    if report.failed:
        return "error"
    if report.skipped:
        return "xfailed" if hasattr(report, "wasxfail") else "skipped"
    return None


def _reason(report: TestReport) -> str:
    """pytest's own raw skip / xfail reason (``_pytest.terminal._get_raw_skip_reason``)."""
    if hasattr(report, "wasxfail"):
        return str(report.wasxfail).removeprefix("reason: ")
    longrepr = report.longrepr
    reason = str(longrepr[2]) if isinstance(longrepr, tuple) else str(longrepr)
    return "" if reason == "Skipped" else reason.removeprefix("Skipped: ")


def pytest_collectreport(report: CollectReport) -> None:
    global _module_skips
    if report.failed:
        _errors.append({"path": str(report.nodeid), "message": report.longreprtext})
    elif report.skipped:
        _module_skips += 1


def pytest_deselected(items: list[Any]) -> None:
    global _deselected
    _deselected += len(items)


def pytest_collection_finish(session: Session) -> None:
    _collected[:] = [item.nodeid for item in session.items]


def pytest_runtest_logreport(report: TestReport) -> None:
    status = _status(report)
    if report.when == "setup" or report.nodeid not in _open:
        _open[report.nodeid] = {"id": report.nodeid, "status": None, "duration": 0.0}
        _tests.append(_open[report.nodeid])
    entry = _open[report.nodeid]
    entry["duration"] += float(report.duration)
    for stream in ("stdout", "stderr"):
        text: str = getattr(report, "cap" + stream)
        if text:
            entry[stream] = text  # each phase's report already carries the earlier ones
    if status is None:
        return
    if entry["status"] is not None and _PRECEDENCE.index(status) >= _PRECEDENCE.index(
        entry["status"]
    ):
        return
    entry["status"] = status
    entry.pop("message", None)
    entry.pop("line", None)
    if status in ("error", "failed"):
        entry["message"] = report.longreprtext
        crash = getattr(report.longrepr, "reprcrash", None)
        if crash is not None:
            entry["line"] = crash.lineno
    elif status != "passed":
        entry["message"] = _reason(report)


def pytest_sessionfinish(session: Session, exitstatus: int) -> None:
    path = os.environ.get("RUSTEST_PYTEST_REPORT")
    if not path or hasattr(session.config, "workerinput"):  # an xdist worker: not the controller
        return
    tests = [t for t in _tests if t["status"] is not None]  # a phase that never reported
    counts = {name: sum(t["status"] == name for t in tests) for name in _PRECEDENCE}
    counts["skipped"] += _module_skips
    report = {
        "version": 2,
        "rootdir": str(session.config.rootpath.as_posix()),
        "summary": {
            "total": len(_collected) or len(tests),
            **counts,
            "deselected": _deselected,
            "duration": time.perf_counter() - _started,
        },
        "tests": tests,
        "collection_errors": _errors,
        "collected": _collected,
    }
    if session.shouldfail or session.shouldstop or exitstatus == 2:  # -x, pytest.exit, Ctrl-C
        report["stopped_early"] = True
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle)
