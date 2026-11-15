"""A pytest plugin that writes rustest's schema-v2 report, for the pytest fallback.

Loaded with ``-p rustest._pytest_report`` in the **child** pytest only, never in rustest's own
process.  It writes one JSON object to the path in ``RUSTEST_PYTEST_REPORT``; the parent adds
``exit_code`` and ``pytest_fallback`` and renders it exactly as it renders an engine report.

Each test gets one reduced status, by the precedence the worker uses; a teardown that raises
is the test's ``teardown_error`` beside that status, counted in ``summary.error`` as pytest
counts ``1 passed, 1 error``.

Fields that cannot be filled faithfully from pytest's side, and are therefore left out:
``teardown_errors`` and ``worker_stderr`` (there are no workers, and pytest attributes a
teardown failure to its test), and ``session_exit`` (``pytest.exit`` reaches the plugin only
as ``stopped_early``).  ``message`` of a failure is pytest's own rendering of the traceback,
so its frames are worded pytest's way rather than rustest's (and follow the user's ``--tb``).
Only the controller writes under pytest-xdist; each worker hands it its deselection count
through ``workeroutput`` (xdist's channel), because deselection happens in the workers.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, Final

import pytest

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


_warnings: list[dict[str, str]] = []  # recorded outside a test: collection, config


def pytest_warning_recorded(
    warning_message: Any, when: str, nodeid: str, location: tuple[str, int, str] | None
) -> None:
    """A warning, as rustest's report records one: located by node id inside a test, by
    ``path:line`` otherwise, with pytest's own text (``warning_record_to_str``)."""
    from _pytest.warnings import warning_record_to_str

    text = warning_record_to_str(warning_message)
    if when == "runtest" and nodeid in _open:
        _open[nodeid].setdefault("warnings", []).append({"location": nodeid, "message": text})
    else:
        where = f"{location[0]}:{location[1]}" if location else nodeid
        _warnings.append({"location": where, "message": text})


#: B-lite under the xdist model: this pytest is one more xdist worker of a rustest session.
_HANDED = os.environ.get("RUSTEST_HANDED_WORKER") == "1"
#: ... with a controller to replay its reports into: only then are they recorded.
_RECORDS = _HANDED and os.environ.get("RUSTEST_HANDED_RECORDS") == "1"
_records: list[dict[str, object]] = []  # what the controller replays, in event order
_misses: list[str] = []  # workerinput keys a conftest asked for that this worker lacks
_unwirable = False  # a user_properties value the wire to the controller cannot carry exactly
_list_changed = False  # a pytest_collection_modifyitems changed this session's list
_foreign = False  # a pytest_collection_modifyitems neither pytest's own nor this plugin's ran
_exited = False  # `pytest.exit()` or an interrupt ended the session, whatever its returncode


class _WorkerInput(dict[str, object]):
    """``config.workerinput`` with xdist's three keys. Another key -- one another plugin's
    ``pytest_configure_node`` would have added -- is recorded and raises ``KeyError``: what the
    conftest would have done with it is unknown, so rustest hands the session to pytest."""

    def __missing__(self, key: str) -> object:
        _misses.append(key)
        raise KeyError(key)

    def get(self, key: str, default: object = None) -> object:
        if key not in self:
            _misses.append(key)
        return super().get(key, default)


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    """Before any conftest is imported -- as xdist's ``remote.py`` sets them before the
    session starts -- so a conftest's own ``tryfirst`` ``pytest_configure`` sees them too."""
    if _HANDED:
        early_config.workerinput = _WorkerInput(  # pyright: ignore[reportAttributeAccessIssue]
            workerid=os.environ["PYTEST_XDIST_WORKER"],
            workercount=int(os.environ["PYTEST_XDIST_WORKER_COUNT"]),
            testrunuid=os.environ["PYTEST_XDIST_TESTRUNUID"],
        )
        early_config.workeroutput = {}  # pyright: ignore[reportAttributeAccessIssue]


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> Generator[None]:
    """Outermost around every ``pytest_collection_modifyitems``: did the list change (selection,
    order, marks), with an implementation other than pytest's own registered? Run over the
    handed files alone, such a hook may have answered differently over the whole session, so
    rustest hands the session over. pytest's own (fixture reordering, ``--deselect``) are
    item-local and do not count; with ``-k``/``-m`` the change cannot be told from pytest's
    selection, so rustest hands over on the foreign hook alone."""
    global _list_changed, _foreign
    manager = config.pluginmanager
    # An installed plugin (an entry point) is one rustest's own autoload check already let
    # through as item-local (hypothesis marks its tests); a conftest or `pytest_plugins`
    # module is not.
    installed = {id(plugin) for plugin, _ in manager.list_plugin_distinfo()}
    for impl in manager.hook.pytest_collection_modifyitems.get_hookimpls():
        module = getattr(impl.plugin, "__name__", None) or type(impl.plugin).__module__
        if id(impl.plugin) not in installed and not (
            module.startswith("_pytest.") or module == __name__
        ):
            _foreign = True

    def shape() -> list[tuple[str, tuple[str, ...]]]:
        return [(item.nodeid, tuple(mark.name for mark in item.iter_markers())) for item in items]

    before = shape()
    yield
    _list_changed = _list_changed or shape() != before


