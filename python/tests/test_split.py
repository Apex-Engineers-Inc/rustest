"""Within-file splitting under ``-n``: a costly file's tests run in more than one worker, as
under ``pytest -n`` (here in contiguous slices), once the duration record says it is costly."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .helpers import clean_env, make_tree, run

#: Eight slow tests in one file (a class among them) and a quick file. Every process that sets
#: the module fixture up appends its pid, so the log shows where the slices ran.
_SLOW = """\
import os, time
import pytest

LOG = os.path.join(os.path.dirname(__file__), "setups.txt")

@pytest.fixture(scope="module")
def module_state():
    with open(LOG, "a") as handle:
        handle.write(f"{os.getpid()}\\n")
    return os.getpid()

def test_0(module_state):
    time.sleep(0.3)
    assert module_state == os.getpid()

def test_1(module_state):
    time.sleep(0.3)

def test_2(module_state):
    time.sleep(0.3)

class TestGroup:
    def test_3(self, module_state):
        time.sleep(0.3)

    def test_4(self, module_state):
        time.sleep(0.3)

def test_5(module_state):
    time.sleep(0.3)

def test_6(module_state):
    time.sleep(0.3)

def test_7(module_state):
    time.sleep(0.3)
    assert module_state == os.getpid()
"""

_FILES = {"test_slow.py": _SLOW, "test_quick.py": "def test_quick():\n    pass\n"}


def _llm(tree: Path, *args: str) -> list[dict[str, object]]:
    result = run([sys.executable, "-m", "rustest", "--llm", *args], tree)
    assert result.returncode == 0, result.stdout + result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def _setups(tree: Path) -> list[str]:
    log = tree / "setups.txt"
    lines = log.read_text(encoding="utf-8").split()
    log.unlink()
    return lines


def test_a_recorded_costly_file_is_split_under_n_and_says_so(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "split", _FILES)

    first = _llm(tree, "-n", "2")  # no record yet: whole files
    assert not [line for line in first if line["t"] == "split"]
    assert len(_setups(tree)) == 1

    second = _llm(tree, "-n", "2")
    splits = [line for line in second if line["t"] == "split"]
    assert len(splits) == 1, splits
    assert (splits[0]["file"], splits[0]["workers"]) == ("test_slow.py", ["gw0", "gw1"])
    tests = splits[0]["tests"]
    assert isinstance(tests, list) and sum(tests) == 8 and min(tests) > 0, splits
    assert second[-1]["passed"] == 9
    assert len(set(_setups(tree))) == 2  # one module instance per slice, in two processes

    # Slices are cut over what was selected.
    third = _llm(tree, "-n", "2", "-k", "not test_quick and not test_7")
    assert third[-1]["passed"] == 7 and third[-1]["deselected"] == 2
    _ = _setups(tree)


def test_whole_files_stay_the_rule_without_n(tmp_path: Path) -> None:
    """No ``-n``: whatever the record says, files are not split (plain ``pytest`` never splits
    a file, pytest-xdist installed or not)."""
    tree = make_tree(tmp_path, "whole", _FILES)

    for _ in range(2):
        lines = _llm(tree)
        assert not [line for line in lines if line["t"] == "split"]
        assert len(_setups(tree)) == 1


def test_a_file_each_worker_collects_differently_runs_whole(tmp_path: Path) -> None:
    """Ids that depend on the process (``pytest -n`` refuses such a session): the workers
    disagree, so the file runs whole on its first worker and nothing is reported split."""
    slow = (
        "import os, time\n"
        "import pytest\n"
        "@pytest.mark.parametrize('n', range(8), ids=lambda n: f'{n}-{os.getpid()}')\n"
        "def test_slow(n):\n"
        "    time.sleep(0.2)\n"
    )
    tree = make_tree(tmp_path, "differ", {**_FILES, "test_slow.py": slow})

    for _ in range(2):
        lines = _llm(tree, "-n", "2")
        assert not [line for line in lines if line["t"] == "split"]
        assert lines[-1]["passed"] == 9


def test_a_node_id_run_is_not_split(tmp_path: Path) -> None:
    """A node id narrows the run before anything is collected: one test, one worker, however
    costly its file's record."""
    tree = make_tree(tmp_path, "nodeid", _FILES)
    _ = _llm(tree, "-n", "2")  # the record
    _ = _setups(tree)

    env = {**clean_env(), "RUSTEST_SCHEDULE_TRACE": "1"}
    result = run(
        [sys.executable, "-m", "rustest", "--llm", "-n", "8", "test_slow.py::test_0"], tree, env
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "rustest-schedule: pool 1," in result.stderr, result.stderr
    assert '"t": "split"' not in result.stdout and '"t":"split"' not in result.stdout


def test_a_slow_module_fixture_does_not_move_the_split(tmp_path: Path) -> None:
    """The split is weighed and cut by call time: the module fixture's setup, which lands on
    each slice's first test, does not feed the next plan. Two identical runs, one split line."""
    slow = _SLOW.replace("    return os.getpid()", "    time.sleep(1.5)\n    return os.getpid()")
    tree = make_tree(tmp_path, "stable", {**_FILES, "test_slow.py": slow})
    _ = _llm(tree, "-n", "4")  # the record

    lines = []
    for _ in range(2):
        lines.append([line for line in _llm(tree, "-n", "4") if line["t"] == "split"])
    assert lines[0] and lines[0] == lines[1], lines
