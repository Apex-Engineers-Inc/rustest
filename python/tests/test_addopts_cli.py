"""The ``addopts`` ini, applied to argv the way pytest applies it.

Oracle: `_pytest/config/__init__.py::Config._preparse` (pytest 8.4.2, l. 1385-1397)::

    args[:] = self._validate_args(self.getini("addopts"), "via addopts config") + args

**Prepended**, so an explicit command-line flag still wins a last-one-wins option and a path
in ``addopts`` comes first.  rustest parsed the key (`src/v2/config.rs` l. 224, and it is
part of the manifest-cache fingerprint) and applied it nowhere: click's ``-m 'not stress'``
meant pytest ran 1 686 tests where rustest ran 32 686, and the Phase 3 sweep had to replay
the value on both command lines to measure anything at all (report §1.1, §4.2).

Every expectation below was **measured** on both runners first (`scratchpad/probe/ao`, six
shapes).  The two that decide the design:

* an unrecognised flag in ``addopts`` is a pytest **usage error, exit 4** — not exit 2 —
  and its message carries the ``inifile:``/``rootdir:`` lines that say which file to edit;
* a bare *path* in ``addopts`` counts as an argument, and therefore **suppresses
  ``testpaths``** exactly as a command-line path does.

Not modelled: ``PYTEST_ADDOPTS``, which pytest splices ahead of the ini value (l. 1386-1392).
See the note in `cli.py::main`.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for leak in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST", "RUSTEST_RUNNING"):
        _ = env.pop(leak, None)
    return env


def _tree(tmp_path: Path, addopts: str) -> Path:
    tree = tmp_path / "addopts_project"
    _write(tree / "pytest.ini", f"[pytest]\ntestpaths = tests\naddopts = {addopts}\n")
    _write(
        tree / "tests" / "test_a.py",
        "import pytest\n\n\ndef test_pass():\n    assert True\n\n\n"
        "@pytest.mark.slow\ndef test_slow():\n    assert True\n",
    )
    _write(tree / "other" / "test_b.py", "def test_other():\n    assert True\n")
    return tree


def _rustest(tree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rustest", "-q", *args],
        cwd=str(tree),
        capture_output=True,
        text=True,
        env=_clean_env(),
        check=False,
    )


def _pytest(tree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=str(tree),
        capture_output=True,
        text=True,
        env=_clean_env(),
        check=False,
    )


def test_addopts_marker_expression_is_applied(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "-m 'not slow'")

    oracle = _pytest(tree, [])
    ours = _rustest(tree, [])

    assert oracle.returncode == 0, oracle.stdout
    assert "1 passed, 1 deselected" in oracle.stdout, oracle.stdout
    assert ours.returncode == 0, ours.stdout + ours.stderr
    assert "1 passed, 1 deselected" in ours.stderr, ours.stderr


def test_a_path_in_addopts_suppresses_testpaths(tmp_path: Path) -> None:
    """A path is an argument, and any argument makes ``testpaths`` inert — on both sides."""
    tree = _tree(tmp_path, "other")

    oracle = _pytest(tree, [])
    ours = _rustest(tree, [])

    assert oracle.returncode == 0 and "1 passed" in oracle.stdout, oracle.stdout
    assert ours.returncode == 0, ours.stdout + ours.stderr
    assert "1 passed" in ours.stderr, ours.stderr


def test_command_line_paths_still_win_and_addopts_still_applies(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "-m 'not slow'")

    ours = _rustest(tree, [str(Path("tests") / "test_a.py")])

    assert ours.returncode == 0, ours.stdout + ours.stderr
    assert "1 passed, 1 deselected" in ours.stderr, ours.stderr


def test_an_unknown_flag_in_addopts_is_a_usage_error_with_the_file_named(
    tmp_path: Path,
) -> None:
    """Measured on pytest 8.4.2: exit **4**, and the message names the ini and the rootdir."""
    tree = _tree(tmp_path, "--bogus-flag")

    oracle = _pytest(tree, [])
    ours = _rustest(tree, [])

    assert oracle.returncode == 4, oracle.stdout + oracle.stderr
    assert "unrecognized arguments: --bogus-flag" in oracle.stderr, oracle.stderr

    assert ours.returncode == 4, ours.stdout + ours.stderr
    assert "unrecognized arguments: --bogus-flag" in ours.stderr, ours.stderr
    assert "inifile:" in ours.stderr and "rootdir:" in ours.stderr, ours.stderr


def test_a_removed_flag_in_addopts_is_refused_and_names_the_file(tmp_path: Path) -> None:
    """``REMOVED_FLAGS`` is scanned *after* the splice, so `addopts = --pytest-compat`
    gets rustest's own explanation rather than being swallowed as a path by ``nargs="*"``."""
    tree = _tree(tmp_path, "--pytest-compat")

    ours = _rustest(tree, [])

    assert ours.returncode == 4, ours.stdout + ours.stderr
    assert "--pytest-compat has been removed" in ours.stderr, ours.stderr
    assert "inifile:" in ours.stderr, ours.stderr


def test_an_unknown_flag_on_the_command_line_is_also_exit_4(tmp_path: Path) -> None:
    """pytest's ``UsageError`` is exit 4 wherever the flag came from; argparse's stock 2
    collided with rustest's own meaning of 2 (collection errors).

    The ``inifile:``/``rootdir:`` lines are printed here too: pytest takes them from
    ``_parser.extra_info``, which is filled as soon as rootdir is known, so they follow
    *every* usage error rather than only the ones sourced from a file. Measured.
    """
    tree = _tree(tmp_path, "-q")

    oracle = _pytest(tree, ["--bogus-flag"])
    ours = _rustest(tree, ["--bogus-flag"])

    assert oracle.returncode == 4, oracle.stdout + oracle.stderr
    assert "inifile:" in oracle.stderr and "rootdir:" in oracle.stderr, oracle.stderr
    assert ours.returncode == 4, ours.stdout + ours.stderr
    assert "unrecognized arguments: --bogus-flag" in ours.stderr, ours.stderr
    assert "inifile:" in ours.stderr and "rootdir:" in ours.stderr, ours.stderr


def test_no_addopts_means_no_change_to_argv(tmp_path: Path) -> None:
    tree = tmp_path / "plain"
    _write(tree / "pytest.ini", "[pytest]\n")
    _write(tree / "test_a.py", "def test_pass():\n    assert True\n")

    ours = _rustest(tree, [])

    assert ours.returncode == 0, ours.stdout + ours.stderr
    assert "1 passed" in ours.stderr, ours.stderr
