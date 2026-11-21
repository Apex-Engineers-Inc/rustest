"""The session's end -- ``pytest_sessionfinish``, ``pytest_terminal_summary``,
``pytest_unconfigure`` -- as pytest's ``wrap_session`` and ``main`` run it, and the controller
that runs it under the xdist model.

Each case is one pytest probed (pytest 8.4.2, and pytest-xdist 3.8.0 where ``-n`` is named);
the expected values are pytest's.  ``test_run_hooks.py`` has the tier rule's own cases.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .helpers import make_tree, run


def _rustest(
    tree: Path, *args: str, workers: int | None = None
) -> subprocess.CompletedProcess[str]:
    pool = [] if workers is None else ["-n", str(workers)]
    return run([sys.executable, "-m", "rustest", "-q", *pool, *args], tree)


def _log(tree: Path) -> list[str]:
    path = tree / "hooks.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


#: Appends one line per hook call to ``hooks.log`` beside the conftest.
_LOGGER = """
import os
LOG = os.path.join(os.path.dirname(__file__), "hooks.log")
def log(line):
    with open(LOG, "a") as handle:
        handle.write(line + "\\n")
"""

_FOUR_FILES = {f"test_{n}.py": "def test_x():\n    pass\n" for n in range(4)}


# -- M1: what configured, unconfigures, once ----------------------------------------------


def test_a_session_end_hook_runs_the_session_in_one_process_configured_once(
    tmp_path: Path,
) -> None:
    """Initial conftest: worker 0 boots alone, says the session needs one process, and is the
    pool -- one configure, one unconfigure, as pytest."""
    conftest = _LOGGER + (
        "def pytest_configure(config):\n"
        "    log(f\"configure {os.environ.get('PYTEST_XDIST_WORKER')}\")\n"
        "def pytest_unconfigure(config):\n"
        "    log('unconfigure')\n"
    )
    tree = make_tree(tmp_path, "once", {"conftest.py": conftest, **_FOUR_FILES})

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "running on one worker" in result.stderr
    assert _log(tree) == ["configure None", "unconfigure"]


def test_a_late_session_end_hook_replans_and_every_worker_unconfigures(tmp_path: Path) -> None:
    """A conftest the scan does not see (below the rootdir): the barrier replans on one worker,
    and every worker that configured unconfigures first.  One file per call: concurrent
    appends to one file lose lines on Windows."""
    conftest = (
        "import os\n"
        "def mark(event):\n"
        "    os.makedirs('calls', exist_ok=True)\n"
        "    open(os.path.join('calls', f'{event}-{os.getpid()}'), 'w').close()\n"
        "def pytest_configure(config):\n    mark('configure')\n"
        "def pytest_unconfigure(config):\n    mark('unconfigure')\n"
    )
    files = {"sub/conftest.py": conftest, "sub/test_s.py": "def test_s():\n    pass\n"}
    tree = make_tree(tmp_path, "late", {**files, **_FOUR_FILES})

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = sorted(path.name for path in (tree / "calls").iterdir())
    configured = {name.split("-")[1] for name in calls if name.startswith("configure-")}
    unconfigured = {name.split("-")[1] for name in calls if name.startswith("unconfigure-")}
    assert configured == unconfigured and len(configured) >= 2, calls


# -- M2 / M3: a session-end hook that raises or exits ---------------------------------------


def test_a_raising_sessionfinish_still_unconfigures_and_exits_one(tmp_path: Path) -> None:
    conftest = _LOGGER + (
        "def pytest_sessionfinish(session):\n    raise RuntimeError('boom in sessionfinish')\n"
        "def pytest_unconfigure(config):\n    log('unconfigure')\n"
    )
    tree = make_tree(
        tmp_path, "raises", {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    )

    result = _rustest(tree)

    assert result.returncode == 1
    assert "RuntimeError: boom in sessionfinish" in result.stderr
    assert "teardown failed" not in result.stdout + result.stderr
    assert " passed" not in result.stderr  # pytest prints no counts line
    assert _log(tree) == ["unconfigure"]


def test_pytest_exit_in_sessionfinish_is_the_exit_code(tmp_path: Path) -> None:
    conftest = _LOGGER + (
        "import pytest\n"
        "def pytest_sessionfinish(session):\n    pytest.exit('from sf', returncode=9)\n"
        "def pytest_unconfigure(config):\n    log('unconfigure')\n"
    )
    tree = make_tree(
        tmp_path, "exits", {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    )

    result = _rustest(tree)

    assert result.returncode == 9, result.stdout + result.stderr
    assert "Exit: from sf" in result.stderr
    assert " passed" not in result.stderr
    assert _log(tree) == ["unconfigure"]


def test_a_violation_in_terminal_summary_unconfigures_before_the_hand_off(tmp_path: Path) -> None:
    conftest = _LOGGER + (
        "def pytest_terminal_summary(terminalreporter):\n"
        "    terminalreporter.stats\n"
        "def pytest_unconfigure(config):\n    log('unconfigure')\n"
    )
    tree = make_tree(
        tmp_path, "stats", {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    )

    result = _rustest(tree)

    assert "handing this session to pytest" in result.stderr
    assert _log(tree) == ["unconfigure", "unconfigure"]  # rustest's attempt, then pytest's


# -- M4: pytest.exit from a test ------------------------------------------------------------


def test_pytest_exit_in_a_test_still_ends_the_session(tmp_path: Path) -> None:
    """pytest: ``sessionfinish`` with the return code, ``unconfigure``, exit 7, and no
    ``terminal_summary`` (7 is not a status the terminal summary runs for)."""
    conftest = _LOGGER + (
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    log(f'sessionfinish {int(exitstatus)} {session.testsfailed}')\n"
        "def pytest_terminal_summary(terminalreporter):\n    log('terminal_summary')\n"
        "def pytest_unconfigure(config):\n    log('unconfigure')\n"
    )
    test = (
        "import pytest\n"
        "def test_a():\n    assert 0\n"
        "def test_b():\n    pytest.exit('bye', returncode=7)\n"
        "def test_c():\n    pass\n"
    )
    tree = make_tree(tmp_path, "exit", {"conftest.py": conftest, "test_a.py": test})

    result = _rustest(tree)

    assert result.returncode == 7, result.stdout + result.stderr
    assert _log(tree) == ["sessionfinish 7 1", "unconfigure"]


def test_pytest_exit_return_code_is_the_exit_code_without_hooks(tmp_path: Path) -> None:
    test = "import pytest\ndef test_a():\n    pytest.exit('bye', returncode=7)\n"
    tree = make_tree(tmp_path, "plain", {"test_a.py": test})

    assert _rustest(tree).returncode == 7


# -- M8: the facts --------------------------------------------------------------------------

_FACTS = _LOGGER + (
    "def pytest_sessionfinish(session, exitstatus):\n"
    "    log(f'{int(exitstatus)} {session.testsfailed} {session.testscollected}'\n"
    "        f' {session.shouldfail!r}')\n"
)

_MIXED = (
    "import pytest\n"
    "@pytest.fixture\n"
    "def bad_teardown():\n    yield\n    raise RuntimeError('td')\n"
    "def test_pass(): pass\n"
    "def test_fail(): assert 0\n"
    "def test_fail_and_td(bad_teardown): assert 0\n"
    "def test_pass_and_td(bad_teardown): pass\n"
)


def test_testsfailed_counts_failed_reports(tmp_path: Path) -> None:
    """A test that fails and errors in teardown is two failed reports."""
    tree = make_tree(tmp_path, "reports", {"conftest.py": _FACTS, "test_a.py": _MIXED})

    assert _rustest(tree).returncode == 1
    assert _log(tree) == ["1 4 4 False"]


def test_maxfail_sets_shouldfail(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "maxfail", {"conftest.py": _FACTS, "test_a.py": _MIXED})

    assert _rustest(tree, "-x").returncode == 1
    assert _log(tree) == ["1 1 4 'stopping after 1 failures'"]


def test_a_collection_error_is_a_failed_report_and_the_rest_were_collected(
    tmp_path: Path,
) -> None:
    files = {
        "conftest.py": _FACTS,
        "test_broken.py": "import nope\n",
        "test_ok.py": "def test_a(): pass\ndef test_b(): pass\n",
    }
    tree = make_tree(tmp_path, "collect", files)

    assert _rustest(tree).returncode == 2
    assert _log(tree) == ["2 1 2 False"]


def test_a_failing_shutdown_teardown_is_one_more_failed_report(tmp_path: Path) -> None:
    """``_session_end``'s ``drain_failed``: the session fixture's teardown fails after the last
    test's report, so the session ends failed."""
    conftest = _FACTS + (
        "import pytest\n"
        "@pytest.fixture(scope='session')\n"
        "def held():\n    yield\n    raise RuntimeError('session teardown')\n"
    )
    tree = make_tree(
        tmp_path, "drain", {"conftest.py": conftest, "test_a.py": "def test_a(held): pass\n"}
    )

    assert _rustest(tree).returncode == 1
    assert _log(tree) == ["1 1 1 False"]