def _record(report: TestReport) -> dict[str, object]:
    """One phase report in the shape rustest's workers ship (``_runtest.record_of``)."""
    longrepr = report.longrepr
    if longrepr is None:
        wire: object = None
    elif isinstance(longrepr, tuple):
        wire = {"skip": list(longrepr)}
    elif (crash := getattr(longrepr, "reprcrash", None)) is not None:
        wire = {"crash": [crash.path, crash.lineno, crash.message]}
    else:
        wire = {"text": str(longrepr)}
    record: dict[str, object] = {
        "nodeid": report.nodeid,
        "location": list(report.location),
        "keywords": list(report.keywords),
        "outcome": report.outcome,
        "longrepr": wire,
        "when": report.when,
        "duration": report.duration,
        "start": report.start,
        "stop": report.stop,
        "user_properties": [[name, value] for name, value in report.user_properties],
        "worker_id": os.environ["PYTEST_XDIST_WORKER"],
        "testrun_uid": os.environ["PYTEST_XDIST_TESTRUNUID"],
    }
    if hasattr(report, "wasxfail"):
        record["wasxfail"] = report.wasxfail
    from rustest._hookshim import wire_exact  # only a handed worker with a controller

    # `{name: value}`: exact only when the name is a string and the value survives JSON.
    if not all(wire_exact({n: v}) for n, v in report.user_properties):
        global _unwirable
        _unwirable = True  # the wire cannot carry it exactly: pytest's session
    return record


def pytest_keyboard_interrupt() -> None:
    """pytest calls it for ``pytest.exit()`` too (``wrap_session``), whatever the returncode: the
    session ended there, which a ``returncode`` of 0, 1 or 5 does not show."""
    global _exited
    _exited = True


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    if _RECORDS:
        _records.append({"hook": "logstart", "nodeid": nodeid, "location": list(location)})


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    if _RECORDS:
        _records.append({"hook": "logfinish", "nodeid": nodeid, "location": list(location)})


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
    if _RECORDS:
        _records.append(_record(report))
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
    if report.when == "teardown" and report.failed:
        # pytest's `1 passed, 1 error`: an error of its own beside the test's status
        entry["teardown_error"] = report.longreprtext
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


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    """xdist's controller, as each worker finishes: every worker collects and deselects the
    whole session, so their counts agree and the controller keeps one."""
    global _deselected
    output: dict[str, Any] = getattr(node, "workeroutput", None) or {}
    _deselected = max(_deselected, int(output.get("rustest_deselected", 0)))


def pytest_sessionfinish(session: Session, exitstatus: int) -> None:
    path = os.environ.get("RUSTEST_PYTEST_REPORT")
    workeroutput = getattr(session.config, "workeroutput", None)
    if isinstance(workeroutput, dict) and not _HANDED:  # an xdist worker: count to controller
        workeroutput["rustest_deselected"] = _deselected
        return
    if not path or (hasattr(session.config, "workerinput") and not _HANDED):
        return
    tests = [t for t in _tests if t["status"] is not None]  # a phase that never reported
    counts = {name: sum(t["status"] == name for t in tests) for name in _PRECEDENCE}
    counts["skipped"] += _module_skips
    counts["error"] += sum("teardown_error" in t for t in tests)
    report = {
        "version": 2,
        "rootdir": str(session.config.rootpath.as_posix()),
        "summary": {
            "total": len(_collected) or len(tests),
            **counts,
            "deselected": _deselected,
            "duration": time.perf_counter() - _started,
            "warnings": len(_warnings) + sum(len(t.get("warnings", ())) for t in tests),
        },
        "tests": tests,
        "collection_errors": _errors,
        "collected": _collected,
        "warnings": _warnings,
    }
    report["list_changed"] = _list_changed and _foreign
    report["foreign_modifyitems"] = _foreign
    report["exited"] = _exited
    if _HANDED:
        report["records"] = _records
        report["workerinput_miss"] = bool(_misses)
        report["unwirable"] = _unwirable
    # -x, Ctrl-C, pytest.exit (whatever its returncode)
    if session.shouldfail or session.shouldstop or exitstatus == 2 or _exited:
        report["stopped_early"] = True
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle)
