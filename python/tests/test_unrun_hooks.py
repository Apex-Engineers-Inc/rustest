"""A conftest's ``pytest_*`` hooks are not run, and the run says so once on stderr.

The engine has no plugin system, so a hook in a loaded conftest (or ``pytest_plugins``
module) is inert.  The line is a warning: exit code, stdout, JSON and ``--llm`` output are
exactly what the same tree without the hook produces.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .helpers import make_tree as _tree, run as _run

_NOTE = "NOTE: "
_TEST = "def test_ok():\n    pass\n"
_HOOK = "def pytest_configure(config):\n    pass\n"


def _rustest(tree: Path, *args: str, env: dict[str, str] | None = None):
    # These pin the NOTE rustest prints when it runs anyway, so they opt out of the fallback.
    return _run([sys.executable, "-m", "rustest", "--no-pytest-fallback", *args], tree, env)


def _notes(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if "pytest hook" in line]


def test_one_hook_is_named_once_and_changes_nothing_else(tmp_path: Path) -> None:
    hooked = _tree(tmp_path, "hooked", {"conftest.py": _HOOK, "test_a.py": _TEST})
    plain = _tree(tmp_path, "plain", {"conftest.py": "", "test_a.py": _TEST})

    with_hook = _rustest(hooked)
    without = _rustest(plain)

    assert _notes(with_hook.stderr) == [
        _NOTE
        + "1 pytest hook in conftest files is not run (rustest has no plugin system):"
        + " conftest.py::pytest_configure"
    ]
    assert _notes(without.stderr) == []
    assert (with_hook.returncode, with_hook.stdout) == (without.returncode, without.stdout)


def test_nested_conftests_across_workers_are_deduped_and_sorted(tmp_path: Path) -> None:
    files = {
        "conftest.py": "def pytest_configure(config):\n    pass\n",
        "tests/conftest.py": (
            "def pytest_collection_modifyitems(items):\n    pass\n"
            "def pytest_addoption(parser):\n    pass\n"
            "pytest_plugins = []\n"
        ),
        "tests/test_a.py": _TEST,
        "tests/test_b.py": _TEST,
        "tests/test_c.py": _TEST,
        "test_top.py": _TEST,
    }
    tree = _tree(tmp_path, "nested", files)

    result = _rustest(tree, "-n", "4")

    assert result.returncode == 0
    assert _notes(result.stderr) == [
        _NOTE
        + "3 pytest hooks in conftest files are not run (rustest has no plugin system):"
        + " conftest.py::pytest_configure,"
        + " tests/conftest.py::pytest_addoption,"
        + " tests/conftest.py::pytest_collection_modifyitems"
    ]


def test_a_pytest_plugins_module_is_named_by_its_dotted_name(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        "plugin",
        {
            "conftest.py": 'pytest_plugins = ["myplug"]\n',
            "myplug.py": "def pytest_sessionstart(session):\n    pass\n",
            "test_a.py": _TEST,
        },
    )

    result = _rustest(tree)

    assert result.returncode == 0
    assert "myplug::pytest_sessionstart" in "".join(_notes(result.stderr))


def test_collect_only_names_hooks_of_conftests_it_loaded(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "collect", {"conftest.py": _HOOK, "test_a.py": _TEST})

    # The default tier: a statically answerable tree, except that Tier S routes a directory
    # whose conftest defines a hook to a worker, which imports it and names the hook.
    result = _rustest(tree, "--collect-only", "-q")

    assert result.stdout.strip().splitlines()[0] == "test_a.py::test_ok"
    assert len(_notes(result.stderr)) == 1


def test_llm_stdout_is_still_jsonl_and_the_line_stays_on_stderr(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "llm", {"conftest.py": _HOOK, "test_a.py": _TEST})

    result = _rustest(tree, "--llm")

    assert result.returncode == 0
    assert result.stdout.strip()
    for line in result.stdout.splitlines():
        json.loads(line)
    assert "pytest hook" not in result.stdout
    assert len(_notes(result.stderr)) == 1


def test_a_tree_without_hooks_says_nothing(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        "quiet",
        {"conftest.py": "pytest_plugins = []\ndef helper():\n    pass\n", "test_a.py": _TEST},
    )

    assert _notes(_rustest(tree).stderr) == []