# -- S1: nothing to run ----------------------------------------------------------------------


def test_a_session_with_no_tests_and_a_session_end_hook_goes_to_pytest(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "empty", {"conftest.py": _FACTS})

    result = _rustest(tree)

    assert result.returncode == 5
    assert "nothing to run still calls the session-end hooks" in result.stderr
    assert _log(tree) == ["5 0 0 False"]  # pytest's run


def test_a_usage_error_with_a_session_end_hook_goes_to_pytest(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "usage", {"conftest.py": _FACTS, "test_a.py": _MIXED})

    result = _rustest(tree, "-k", "and and")

    assert result.returncode == 4
    assert _log(tree) == ["4 0 0 False"]


# -- M7: what the workers and the controller register differs ----------------------------------

_GUARDED = """
class Reporter:
    def pytest_sessionfinish(self, session):
        session.exitstatus = 0
    def pytest_terminal_summary(self, terminalreporter):
        terminalreporter.write_line("REPORTER-SUMMARY")
def pytest_configure(config):
    pm = config.pluginmanager
    if not hasattr(config, "workerinput"):
        pm.register(Reporter())
"""


def test_a_workerinput_check_runs_the_session_in_one_process(tmp_path: Path) -> None:
    files = {"conftest.py": _GUARDED, **_FOUR_FILES, "test_f.py": "def test_f(): assert 0\n"}
    tree = make_tree(tmp_path, "guarded", files)

    result = _rustest(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "REPORTER-SUMMARY" in result.stdout
    assert "running on one worker" in result.stderr


def test_with_n_a_workerinput_check_gets_a_controller(tmp_path: Path) -> None:
    files = {"conftest.py": _GUARDED, **_FOUR_FILES, "test_f.py": "def test_f(): assert 0\n"}
    tree = make_tree(tmp_path, "guarded-n", files)

    result = _rustest(tree, workers=2)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "REPORTER-SUMMARY" in result.stdout
    assert "running on one worker" not in result.stderr


# -- the controller under -n -------------------------------------------------------------

_CONTROLLER = """
import os
import json
class Seen:
    def __init__(self):
        self.started = 0
        self.finished = 0
        self.reports = []
    def pytest_runtest_logstart(self, nodeid, location):
        self.started += 1
    def pytest_runtest_logfinish(self, nodeid, location):
        self.finished += 1
    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            self.reports.append([report.worker_id, report.testrun_uid])
    def pytest_sessionfinish(self, session, exitstatus):
        with open(os.path.join(os.path.dirname(__file__), "controller.json"), "w") as handle:
            json.dump({"started": self.started, "finished": self.finished,
                       "reports": self.reports, "exitstatus": int(exitstatus),
                       "shouldfail": session.shouldfail}, handle)
def pytest_configure(config):
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(Seen())
"""


def _controller(tree: Path) -> dict[str, object]:
    return json.loads((tree / "controller.json").read_text(encoding="utf-8"))


def test_the_controller_sees_logstart_logfinish_and_who_ran_each_report(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "seen", {"conftest.py": _CONTROLLER, **_FOUR_FILES})

    result = _rustest(tree, workers=2)

    assert result.returncode == 0, result.stdout + result.stderr
    seen = _controller(tree)
    assert (seen["started"], seen["finished"]) == (4, 4)
    reports = seen["reports"]
    assert isinstance(reports, list)
    assert {worker for worker, _ in reports} <= {"gw0", "gw1"}  # pyright: ignore[reportUnknownVariableType]
    assert len({uid for _, uid in reports}) == 1  # pyright: ignore[reportUnknownVariableType]


def test_with_n_and_x_the_controller_interrupts_as_xdist(tmp_path: Path) -> None:
    """pytest-xdist 3.8.0, probed: ``pytest -n 2 -x`` exits 2, and the controller's
    ``sessionfinish`` sees ``INTERRUPTED`` and ``shouldfail``."""
    files = {"conftest.py": _CONTROLLER, **_FOUR_FILES, "test_f.py": "def test_f(): assert 0\n"}
    tree = make_tree(tmp_path, "stop", files)

    result = _rustest(tree, "-x", workers=2)

    assert result.returncode == 2, result.stdout + result.stderr
    seen = _controller(tree)
    assert (seen["exitstatus"], seen["shouldfail"]) == (2, "stopping after 1 failures")


def test_a_controller_configure_that_changes_the_environment_goes_to_pytest(
    tmp_path: Path,
) -> None:
    """xdist's workers inherit what its controller's ``pytest_configure`` put in ``os.environ``;
    rustest's are already running."""
    conftest = (
        "import os\n"
        "def pytest_configure(config):\n"
        "    if not hasattr(config, 'workerinput'):\n"
        "        os.environ['SHARED_DIR'] = 'x'\n"
        "def pytest_sessionfinish(session):\n    pass\n"
    )
    tree = make_tree(tmp_path, "environ", {"conftest.py": conftest, **_FOUR_FILES})

    result = _rustest(tree, workers=2)

    assert "changed os.environ (SHARED_DIR)" in result.stderr, result.stderr


def test_a_controller_report_refuses_what_rustest_does_not_model(tmp_path: Path) -> None:
    conftest = (
        "class Seen:\n"
        "    def pytest_runtest_logreport(self, report):\n"
        "        report.node\n"
        "def pytest_configure(config):\n"
        "    if not hasattr(config, 'workerinput'):\n"
        "        config.pluginmanager.register(Seen())\n"
    )
    tree = make_tree(tmp_path, "node", {"conftest.py": conftest, **_FOUR_FILES})

    result = _rustest(tree, workers=2)

    assert "reads report.node" in result.stderr, result.stderr


# -- the renderers -------------------------------------------------------------------------

_SUMMARY = """
def pytest_terminal_summary(terminalreporter):
    terminalreporter.section("MINE")
    terminalreporter.write_line("from the hook")
"""


def test_llm_carries_the_terminal_summary_before_the_summary_line(tmp_path: Path) -> None:
    tree = make_tree(
        tmp_path, "llm", {"conftest.py": _SUMMARY, "test_a.py": "def test_a(): pass\n"}
    )

    result = run([sys.executable, "-m", "rustest", "--llm"], tree)

    kinds = [json.loads(line)["t"] for line in result.stdout.splitlines()]
    assert kinds[-2:] == ["terminal_summary", "summary"], result.stdout
    assert "from the hook" in json.loads(result.stdout.splitlines()[-2])["text"]


def test_the_terminal_reporter_records_what_pytests_writes(tmp_path: Path) -> None:
    conftest = (
        "def pytest_terminal_summary(terminalreporter):\n"
        "    terminalreporter.write_sep('-')\n"
        "    terminalreporter.write_sep('=', 'TITLED', fullwidth=40)\n"
        "    terminalreporter.line()\n"
        "    terminalreporter.write_line(b'from bytes')\n"
    )
    tree = make_tree(
        tmp_path, "ops", {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    )
    path = tree / "report.json"

    result = _rustest(tree, "--report-json", str(path))

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["terminal_summary"] == [
        ["sep", "-", None],
        ["sep", "=", "TITLED"],
        ["line", ""],
        ["line", "from bytes"],
    ]
    assert "TITLED" in result.stdout and "from bytes" in result.stdout


@pytest.mark.parametrize("plugins", [["xdist"], []], ids=["xdist", "no-xdist"])
def test_the_controller_gets_xdists_options_only_when_xdist_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plugins: list[str]
) -> None:
    """xdist's ``pytest_cmdline_main`` leaves ``tx`` / ``numprocesses`` / ``dist`` on the
    controller's options before ``pytest_configure``; without xdist nobody does."""
    from rustest import _hookwire

    monkeypatch.setattr(_hookwire, "_session", None)
    message = {"hook_runtime": True, "conftests": [], "controller": {"workers": 3}}

    session = _hookwire.start(
        message, rootdir=tmp_path, invocation_dir=tmp_path, plugins=plugins, load=lambda _: None
    )

    assert session is not None
    options = {key: session.options.get(key) for key in ("tx", "numprocesses", "dist")}
    expected = {"tx": ["popen"] * 3, "numprocesses": 3, "dist": "load"}
    assert options == (expected if plugins else dict.fromkeys(expected))
