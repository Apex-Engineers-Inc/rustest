"""``filterwarnings``, the warnings summary and ``xfail_strict``, against pytest 8.4.2.

The outcome rules (which phase an ``error`` filter fails, mark precedence, collection
errors, ``xfail_strict``) are graded by the conformance corpus: ``marks/filterwarnings-error``,
``marks/xfail-strict-ini`` and ``collection/filterwarnings-collect``. What is here is what
the corpus does not grade: the filter parser, and the ``warnings summary`` text and count.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

import pytest

from rustest._worker import parse_warning_filter
from rustest.core import _warning_groups  # pyright: ignore[reportPrivateUsage]

from .helpers import make_tree as _tree, run as _run


def test_the_filter_parser_is_pytests() -> None:
    assert parse_warning_filter("error") == ("error", "", Warning, "", 0)
    # An action may be any prefix of one (`warnings._getaction`), and fields are stripped.
    assert parse_warning_filter(" ig : msg : DeprecationWarning : mod : 3 ") == (
        "ignore",
        "msg",
        DeprecationWarning,
        "mod",
        3,
    )
    assert parse_warning_filter("default:x:pytest.PytestWarning")[2].__name__ == "PytestWarning"
    for bad in ("bogus", "a:b:c:d:e:f", "error::NotAWarningAtAll", "error::int", "error:::: -1"):
        with pytest.raises(Exception):
            _ = parse_warning_filter(bad)


def test_warnings_are_grouped_by_message_and_collapsed_from_ten() -> None:
    few = [{"location": f"t.py::test_{i}", "message": "m\n"} for i in range(3)]
    many = [{"location": f"u.py::test_{i}", "message": "n\n"} for i in range(10)]
    report = {
        "warnings": [{"location": "c.py:1", "message": "c\n"}],
        "tests": [{"warnings": few}, {}, {"warnings": many}],
    }
    groups = _warning_groups(report)  # pyright: ignore[reportArgumentType]
    assert groups == [
        ("c.py:1", "c\n"),
        ("t.py::test_0\nt.py::test_1\nt.py::test_2", "m\n"),
        ("u.py: 10 warnings", "n\n"),
    ]


_WARNS = """\
import warnings

warnings.warn("at import", UserWarning)


class TestInit:
    def __init__(self):
        pass

    def test_x(self):
        pass


def test_dep():
    warnings.warn("old api", DeprecationWarning)


def test_ret():
    return 3
"""


def _section(output: str) -> list[str]:
    """The warnings summary, between its rule and the docs line, paths made relative."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if "warnings summary" in line)
    end = next(i for i, line in enumerate(lines) if line.startswith("-- Docs:"))
    # The return-not-None warning is raised from each runner's own source file.
    return [
        re.sub(
            r"^  \S+?:\d+: PytestReturnNotNoneWarning",
            "  <runner>: PytestReturnNotNoneWarning",
            line,
        )
        for line in lines[start + 1 : end]
        if not line.startswith("    warnings.warn(")
    ]


def test_the_warnings_summary_and_count_are_pytests(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "w", {"test_w.py": _WARNS})
    expected = _run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider"], tree
    )
    got = _run([sys.executable, "-m", "rustest", "-n", "1", "--no-pytest-fallback"], tree)

    assert got.returncode == expected.returncode == 0, got.stdout + got.stderr
    assert _section(got.stdout) == _section(expected.stdout)
    assert re.search(r"2 passed, 4 warnings in ", got.stderr), got.stderr
    assert "2 passed, 4 warnings in " in expected.stdout


def test_an_error_filter_is_undone_before_the_worker_exits(tmp_path: Path) -> None:
    """The session's filters end at ``shutdown``, as pytest's ``config.add_cleanup`` ends
    them: under ``error``, the interpreter's own exit-time ``ResourceWarning``s would
    otherwise be printed as ignored exceptions."""
    tree = _tree(tmp_path, "w", {"test_ok.py": "def test_ok():\n    print('x')\n"})
    _ = (tree / "pytest.ini").write_text("[pytest]\nfilterwarnings = error\n", encoding="utf-8")
    got = _run([sys.executable, "-m", "rustest", "-n", "1", "--no-pytest-fallback"], tree)
    assert got.returncode == 0, got.stderr
    assert "Exception ignored" not in got.stderr, got.stderr
