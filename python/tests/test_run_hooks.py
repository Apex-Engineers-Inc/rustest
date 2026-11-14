"""The per-test hooks, run in the worker as an xdist worker runs them.

``pytest_runtest_*`` and ``pytest_fixture_setup``: the corpus (``conformance/corpus/hooks``)
grades their order and outcomes against pytest.  These pin what the corpus cannot see -- the
hand-offs (up front and mid-run), the exit codes of a hook that raises, the discarded results,
several workers, and the item's attribute surface against pytest's own.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rustest._worker import PYTEST_FIXTUREDEF_NAMES, PYTEST_FUNCTION_NAMES

from .helpers import make_tree, run


def _rustest(tree: Path, *args: str, workers: int | None = 1) -> subprocess.CompletedProcess[str]:
    pool = [] if workers is None else ["-n", str(workers)]
    return run([sys.executable, "-m", "rustest", *pool, *args], tree)


def _report(tree: Path, *args: str, workers: int | None = 1) -> tuple[int, dict[str, object]]:
    path = tree.parent / f"{tree.name}-report.json"
    result = _rustest(tree, "--report-json", str(path), *args, workers=workers)
    return result.returncode, json.loads(path.read_text(encoding="utf-8"))


def _statuses(report: dict[str, object]) -> dict[str, str]:
    tests = report["tests"]
    assert isinstance(tests, list)
    return {str(t["id"]): str(t["status"]) for t in tests}  # pyright: ignore[reportUnknownArgumentType]


def _reasons(report: dict[str, object]) -> str:
    return "; ".join(str(r) for r in report.get("pytest_fallback", []))  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType,reportAttributeAccessIssue]


_PASS = "def test_a():\n    pass\n"


# -- the item's surface, against pytest's own --------------------------------------------


def test_the_item_and_fixturedef_name_sets_are_pytests(tmp_path: Path) -> None:
    """A name pytest's ``Function`` (or ``FixtureDef``) has is a hand-off when the shim lacks it;
    any other name is pytest's own ``AttributeError``.  The sets are read off real items."""
    tree = make_tree(
        tmp_path,
        "names",
        {
            "conftest.py": (
                "import json\n\n\n"
                "def pytest_collection_modifyitems(items):\n"
                "    names = set()\n"
                "    for item in items:\n"
                "        names.update(n for n in dir(item) if not n.startswith('__'))\n"
                "    fixturedef = items[0]._fixtureinfo.name2fixturedefs['f'][0]\n"
                "    defs = sorted(n for n in dir(fixturedef) if not n.startswith('__'))\n"
                "    with open('names.json', 'w') as handle:\n"
                "        json.dump({'item': sorted(names), 'fixturedef': defs}, handle)\n"
            ),
            "test_n.py": (
                "import pytest\n\n\n@pytest.fixture\ndef f():\n    return 1\n\n\n"
                "def test_a(f):\n    pass\n\n\n"
                "@pytest.mark.parametrize('x', [1])\ndef test_p(x):\n    pass\n"
            ),
        },
    )
    result = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:asyncio",
            "-p",
            "no:hypothesispytest",
            "-p",
            "no:pytest_codeblocks",
            "--collect-only",
        ],
        tree,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    names = json.loads((tree / "names.json").read_text(encoding="utf-8"))
    assert sorted(PYTEST_FUNCTION_NAMES) == names["item"]
    assert sorted(PYTEST_FIXTUREDEF_NAMES) == names["fixturedef"]


def test_a_name_pytests_item_lacks_is_a_plain_attribute_error_in_a_hook(tmp_path: Path) -> None:
    """The ``rep_call`` idiom: ``hasattr(item, "rep_call")`` before the wrapper set it."""
    tree = make_tree(
        tmp_path,
        "repcall",
        {
            "conftest.py": (
                "import pytest\n\n\n"
                "@pytest.hookimpl(wrapper=True)\n"
                "def pytest_runtest_makereport(item, call):\n"
                "    assert hasattr(item, 'rep_' + call.when) is False\n"
                "    report = yield\n"
                "    setattr(item, 'rep_' + call.when, report)\n"
                "    return report\n"
            ),
            "test_a.py": _PASS,
        },
    )

    code, report = _report(tree)

    assert code == 0 and not _reasons(report)


