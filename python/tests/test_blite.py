"""B-lite: test files that use pytest's ``pytester`` run under one pytest subprocess first, the
rest natively -- and every guard that sends the whole session to pytest instead."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .helpers import clean_env, make_tree, run

_PYTESTER = (
    'pytest_plugins = ["pytester"]\n'
    "def test_inner(pytester):\n"
    '    pytester.makepyfile(test_inner="def test_x():\\n    pass\\n")\n'
    '    pytester.runpytest("-p", "no:cacheprovider").assert_outcomes(passed=1)\n'
    "def test_fails():\n"
    "    assert False\n"
)
_FILES = {
    "test_a.py": "def test_one():\n    pass\n",
    "test_b_pytester.py": _PYTESTER,
    "test_c.py": "def test_two():\n    pass\n",
}


def _rustest(
    tree: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    path = tree.parent / f"{tree.name}.json"
    result = run([sys.executable, "-m", "rustest", "--report-json", str(path), *args], tree, env)
    return result, json.loads(path.read_text(encoding="utf-8"))


def _ids(report: dict[str, object]) -> list[tuple[str, str]]:
    tests = report["tests"]
    assert isinstance(tests, list)
    return [(str(t["id"]), str(t["status"])) for t in tests]  # pyright: ignore[reportUnknownArgumentType]


def test_the_pytester_file_runs_first_under_pytest_and_the_rest_natively(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "split", _FILES)

    result, report = _rustest(tree)

    assert result.returncode == 1, result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr
    assert "pytest_fallback" not in report
    assert _ids(report) == [
        ("test_a.py::test_one", "passed"),
        ("test_b_pytester.py::test_inner", "passed"),
        ("test_b_pytester.py::test_fails", "failed"),
        ("test_c.py::test_two", "passed"),
    ]
    lastfailed = json.loads((tree / ".rustest_cache/v/cache/lastfailed").read_text("utf-8"))
    assert list(lastfailed) == ["test_b_pytester.py::test_fails"]


def test_the_live_lines_cover_the_handed_tests_in_order(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "verbose", _FILES)

    result, _ = _rustest(tree, "-v")

    lines = [line.split(" ")[0] for line in result.stdout.splitlines() if "::" in line]
    assert lines[:4] == [
        "test_a.py::test_one",
        "test_b_pytester.py::test_inner",
        "test_b_pytester.py::test_fails",
        "test_c.py::test_two",
    ], result.stdout


def test_the_handed_files_warnings_are_the_runs(tmp_path: Path) -> None:
    files = dict(_FILES)
    files["test_b_pytester.py"] = (
        'import warnings\npytest_plugins = ["pytester"]\n'
        "def test_w():\n    warnings.warn(UserWarning('from handed'))\n"
    )
    tree = make_tree(tmp_path, "warns", files)

    result, report = _rustest(tree)

    assert result.returncode == 0, result.stderr
    summary = report["summary"]
    assert isinstance(summary, dict) and summary["warnings"] == 1
    assert "from handed" in result.stdout


def test_an_interrupt_in_a_handed_test_ends_the_session_once(tmp_path: Path) -> None:
    """pytest's session ends at a ``KeyboardInterrupt``: the handed test is not run twice and
    nothing runs after it (native files included), exit 2."""
    files = {
        "test_a.py": "def test_a():\n    open('side.txt', 'a').write('A')\n",
        "test_b_pytester.py": (
            'pytest_plugins = ["pytester"]\n'
            "def test_b():\n    open('side.txt', 'a').write('B')\n    raise KeyboardInterrupt\n"
        ),
    }
    tree = make_tree(tmp_path, "interrupt", files)

    result, _ = _rustest(tree)

    assert result.returncode == 2, result.stderr
    assert (tree / "side.txt").read_text(encoding="utf-8") == "B"
    assert "handing this session to pytest" not in result.stderr


def test_a_value_that_spells_a_path_stays_an_option_value(tmp_path: Path) -> None:
    files = {f"slow/{name}": body for name, body in _FILES.items()}
    tree = make_tree(tmp_path, "values", files)

    result, report = _rustest(tree, "-k", "slow", "slow")

    assert "expected one argument" not in result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr
    assert "pytest_fallback" not in report


def test_the_handed_run_writes_no_nodeids_cache(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "nodeids", _FILES)

    _ = _rustest(tree)

    assert not (tree / ".rustest_cache/v/cache/nodeids").exists()


def test_an_unused_session_directory_costs_pytest_no_number(tmp_path: Path) -> None:
    """Nothing native uses ``tmp_path``: rustest's own ``pytest-<n>`` goes when the run ends,
    and only the handed pytest's (pytester uses ``tmp_path``) is left."""
    temproot = tmp_path / "temproot"
    temproot.mkdir()
    tree = make_tree(tmp_path, "numbers", _FILES)

    _ = _rustest(tree, env={**clean_env(), "PYTEST_DEBUG_TEMPROOT": str(temproot)})

    (root,) = temproot.glob("pytest-of-*")
    # `pytest-current` is the handed pytest's own symlink (POSIX only).
    assert len([path for path in root.glob("pytest-[0-9]*")]) == 1


