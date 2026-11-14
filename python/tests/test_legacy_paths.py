"""``request.fspath`` and ``request.node.fspath`` are ``py.path.local``, as in pytest 8.4.2."""

from __future__ import annotations

from pathlib import Path
import sys

from .helpers import make_tree as _tree, run as _run

_TEST = """\
def test_f(request):
    assert type(request.fspath).__name__ == "LocalPath", type(request.fspath)
    assert type(request.node.fspath).__name__ == "LocalPath", type(request.node.fspath)
    assert request.fspath == request.node.fspath
    assert str(request.fspath) == str(request.path)
"""


def test_fspath_is_a_legacy_path_on_both_runners(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "fs", {"test_f.py": _TEST})
    for command in (
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
        [sys.executable, "-m", "rustest", "-n", "1", "--no-pytest-fallback"],
    ):
        result = _run(command, tree)
        assert result.returncode == 0, result.stdout + result.stderr
