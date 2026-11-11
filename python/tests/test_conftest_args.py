"""A conftest's own command-line options (``rustest --runslow``, ``--db-url x tests/``) run
natively: the CLI forwards what it does not know, the worker parses it against the options the
initial conftests declared (pytest's second argparse pass), and whatever is left over goes to
pytest. Every case is compared against real pytest on the same tree and arguments."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .helpers import clean_env, make_tree, run

_CONFTEST = """
import pytest


def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", default=False)
    parser.addoption("--db-url", dest="url", default="sqlite://")
    parser.addoption("--level", type=int, default=1, choices=[1, 2, 3])
    parser.addoption("--tag", action="append", default=[])
    parser.addoption("--n-items", type=int, nargs=2, default=[0, 0])
    parser.addoption("--multi", nargs="+", default=[])


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return
    skip = pytest.mark.skip(reason="need --runslow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
"""

_TESTS = """
import os
import pytest


def test_fast(request):
    config = request.config
    seen = [config.getoption(name) for name in ("url", "--level", "--tag", "--n-items", "--multi")]
    with open(os.path.join(os.path.dirname(__file__), "..", "seen.txt"), "a") as handle:
        handle.write(repr(seen) + "\\n")


@pytest.mark.slow
def test_slow():
    pass
"""


def _tree(tmp_path: Path) -> Path:
    return make_tree(
        tmp_path,
        "opts",
        {
            "pytest.ini": "[pytest]\nmarkers =\n    slow: slow\n",
            "conftest.py": _CONFTEST,
            "tests/test_opts.py": _TESTS,
            "sub/conftest.py": (
                "def pytest_addoption(parser):\n    parser.addoption('--late', action='store_true')\n"
            ),
            "sub/test_sub.py": "def test_sub():\n    pass\n",
        },
    )


def _testpaths_tree(tmp_path: Path) -> Path:
    """`testpaths` reaches a conftest only pytest's first pass would make initial."""
    return make_tree(
        tmp_path,
        "testpaths",
        {
            "pytest.ini": "[pytest]\ntestpaths = src/pkg/tests\n",
            "src/pkg/tests/conftest.py": "def pytest_addoption(parser):\n"
            "    parser.addoption('--db-url', default='sqlite://')\n",
            "src/pkg/tests/test_t.py": "def test_t():\n    pass\n",
        },
    )


def _both(tree: Path, args: list[str]) -> tuple[tuple[int, str], tuple[int, str, str]]:
    seen = tree / "seen.txt"
    seen.unlink(missing_ok=True)
    oracle = run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args], tree, clean_env()
    )
    pytest_seen = seen.read_text(encoding="utf-8") if seen.exists() else ""
    seen.unlink(missing_ok=True)
    ours = run([sys.executable, "-m", "rustest", "-n", "2", *args], tree, clean_env())
    ours_seen = seen.read_text(encoding="utf-8") if seen.exists() else ""
    assert pytest_seen == ours_seen, (pytest_seen, ours_seen, ours.stderr)
    return (oracle.returncode, oracle.stdout), (ours.returncode, ours.stdout, ours.stderr)


@pytest.mark.parametrize(
    ("args", "summary"),
    [
        (["--runslow"], "3 passed"),
        (["--db-url", "postgres://x", "tests/"], "1 passed, 1 skipped"),
        (["tests/", "--db-url", "postgres://x"], "1 passed, 1 skipped"),
        (["--db-url=postgres://x", "--level", "3", "--tag", "a", "--tag=b", "tests"], "1 skipped"),
        (["--runslow", "tests/"], "2 passed"),
        (["tests/", "--n-items", "1", "2"], "1 passed, 1 skipped"),
        (["--multi", "a", "b", "c"], "2 passed, 1 skipped"),
    ],
    ids=[
        "store_true",
        "value-then-path",
        "path-then-value",
        "equals-int-append",
        "flag-then-path",
        "nargs-2",
        "nargs-plus",
    ],
)
def test_declared_options_on_the_command_line_run_natively(
    tmp_path: Path, args: list[str], summary: str
) -> None:
    tree = _tree(tmp_path)

    (code, out), (ours_code, _, ours_err) = _both(tree, args)

    assert code == ours_code == 0, ours_err
    assert summary in out and summary in ours_err
    assert "handing this session to pytest" not in ours_err


@pytest.mark.parametrize(
    "args",
    [
        ["--runslwo"],  # a typo: pytest's own usage error
        ["--runslow", "nowhere"],  # a flag, then a word pytest reads as a missing path
        ["--level", "7"],  # not one of the choices
        ["--late"],  # declared by a conftest pytest loads after argparse
        ["--db-url", "tests"],  # an existing path that pytest reads as the value
        ["--multi", "a", "tests/"],  # nargs="+" takes the path too, and pytest collects all
    ],
    ids=[
        "typo",
        "flag-then-word",
        "bad-choice",
        "late-declaration",
        "value-is-a-path",
        "nargs-plus-takes-a-path",
    ],
)
def test_what_rustest_cannot_parse_is_pytests_answer(tmp_path: Path, args: list[str]) -> None:
    tree = _tree(tmp_path)

    (code, _), (ours_code, _, ours_err) = _both(tree, args)

    assert ours_code == code, ours_err
    assert "handing this session to pytest" in ours_err


def test_a_value_word_is_a_first_pass_positional_and_drops_testpaths(tmp_path: Path) -> None:
    """pytest's first pass reads `x` in `--db-url x` as a path, so `testpaths` does not choose
    the initial conftests and the one declaring `--db-url` is not loaded in time: pytest's own
    usage error (exit 4), where `--db-url=x` runs (review M6)."""
    tree = _testpaths_tree(tmp_path)

    (code, _), (ours_code, _, ours_err) = _both(tree, ["--db-url", "x"])
    assert (code, ours_code) == (4, 4), ours_err

    (code, _), (ours_code, _, ours_err) = _both(tree, ["--db-url=x"])
    assert (code, ours_code) == (0, 0), ours_err
    assert "handing this session to pytest" not in ours_err