def test_item_config_in_a_hook_is_the_hooks_config(tmp_path: Path) -> None:
    """``item.config.getoption`` answers natively; ``item.config.stash`` -- something the hook
    runtime's ``config`` does not provide -- hands the session off instead of erroring tests."""
    native = make_tree(
        tmp_path,
        "getoption",
        {
            "conftest.py": (
                "def pytest_addoption(parser):\n"
                "    parser.addoption('--flag', action='store_true')\n\n\n"
                "def pytest_runtest_setup(item):\n"
                "    assert item.config.getoption('--flag') is False\n"
            ),
            "test_a.py": _PASS,
        },
    )
    stash = make_tree(
        tmp_path,
        "stash",
        {
            "conftest.py": "def pytest_runtest_setup(item):\n    item.config.stash\n",
            "test_a.py": _PASS,
        },
    )

    code, report = _report(native)
    assert code == 0 and not _reasons(report)

    result = _rustest(stash)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "discarding the 1 results so far" in result.stderr
    assert "config.stash" in result.stderr


# -- hand-offs: up front and mid-run ----------------------------------------------------


def test_a_nextitem_teardown_hook_hands_off_before_any_test(tmp_path: Path) -> None:
    tree = make_tree(
        tmp_path,
        "upfront",
        {
            "conftest.py": "def pytest_runtest_teardown(item, nextitem):\n    pass\n",
            "test_a.py": _PASS,
        },
    )

    code, report = _report(tree)

    assert code == 0 and _reasons(report)
    assert "discarded" not in report


# -- the session's end: one worker, or workers and a controller --------------------------

#: A controller-only plugin (shards.py's shape) and a summary (slow_guard.py's): the session
#: end writes what the controller saw; its exit status is overridden on 5, as CI recipes do.
_SESSION_END = """
import json
import os

import pytest


class Seen:
    def __init__(self):
        self.reports = []

    def pytest_runtest_logreport(self, report):
        self.reports.append([report.nodeid, report.when, report.outcome,
                             list(report.user_properties), type(report.location).__name__])

    def pytest_sessionfinish(self, session, exitstatus):
        with open("controller.json", "w") as handle:
            json.dump({"exitstatus": int(exitstatus), "testsfailed": session.testsfailed,
                       "testscollected": session.testscollected, "reports": self.reports},
                      handle)
        if exitstatus == 5:
            session.exitstatus = 0

    def pytest_terminal_summary(self, terminalreporter):
        terminalreporter.section("SEEN BY THE CONTROLLER")
        terminalreporter.write_line(f"{len(self.reports)} reports")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if call.when == "call":
        report.user_properties.append(("worker", os.environ.get("PYTEST_XDIST_WORKER", "-")))
    return report


def pytest_configure(config):
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(Seen())
"""

_FILES = {
    "conftest.py": _SESSION_END,
    "test_a.py": "def test_a1():\n    pass\n\n\ndef test_a2():\n    assert False\n",
    "test_b.py": "def test_b1():\n    pass\n",
}


def test_without_xdist_a_controller_hook_runs_the_session_on_one_worker(tmp_path: Path) -> None:
    """The default pool would be several workers, but nothing tells the project how such a pool
    ends its session: one worker, serial semantics, and a line that says why."""
    tree = make_tree(tmp_path, "serial", _FILES)

    result = _rustest(tree, workers=None)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "rustest: running on one worker" in result.stderr
    assert "Install pytest-xdist, or pass -n N" in result.stderr
    seen = json.loads((tree / "controller.json").read_text(encoding="utf-8"))
    assert (seen["exitstatus"], seen["testsfailed"], seen["testscollected"]) == (1, 1, 3)
    assert len(seen["reports"]) == 9  # setup, call and teardown of three tests
    assert {props[0][1] for _, when, _, props, _ in seen["reports"] if when == "call"} == {"-"}
    out = result.stdout
    assert out.index("SEEN BY THE CONTROLLER") < out.index("short test summary info")
    assert "9 reports" in out


def test_with_n_the_workers_and_one_controller_run_the_session_as_xdist(tmp_path: Path) -> None:
    """``-n 2``: each worker guards on ``workerinput`` and registers nothing; the controller
    process registers the plugin, is fed every phase report (``user_properties`` as the worker
    set them) and ends the session once."""
    tree = make_tree(tmp_path, "xdist", _FILES)

    code, report = _report(tree, workers=2)

    assert code == 1 and not _reasons(report)
    assert "notes" not in report
    seen = json.loads((tree / "controller.json").read_text(encoding="utf-8"))
    assert (seen["exitstatus"], seen["testsfailed"], seen["testscollected"]) == (1, 1, 3)
    assert len(seen["reports"]) == 9
    workers = {props[0][1] for _, when, _, props, _ in seen["reports"] if when == "call"}
    assert workers <= {"gw0", "gw1"} and workers
    assert {kind for *_, kind in seen["reports"]} == {"tuple"}  # as xdist's controller has it
    assert report["terminal_summary"] == [
        ["sep", "=", "SEEN BY THE CONTROLLER"],
        ["line", "9 reports"],
    ]