def test_k_deselects_the_handed_files_once(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "deselect", _FILES)

    result, report = _rustest(tree, "-k", "not fails")

    assert result.returncode == 0, result.stderr
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert (summary["passed"], summary["deselected"]) == (3, 1)


@pytest.mark.parametrize(
    ("name", "extra", "args"),
    [
        ("maxfail", {}, ("-x",)),
        ("node-id", {}, ("test_b_pytester.py::test_inner",)),
        (
            "fixture",
            {"test_d.py": "def test_d(pytester):\n    assert pytester.path.is_dir()\n"},
            (),
        ),
        ("aggregator", {"conftest.py": "def pytest_sessionfinish(session):\n    pass\n"}, ()),
        ("error", {"test_d.py": "import nope\n"}, ()),
        (
            "reorder",
            {"conftest.py": "def pytest_collection_modifyitems(items):\n    items.reverse()\n"},
            (),
        ),
        (
            "mark-only",
            {
                "conftest.py": "import pytest\n"
                "def pytest_collection_modifyitems(items):\n"
                "    for item in items:\n"
                "        item.add_marker(pytest.mark.tagged)\n"
            },
            (),
        ),
        (
            "k-with-a-hook",
            {"conftest.py": "def pytest_collection_modifyitems(items):\n    return\n"},
            ("-k", "not fails"),
        ),
        ("capture", {}, ("-s",)),
        ("last-failed", {}, ("--lf",)),
    ],
)
def test_what_ties_the_file_to_the_session_sends_the_whole_session(
    tmp_path: Path, name: str, extra: dict[str, str], args: tuple[str, ...]
) -> None:
    tree = make_tree(tmp_path, name, {**_FILES, **extra})

    result, report = _rustest(tree, *args)

    assert "running 1 file(s) under pytest first" not in result.stderr
    assert report.get("pytest_fallback"), result.stderr
    if name in ("node-id", "fixture"):
        assert result.returncode in (0, 1), result.stderr  # pytest's answer, not a usage error


def test_a_conftest_naming_pytester_still_sends_the_whole_session(tmp_path: Path) -> None:
    files = {"conftest.py": 'pytest_plugins = ["pytester"]\n', "test_a.py": "def test_a(): pass\n"}
    tree = make_tree(tmp_path, "conftest", files)

    result, report = _rustest(tree)

    assert result.returncode == 0, result.stderr
    assert report.get("pytest_fallback")


def test_without_an_ini_conftests_above_the_rootdir_stay_out(tmp_path: Path) -> None:
    """The handed pytest gets an empty ``-c``; ``--confcutdir`` keeps it from loading a conftest
    above the rootdir, as the session itself does not."""
    outer = tmp_path / "outer"
    (outer / "proj").mkdir(parents=True)
    _ = (outer / "conftest.py").write_text(
        "import pytest\n@pytest.fixture(autouse=True)\ndef boom():\n"
        "    raise RuntimeError('conftest above rootdir loaded')\n",
        encoding="utf-8",
    )
    _ = (outer / "proj" / "setup.py").write_text("", encoding="utf-8")
    for name, body in _FILES.items():
        _ = (outer / "proj" / name).write_text(body, encoding="utf-8")

    result, report = _rustest(outer / "proj")

    assert "conftest above rootdir loaded" not in result.stdout + result.stderr
    assert "pytest_fallback" not in report


