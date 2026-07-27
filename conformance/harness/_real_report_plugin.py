"""A pytest plugin that writes one reduced status per test id, as JSON.

Loaded with ``-p`` from a throwaway directory on ``PYTHONPATH`` -- never installed into,
nor copied into, the target repository. It is the pytest half of the ``--real`` gate's
read surface, and it exists because the alternatives are all lossy:

* ``-q``'s summary line gives **counts only**, so a suite where rustest passes a test
  pytest skips and skips a test pytest passes reads as a perfect match.
* ``-v`` prints one line per *report*, and pytest emits up to three reports per test; a
  body that passes with a teardown that raises prints its id twice. rustest's schema-v2
  report carries one **reduced** status per test, so grading reports against tests would
  manufacture id divergences out of a difference that is real only in the counts.
* ``--junit-xml`` reconstructs node ids from ``classname``/``name`` attributes, which is
  lossy for class-based and parametrized tests and encodes xfail as a flavour of skip.

So the plugin reduces pytest's own reports with pytest's own precedence and writes the
result keyed by node id -- the same shape ``rustest --report-json`` publishes, which is
what makes an id-level diff possible at all.

Nothing here writes to the repository under test: the output path arrives in
``CONFORMANCE_REAL_REPORT`` and points into the harness's temp directory.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - typing only
    from _pytest.reports import CollectReport, TestReport
    from _pytest.runner import CallInfo

#: Environment variable naming the JSON file to write. Absent means "not our run".
REPORT_ENV_VAR: Final = "CONFORMANCE_REAL_REPORT"

#: Reduction precedence, strongest first. A test with reports in more than one phase gets
#: the strongest status any phase produced, which is how rustest's worker reduces too
#: (`python/rustest/_v2_worker.py`'s STATUSES). The one place this differs from pytest's
#: *summary line* is a passing body with a raising teardown: pytest tallies that as
#: `1 passed, 1 error` (two entries for one test), this records it as `error` (one entry).
#: The `--real` gate compares reduced-per-test tallies on BOTH sides for exactly that
#: reason -- comparing pytest's summary line against rustest's per-test report would grade
#: a schema difference as a behavioural one.
_PRECEDENCE: Final[tuple[str, ...]] = (
    "error",
    "failed",
    "xpassed",
    "xfailed",
    "skipped",
    "passed",
)

_statuses: dict[str, str] = {}
_deselected: list[str] = []
_collection_errors: list[str] = []


def _reduce(report: TestReport) -> str | None:
    """The single status *report* contributes, or ``None`` if it contributes nothing.

    Mirrors ``_pytest/skipping.py::pytest_report_teststatus`` and
    ``_pytest/runner.py``: ``wasxfail`` on a skipped call is an expected failure, on a
    passing call an unexpected pass; a setup or teardown that fails is an *error*, not a
    failure, which is the distinction pytest's own ``E``/``F`` letters carry.
    """
    when: str = report.when or ""
    if when == "call":
        if hasattr(report, "wasxfail"):
            if report.skipped:
                return "xfailed"
            if report.passed:
                return "xpassed"
        if report.passed:
            return "passed"
        if report.failed:
            return "failed"
        if report.skipped:
            return "skipped"
        return None
    if report.failed:
        return "error"
    if report.skipped:
        return "xfailed" if hasattr(report, "wasxfail") else "skipped"
    return None


def _stronger(current: str | None, incoming: str) -> str:
    if current is None:
        return incoming
    return min(current, incoming, key=_PRECEDENCE.index)


def pytest_runtest_logreport(report: TestReport) -> None:
    status = _reduce(report)
    if status is None:
        return
    node_id: str = report.nodeid
    _statuses[node_id] = _stronger(_statuses.get(node_id), status)


def pytest_collectreport(report: CollectReport) -> None:
    if report.failed:
        _collection_errors.append(str(report.nodeid))


def pytest_deselected(items: list[Any]) -> None:
    for item in items:
        _deselected.append(str(getattr(item, "nodeid", item)))


def pytest_sessionfinish(session: object, exitstatus: int | CallInfo[None]) -> None:
    path = os.environ.get(REPORT_ENV_VAR)
    if not path:
        return
    payload: dict[str, object] = {
        "statuses": _statuses,
        "deselected": sorted(_deselected),
        "collection_errors": sorted(_collection_errors),
        "exit_status": int(exitstatus) if isinstance(exitstatus, int) else -1,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