@pytest.mark.parametrize("workers", [None, 2], ids=["one-worker", "controller"])
def test_a_sessionfinish_exit_status_override_is_the_exit_code(
    tmp_path: Path, workers: int | None
) -> None:
    """``session.exitstatus = 0`` on 5 (``-k nomatch``): pytest -- and xdist's controller --
    exit 0."""
    tree = make_tree(tmp_path, "override", _FILES)

    result = _rustest(tree, "-k", "nomatch", workers=workers)

    assert result.returncode == 0, result.stdout + result.stderr
    seen = json.loads((tree / "controller.json").read_text(encoding="utf-8"))
    assert seen["exitstatus"] == 5


def test_a_user_property_the_wire_cannot_carry_hands_off(tmp_path: Path) -> None:
    files = dict(_FILES)
    files["conftest.py"] = _SESSION_END.replace(
        '("worker", os.environ.get("PYTEST_XDIST_WORKER", "-"))', '("set", {1, 2})'
    )
    tree = make_tree(tmp_path, "inexact", files)

    result = _rustest(tree, "--no-pytest-fallback", workers=2)

    # The test whose report carried it errors; the session stays (fallback is off).
    assert result.returncode == 1
    assert "which the wire cannot carry exactly" in result.stdout + result.stderr


def test_collect_only_with_a_session_end_hook_hands_off(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "collect", _FILES)

    result = _rustest(tree, "--collect-only", "-q", workers=None)

    assert "runs at the end of --collect-only too" in result.stderr


def test_a_violation_mid_run_discards_the_results_and_reruns_under_pytest(tmp_path: Path) -> None:
    """The late hand-off: the second test's report hook touches what the runtime cannot answer.
    The batch stops there, the results so far are said to be discarded, the session reruns
    under pytest, and the native attempt wrote no duration record."""
    tree = make_tree(
        tmp_path,
        "late",
        {
            "conftest.py": (
                "def pytest_runtest_logreport(report):\n"
                "    if report.when == 'call' and report.nodeid.endswith('test_2'):\n"
                "        report.capstdout\n"
            ),
            "test_l.py": "".join(f"def test_{n}():\n    pass\n" for n in range(1, 5)),
        },
    )

    result = _rustest(tree)

    assert result.returncode == 0
    assert "rustest: discarding the 2 results so far" in result.stderr
    assert "report.capstdout" in result.stderr
    assert "reports an error" in result.stderr  # what --no-pytest-fallback would do instead
    assert "4 passed" in result.stdout  # pytest's own run of the whole session
    assert not (tree / ".rustest_cache" / "v" / "rustest" / "durations").exists()


@pytest.mark.parametrize("hook", ["logstart", "logfinish"])
def test_a_violation_in_logstart_or_logfinish_is_caught_like_any_other(
    tmp_path: Path, hook: str
) -> None:
    tree = make_tree(
        tmp_path,
        hook,
        {
            "conftest.py": (
                f"def pytest_runtest_{hook}(nodeid, location):\n"
                "    import pytest\n"
                "    pytest.TestReport.capstdout.fget(None)\n"
            ),
            "test_l.py": "".join(f"def test_{n}():\n    pass\n" for n in range(1, 4)),
        },
    )

    code, report = _report(tree, "--no-pytest-fallback")

    assert code == 1, report
    assert list(_statuses(report).values()) == ["error", "error", "error"]


def test_without_fallback_a_violation_mid_run_errors_that_test_only(tmp_path: Path) -> None:
    tree = make_tree(
        tmp_path,
        "nofallback",
        {
            "conftest.py": (
                "def pytest_runtest_logreport(report):\n"
                "    if report.when == 'call' and report.nodeid.endswith('test_2'):\n"
                "        report.capstdout\n"
            ),
            "test_l.py": "".join(f"def test_{n}():\n    pass\n" for n in range(1, 4)),
        },
    )

    code, report = _report(tree, "--no-pytest-fallback")

    assert list(_statuses(report).values()) == ["passed", "error", "passed"]
    assert code == 1 and "pytest_fallback" not in report