def test_without_fallback_the_pytester_file_is_a_collection_error(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "native", _FILES)

    result, report = _rustest(tree, "--no-pytest-fallback")

    assert result.returncode == 2
    errors = report["collection_errors"]
    assert isinstance(errors, list) and len(errors) == 1  # pyright: ignore[reportUnknownArgumentType]


@pytest.mark.parametrize(
    ("name", "extra", "args"),
    [
        ("maxfail", {}, ("-x",)),
        ("aggregator", {"conftest.py": "def pytest_sessionfinish(session):\n    pass\n"}, ()),
    ],
)
def test_a_guard_known_before_collection_hands_off_before_collecting(
    tmp_path: Path, name: str, extra: dict[str, str], args: tuple[str, ...]
) -> None:
    """A pytester file plus a guard known without collecting (`-x`, or a session-end hook in
    worker 0's conftests): the session is pytest's before rustest imports any test module, so
    a module's import side effect happens once, as under pytest alone."""
    files = {
        **_FILES,
        **extra,
        "test_a.py": "open('imports.txt', 'a').write('I')\n\n\ndef test_one():\n    pass\n",
    }
    tree = make_tree(tmp_path, name, files)

    result, report = _rustest(tree, *args)

    assert report.get("pytest_fallback"), result.stderr
    assert (tree / "imports.txt").read_text(encoding="utf-8") == "I"


# -- under the xdist model: the handed pytest is one more xdist worker --------------------------

_COUNTER = """
import os

LOG = os.path.join(os.path.dirname(__file__), "log.txt")


def log(line):
    with open(LOG, "a") as handle:
        handle.write(line + chr(10))


class Counter:
    def __init__(self):
        self.reports = []

    def pytest_runtest_logreport(self, report):
        worker = getattr(report, 'worker_id', None)  # absent when pytest runs it serially
        self.reports.append((report.nodeid, report.when, worker, getattr(report, 'testrun_uid', None)))

    def pytest_terminal_summary(self, terminalreporter):
        log(f"reports {len(self.reports)} distinct {len(set(r[:2] for r in self.reports))}")
        log(f"uids {len(set(r[3] for r in self.reports))}")

    def pytest_unconfigure(self, config):
        log("controller unconfigure")


def pytest_configure(config):
    if hasattr(config, "workerinput"):
        log(f"worker {config.workerinput['workerid']} {os.environ['PYTEST_XDIST_WORKER_COUNT']}")
    else:
        config.pluginmanager.register(Counter())
"""


def _log(tree: Path) -> list[str]:
    return (tree / "log.txt").read_text(encoding="utf-8").splitlines()


def test_under_the_xdist_model_the_controller_replays_the_handed_reports_once(
    tmp_path: Path,
) -> None:
    tree = make_tree(tmp_path, "worker", {**_FILES, "conftest.py": _COUNTER})

    result, report = _rustest(tree, "-n", "2")

    assert "pytest_fallback" not in report, result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr
    log = _log(tree)
    assert "reports 12 distinct 12" in log  # four tests, three phases each, once
    assert "uids 1" in log
    workers = sorted(line for line in log if line.startswith("worker "))
    assert workers == ["worker gw0 3", "worker gw1 3", "worker gw2 3"]  # the handed one is gw2
    assert log.count("controller unconfigure") == 1


def test_under_the_xdist_model_s_no_longer_sends_the_whole_session(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "capture-n", _FILES)

    result, report = _rustest(tree, "-n", "2", "-s")

    assert "pytest_fallback" not in report, result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr


