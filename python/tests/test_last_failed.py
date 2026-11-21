"""``--lf`` skips the files with no remembered failure, as pytest does.

pytest 8.4.2's ``LFPluginCollSkipfiles``: once a last-failed file yields a remembered failure
among its module-level tests, every other file is skipped without being imported, and the
failing file's other module-level tests are dropped at collection rather than deselected.
The shape that shows it -- the speed design's probe -- is a failing ``test_a`` and an
unrelated ``test_b.py`` that breaks at import after the failure was recorded: collecting
everything turns pytest's ``1 failed`` (exit 1) into a collection error (exit 2).

Two runs per runner, which the conformance corpus cannot express, so real pytest is run
here beside rustest as the oracle. Each keeps its own cache directory.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from .helpers import make_tree as _tree, run as _run

_A = "def test_x():\n    assert 0\n\n\ndef test_ok():\n    pass\n"
_B = "def test_b():\n    pass\n"
_B_BROKEN = "raise RuntimeError('broken after the last run')\n"


def _summary(output: str) -> str:
    """The counts of the last summary line, without the duration."""
    lines = [line for line in output.splitlines() if re.search(r"\d+ (failed|passed)", line)]
    return re.sub(r" in [\d.]+m?s.*$", "", lines[-1]).strip("= ")


def test_lf_skips_an_unrelated_broken_file_like_pytest(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "lf", {"test_a.py": _A, "test_b.py": _B})
    pytest = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly"]
    rustest = [sys.executable, "-m", "rustest", "-n", "1"]
    for command in (pytest, rustest):
        assert _run(command, tree).returncode == 1  # records `test_a.py::test_x`

    _ = (tree / "test_b.py").write_text(_B_BROKEN, encoding="utf-8")
    expected = _run([*pytest, "--lf"], tree)
    got = _run([*rustest, "--lf"], tree)

    assert (expected.returncode, got.returncode) == (1, 1), got.stdout + got.stderr
    assert _summary(expected.stdout) == _summary(got.stderr) == "1 failed"
    assert "ERROR collecting" not in got.stdout + got.stderr


_NESTED_HOOK = (
    "if True:\n    def pytest_collection_modifyitems(config, items):\n        items[:] = []\n"
)


@pytest.mark.parametrize(
    ("conftest", "code"),
    [(_NESTED_HOOK, 5), ("import no_such_module_xyz\n", 2)],
    ids=["nested-hook", "broken"],
)
def test_lf_still_imports_a_skipped_directorys_conftest(
    tmp_path: Path, conftest: str, code: int
) -> None:
    """pytest collects the directory of a file it skips, so its conftest still counts: a hook
    nested under ``if`` empties the run (exit 5), a broken one is a collection error (exit 2).
    """
    tree = _tree(tmp_path, "lf", {"test_a.py": _A, "sub/test_c.py": _B})
    pytest_cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly"]
    rustest = [sys.executable, "-m", "rustest", "-n", "1"]
    for command in (pytest_cmd, rustest):
        assert _run(command, tree).returncode == 1

    _ = (tree / "sub" / "conftest.py").write_text(conftest, encoding="utf-8")
    expected = _run([*pytest_cmd, "--lf"], tree)
    got = _run([*rustest, "--lf"], tree)

    assert expected.returncode == got.returncode == code, got.stdout + got.stderr