def test_a_wide_teardown_failure_a_report_hook_would_see_hands_off(tmp_path: Path) -> None:
    """pytest reports a module fixture's failed teardown in the last test's teardown report;
    rustest tears the module down at the next test, so a report hook would see a pass."""
    tree = make_tree(
        tmp_path,
        "wide",
        {
            "conftest.py": (
                "import pytest\n\n\n"
                "@pytest.fixture(scope='module')\ndef shared():\n    yield\n"
                "    raise RuntimeError('module teardown')\n\n\n"
                "def pytest_runtest_logreport(report):\n    pass\n"
            ),
            "test_a.py": "def test_one(shared):\n    pass\n",
            "test_b.py": "def test_two():\n    pass\n",
        },
    )

    code, report = _report(tree)

    assert code == 1
    assert "session-scoped fixture teardown" in _reasons(report)


def test_a_teardown_hook_raising_before_pytests_teardown_hands_off(tmp_path: Path) -> None:
    tree = make_tree(
        tmp_path,
        "tdraise",
        {
            "conftest.py": (
                "def pytest_runtest_teardown(item):\n"
                "    if item.name == 'test_1':\n"
                "        raise RuntimeError('before the fixtures')\n"
            ),
            "test_l.py": "def test_1():\n    pass\n\n\ndef test_2():\n    pass\n",
        },
    )

    code, report = _report(tree)

    assert code == 1
    assert "raised before test_l.py::test_1's fixtures were torn down" in _reasons(report)


# -- exit codes, several workers --------------------------------------------------------


def test_a_logreport_that_raises_is_an_internal_error(tmp_path: Path) -> None:
    """Not inside ``CallInfo``: pytest reports INTERNALERROR and exits 3."""
    tree = make_tree(
        tmp_path,
        "internal",
        {
            "conftest.py": "def pytest_runtest_logreport(report):\n    raise RuntimeError('lr')\n",
            "test_a.py": _PASS,
        },
    )

    result = _rustest(tree)

    assert result.returncode == 3
    assert "RuntimeError: lr" in result.stderr


def test_hooks_run_in_every_worker_and_the_run_stays_native(tmp_path: Path) -> None:
    """Three files over two workers: each worker registers the conftest and runs its hooks
    for its own tests, as each xdist worker does."""
    tree = make_tree(
        tmp_path,
        "pool",
        {
            "conftest.py": (
                "import pytest\n\n\n"
                "@pytest.hookimpl(tryfirst=True)\n"
                "def pytest_runtest_setup(item):\n"
                "    if item.get_closest_marker('later'):\n"
                "        pytest.skip('later')\n\n\n"
                "@pytest.hookimpl(wrapper=True)\n"
                "def pytest_runtest_makereport(item, call):\n"
                "    report = yield\n"
                "    report.user_properties.append(('seen', call.when))\n"
                "    return report\n"
            ),
            "pytest.ini": "[pytest]\nmarkers =\n    later: skipped by the conftest\n",
            **{
                f"test_{n}.py": (
                    "import pytest\n\n\n@pytest.mark.later\ndef test_skip():\n    pass\n\n\n"
                    "def test_run():\n    pass\n"
                )
                for n in "abc"
            },
        },
    )

    code, report = _report(tree, workers=2)

    assert code == 0 and not _reasons(report)
    statuses = _statuses(report)
    assert sorted(statuses.values()) == ["passed"] * 3 + ["skipped"] * 3


# -- fixtures ---------------------------------------------------------------------------


def test_a_fixture_finalizer_runs_even_when_its_setup_failed(tmp_path: Path) -> None:
    """``FixtureDef.execute`` schedules the finalizer "even if the setup failed"."""
    tree = make_tree(
        tmp_path,
        "finalizer",
        {
            "test_a.py": (
                "import pytest\n\nLOG = []\n\n\n"
                "@pytest.fixture\ndef broken(request):\n"
                "    request.addfinalizer(lambda: LOG.append('finalized'))\n"
                "    raise RuntimeError('setup')\n\n\n"
                "def test_one(broken):\n    pass\n\n\n"
                "def test_two():\n    assert LOG == ['finalized']\n"
            ),
        },
    )

    code, report = _report(tree)

    assert _statuses(report) == {"test_a.py::test_one": "error", "test_a.py::test_two": "passed"}
    assert code == 1