@pytest.mark.parametrize(
    "reader",
    [
        "import os\n"
        "def pytest_configure(config):\n"
        "    if os.environ.get('PYTEST_XDIST_WORKER') == 'gw2':\n"
        "        config.workerinput['added_by_another_plugin']\n",
        "import os, pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def node(request):\n"
        "    if os.environ.get('PYTEST_XDIST_WORKER') == 'gw2':\n"
        "        request.config.workerinput.get('added_by_another_plugin')\n",
    ],
    ids=["configure", "fixture"],
)
def test_a_workerinput_key_the_handed_worker_lacks_sends_the_whole_session(
    tmp_path: Path, reader: str
) -> None:
    """Read only in the handed worker (gw2 beside a pool of two), so it is that process's
    report, not a native worker's violation, that sends the session to pytest."""
    tree = make_tree(tmp_path, "miss", {**_FILES, "conftest.py": reader})

    result, report = _rustest(tree, "-n", "2")

    assert "running 1 file(s) under pytest first" in result.stderr
    assert report.get("pytest_fallback"), result.stderr


def test_an_interrupt_in_the_handed_worker_ends_the_session_and_the_controller_once(
    tmp_path: Path,
) -> None:
    files = {
        "conftest.py": _COUNTER,
        "test_a.py": "def test_a():\n    pass\n",
        "test_c.py": "def test_c():\n    pass\n",
        "test_b_pytester.py": (
            'pytest_plugins = ["pytester"]\ndef test_b():\n    raise KeyboardInterrupt\n'
        ),
    }
    tree = make_tree(tmp_path, "interrupt-n", files)

    result, report = _rustest(tree, "-n", "2")

    # xdist's controller ends such a session itself -- interrupted, with its summary hooks
    # (probed, pytest 9.0.2 + xdist 3.8.0) -- so pytest runs it: exit 2, the controller-side
    # summary ran, and rustest's own controller (configured, then left) unconfigured too.
    # Unconfigure twice, and the handed tests up to the interrupt run twice: the price of every
    # hand-off decided at the barrier, not something to "fix" into a 1.
    assert result.returncode == 2, result.stderr
    assert report.get("pytest_fallback"), result.stderr
    log = _log(tree)
    assert any(line.startswith("reports ") for line in log), log
    assert log.count("controller unconfigure") == 2


def test_a_modifyitems_hook_that_changes_nothing_keeps_the_split(tmp_path: Path) -> None:
    """B1 is about a hook that changed the list (it would run again over the handed files
    alone); one that ran and left it as it was -- a filter whose setting is off -- is
    not."""
    hook = "def pytest_collection_modifyitems(config, items):" + chr(10) + "    return" + chr(10)
    tree = make_tree(tmp_path, "noop", {**_FILES, "conftest.py": hook})

    result, report = _rustest(tree)

    assert "pytest_fallback" not in report, result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr


def test_the_controller_s_session_facts_include_the_handed_results(tmp_path: Path) -> None:
    """Every native test passes and one handed test fails: the controller's session end sees
    the whole session's exit status, failures and count (four tests, one failed)."""
    facts = (
        "import os\n"
        "class Facts:\n"
        "    def pytest_sessionfinish(self, session, exitstatus):\n"
        "        path = os.path.join(os.path.dirname(__file__), 'facts.txt')\n"
        "        with open(path, 'w') as handle:\n"
        "            handle.write(f'{int(exitstatus)} {session.testsfailed} {session.testscollected}')\n"
        "def pytest_configure(config):\n"
        "    if not hasattr(config, 'workerinput'):\n"
        "        config.pluginmanager.register(Facts())\n"
    )
    tree = make_tree(tmp_path, "facts", {**_FILES, "conftest.py": facts})

    result, report = _rustest(tree, "-n", "2")

    assert "pytest_fallback" not in report, result.stderr
    assert (tree / "facts.txt").read_text(encoding="utf-8") == "1 1 4"


def test_a_conftest_s_tryfirst_configure_sees_the_handed_worker_s_workerinput(
    tmp_path: Path,
) -> None:
    """``workerinput`` is set before any conftest loads, as xdist's ``remote.py`` sets it: a
    conftest's own ``tryfirst`` ``pytest_configure`` sees it in the handed worker too."""
    probe = (
        "import os, pytest\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_configure(config):\n"
        "    who = config.workerinput['workerid'] if hasattr(config, 'workerinput') else 'controller'\n"
        "    path = os.path.join(os.path.dirname(__file__), 'who.txt')\n"
        "    with open(path, 'a') as handle:\n"
        "        handle.write(who + chr(10))\n"
        "def pytest_sessionfinish(session):\n"
        "    pass\n"
    )
    tree = make_tree(tmp_path, "tryfirst", {**_FILES, "conftest.py": probe})

    result, report = _rustest(tree, "-n", "2")

    assert "pytest_fallback" not in report, result.stderr
    seen = sorted((tree / "who.txt").read_text(encoding="utf-8").split())
    assert seen == ["controller", "gw0", "gw1", "gw2"]


def test_a_mark_that_focuses_the_session_on_a_handed_test_goes_to_pytest(tmp_path: Path) -> None:
    """A hook keyed on the whole list (keep only ``focus``-marked tests, if any) changes nothing
    natively and everything in the handed process: pytest runs the session, so its counts."""
    files = {
        **_FILES,
        "conftest.py": (
            "def pytest_configure(config):\n"
            "    config.addinivalue_line('markers', 'focus: run only these')\n"
            "def pytest_collection_modifyitems(config, items):\n"
            "    focused = [i for i in items if i.get_closest_marker('focus')]\n"
            "    if focused:\n"
            "        config.hook.pytest_deselected(items=[i for i in items if i not in focused])\n"
            "        items[:] = focused\n"
        ),
        "test_b_pytester.py": (
            "import pytest\n"
            'pytest_plugins = ["pytester"]\n'
            "@pytest.mark.focus\n"
            "def test_inner():\n    pass\n"
            "def test_fails():\n    assert False\n"
        ),
    }
    tree = make_tree(tmp_path, "focus", files)

    result, report = _rustest(tree)

    assert report.get("pytest_fallback"), result.stderr
    assert result.returncode == 0, result.stderr
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert (summary["passed"], summary["deselected"]) == (1, 3)


def test_addopts_n_auto_keeps_the_handed_worker_one_process(tmp_path: Path) -> None:
    """A project whose ini says ``addopts = -n auto`` still gets a handed worker that is one
    process (``-n0``), so ``-k`` deselection in it is counted once and right."""
    _ = pytest.importorskip("xdist")
    tree = make_tree(tmp_path, "addopts", _FILES)
    _ = (tree / "pytest.ini").write_text("[pytest]\naddopts = -n auto\n", encoding="utf-8")

    result, report = _rustest(tree, "-k", "not fails")

    assert "pytest_fallback" not in report, result.stderr
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert (summary["passed"], summary["deselected"]) == (3, 1)


def test_a_plugin_s_modifyitems_with_k_goes_to_pytest(tmp_path: Path) -> None:
    """A plugin only the pytester file loads (``pytest_plugins``): no native worker registers
    its ``pytest_collection_modifyitems``, so only the handed process can say it exists; with
    ``-k`` its change cannot be told from pytest's selection, so the session is pytest's."""
    files = {
        **_FILES,
        "myplugin.py": (
            "def pytest_collection_modifyitems(items):\n"
            "    items[:] = [i for i in items if 'fails' not in i.name]\n"
        ),
        "test_b_pytester.py": _PYTESTER.replace(
            'pytest_plugins = ["pytester"]', 'pytest_plugins = ["pytester", "myplugin"]'
        ),
    }
    tree = make_tree(tmp_path, "foreign", files)

    result, report = _rustest(tree, "-k", "not two")

    assert report.get("pytest_fallback"), result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert (summary["passed"], summary["failed"], summary["deselected"]) == (2, 0, 1)


def test_pytest_s_own_fixture_reorder_keeps_the_split(tmp_path: Path) -> None:
    """A parametrized module-scope fixture makes pytest reorder the handed file's items
    (its own ``pytest_collection_modifyitems``): not a conftest's change, so the split stays."""
    reordered = (
        "import pytest\n"
        'pytest_plugins = ["pytester"]\n'
        "@pytest.fixture(scope='module', params=[1, 2])\n"
        "def p(request):\n    return request.param\n"
        "def test_x(p):\n    pass\n"
        "def test_y(p):\n    pass\n"
    )
    tree = make_tree(tmp_path, "reorder", {**_FILES, "test_b_pytester.py": reordered})

    result, report = _rustest(tree)

    assert "pytest_fallback" not in report, result.stderr
    assert "running 1 file(s) under pytest first" in result.stderr
    assert [i for i, _ in _ids(report) if "pytester" in i] == [
        "test_b_pytester.py::test_x[1]",
        "test_b_pytester.py::test_y[1]",
        "test_b_pytester.py::test_x[2]",
        "test_b_pytester.py::test_y[2]",
    ]


@pytest.mark.parametrize("code", [0, 1, 5])
def test_pytest_exit_in_a_handed_test_ends_the_session(tmp_path: Path, code: int) -> None:
    """``pytest.exit(returncode=N)`` in a handed test ends the session with N, as pytest's
    does: nothing native runs after it."""
    files = {
        **_FILES,
        "test_b_pytester.py": (
            "import pytest\n"
            'pytest_plugins = ["pytester"]\n'
            f"def test_bails():\n    pytest.exit('done', returncode={code})\n"
        ),
    }
    tree = make_tree(tmp_path, f"exit{code}", files)

    result, report = _rustest(tree)

    assert result.returncode == code, result.stdout + result.stderr
    assert "pytest_fallback" not in report, result.stderr
    assert not [i for i, _ in _ids(report) if not i.startswith("test_b")], _ids(report)


def test_the_handed_pytest_gets_n0_after_the_user_s_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With a worker identity and xdist importable, ``-n0`` follows the user's own arguments
    (so it beats an ini ``addopts = -n auto``) and precedes the files."""
    from rustest import _fallback

    seen: list[list[str]] = []

    class Stop(Exception):
        pass

    def popen(command: list[str], **_: object) -> object:
        seen.append(command)
        raise Stop

    monkeypatch.setattr(_fallback, "_installed", lambda module: True)
    monkeypatch.setattr("subprocess.Popen", popen)
    for worker in (("gw2", 3, "uid", True), None):
        with pytest.raises(Stop):
            _ = _fallback.run_files(
                ["-k", "x", "-n", "4"],
                ["t.py"],
                str(tmp_path),
                rootdir=str(tmp_path),
                inifile=None,
                worker=worker,
            )
    with_worker, without = seen
    assert with_worker.index("-k") < with_worker.index("-n0") < with_worker.index("t.py")
    assert "4" not in with_worker  # the user's `-n 4` is dropped
    assert "-n0" not in without


def test_ctrl_c_during_the_handed_run_ends_the_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The user's Ctrl-C while the handed pytest runs (simulated at the wait, which is where
    rustest sees it): exit 2, and no whole pytest run is started after it."""
    from rustest import _fallback, cli

    tree = make_tree(tmp_path, "ctrl-c", {**_FILES, "conftest.py": _COUNTER})
    whole: list[object] = []
    monkeypatch.setattr(_fallback, "_wait_interruptible", lambda child: (child.wait(), True))
    monkeypatch.setattr(_fallback, "run", lambda *a, **k: whole.append(a) or 0)
    monkeypatch.chdir(tree)

    code = cli.main(["-n", "2"])

    assert code == 2
    assert whole == []


def test_pytest_exit_in_a_handed_test_with_a_controller_goes_to_pytest(tmp_path: Path) -> None:
    """With a controller, xdist's ends such a session itself: pytest runs it, and its exit
    status is the returncode."""
    files = {
        **_FILES,
        "conftest.py": _COUNTER,
        "test_b_pytester.py": (
            "import pytest\n"
            'pytest_plugins = ["pytester"]\n'
            "def test_bails():\n    pytest.exit('done', returncode=0)\n"
        ),
    }
    tree = make_tree(tmp_path, "exit-controller", files)

    result, report = _rustest(tree, "-n", "2")

    assert report.get("pytest_fallback"), result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
