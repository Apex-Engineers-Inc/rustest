"""The ``rustest --v2-collect-only`` CLI surface, diffed against REAL pytest.

This is the first user-reachable v2 surface: it runs the whole v2 spine (config -> walk
-> worker pool -> manifest) and prints the manifest's node ids, one per line, in manifest
order. The shape is pytest's ``--collect-only -q`` shape, and the tests below prove that
by running **real pytest** on the same tree in a subprocess and comparing byte for byte.

Two deliberate departures from pytest's own ``-q`` output, both so that stdout is a pure
machine-readable id list:

* the ``N tests collected`` summary goes to **stderr** (pytest puts it on stdout);
* collection errors are reported on **stderr** as ``ERROR collecting <path>`` plus the
  indented message (pytest prints a full ``ERRORS`` section on stdout).

Exit codes are pytest's, and they were **probed** rather than remembered (pytest 8.4.2,
``--collect-only -q``): tests collected -> 0; nothing collected -> 5; any collection error
-> 2 (pytest's ``Interrupted``, even when other files collected fine); a path argument that
does not exist -> 4 with ``ERROR: file or directory not found: ...`` on stderr.

**Why the trees are isolated.** Every layout gets its own ``pytest.ini``. Without one, both
runners walk *out* of ``tmp_path`` looking for a config file and land on this repository's
``pyproject.toml`` (it has ``[tool.pytest.ini_options]``), which makes rootdir the repo root
and every node id repo-relative. With a local ini both agree that rootdir is the tree, and
the comparison is about collection rather than about rootdir discovery.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

# The compiled extension is built and installed by `python/tests/__init__.py`, which runs
# `ensure_develop_installed()` before any test module is imported -- these subprocess tests
# exercise the real `rust.v2_collect`, not the pure-Python fallback stub.
from .helpers import stub_rust_module
from rustest import cli, core

PLAIN_MODULE = """\
def test_one():
    pass


class TestBox:
    def test_method(self):
        pass
"""

PARAMETRIZED_MODULE = """\
import pytest


@pytest.mark.parametrize("x", [1, 2])
def test_p(x):
    pass
"""

# Deliberately unparseable: the exception arrives at *import* time, which is the path that
# turns into a manifest error entry rather than an orchestration failure.
BROKEN_MODULE = "def test_x(:\n    pass\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _clean_env() -> dict[str, str]:
    """A child environment with the ambient pytest/rustest session stripped out."""
    env = dict(os.environ)
    for leak in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTEST_CURRENT_TEST",
        "RUSTEST_RUNNING",
    ):
        _ = env.pop(leak, None)
    return env


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_clean_env(),
        check=False,
    )


def _run_v2(cwd: Path, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, "-m", "rustest", "--v2-collect-only", *(args or [])], cwd)


def _run_pytest(cwd: Path, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *(args or []),
        ],
        cwd,
    )


def _pytest_nodeids(stdout: str) -> list[str]:
    """The node id block of ``pytest --collect-only -q``: everything before the first blank.

    pytest follows the ids with a blank line and then either its summary line or an
    ``ERRORS`` section, so the blank line is the terminator in both shapes.
    """
    ids: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            break
        ids.append(line)
    return ids


def _context(label: str, proc: subprocess.CompletedProcess[str]) -> str:
    return f"--- {label} rc={proc.returncode} ---\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def _assert_agrees_with_pytest(tree: Path, args: list[str] | None = None) -> list[str]:
    """Run both collectors on the same tree from the same cwd; diff ids and exit code."""
    oracle = _run_pytest(tree, args)
    ours = _run_v2(tree, args)
    where = f"tree={tree} args={args}\n{_context('pytest', oracle)}\n{_context('v2', ours)}"

    expected = _pytest_nodeids(oracle.stdout)
    actual = ours.stdout.splitlines()
    assert actual == expected, f"node ids diverge\n{where}"
    assert ours.returncode == oracle.returncode, f"exit codes diverge\n{where}"
    return actual


# --------------------------------------------------------------------------------------
# The differential: v2's node ids vs. real pytest's, on the same tree
# --------------------------------------------------------------------------------------


def _mini_suite(tmp_path: Path) -> Path:
    """A tree exercising ordering, classes, parametrization and a non-test module."""
    tree = tmp_path / "mini"
    _write(tree / "pytest.ini", "[pytest]\n")
    _write(tree / "test_a.py", PLAIN_MODULE)
    _write(tree / "sub" / "test_b.py", PARAMETRIZED_MODULE)
    # Neither runner should collect this: it matches no `python_files` pattern.
    _write(tree / "helper.py", "def test_not_collected():\n    pass\n")
    return tree


def test_nodeids_are_byte_equal_to_real_pytest(tmp_path: Path) -> None:
    ids = _assert_agrees_with_pytest(_mini_suite(tmp_path))

    # Pin the interesting properties explicitly, so a future regression that makes *both*
    # runners wrong in the same way still fails here.
    assert ids == [
        "sub/test_b.py::test_p[1]",
        "sub/test_b.py::test_p[2]",
        "test_a.py::test_one",
        "test_a.py::TestBox::test_method",
    ], ids


def test_summary_goes_to_stderr_and_stdout_holds_only_nodeids(tmp_path: Path) -> None:
    proc = _run_v2(_mini_suite(tmp_path))

    assert proc.returncode == 0, _context("v2", proc)
    assert "4 tests collected" in proc.stderr, _context("v2", proc)
    # stdout must stay parseable as a bare id list: no summary, no banner, no blank lines.
    assert all(line.strip() for line in proc.stdout.splitlines()), _context("v2", proc)
    assert "collected" not in proc.stdout, _context("v2", proc)


def test_explicit_file_argument_matches_pytest(tmp_path: Path) -> None:
    tree = _mini_suite(tmp_path)

    ids = _assert_agrees_with_pytest(tree, ["test_a.py"])

    assert ids == ["test_a.py::test_one", "test_a.py::TestBox::test_method"], ids


def test_testpaths_decide_the_roots_when_no_argument_is_given(tmp_path: Path) -> None:
    """No path argument means pytest's ``testpaths`` choose the roots -- so it must here."""
    tree = tmp_path / "paths"
    _write(tree / "pytest.ini", "[pytest]\ntestpaths = suite\n")
    _write(tree / "suite" / "test_in.py", "def test_in():\n    pass\n")
    _write(tree / "outside" / "test_out.py", "def test_out():\n    pass\n")

    ids = _assert_agrees_with_pytest(tree)

    assert ids == ["suite/test_in.py::test_in"], ids


# --------------------------------------------------------------------------------------
# Exit codes -- the v2 exit-code contract's first beachhead
# --------------------------------------------------------------------------------------


def test_empty_tree_exits_5(tmp_path: Path) -> None:
    tree = tmp_path / "empty"
    _write(tree / "pytest.ini", "[pytest]\n")

    ours = _run_v2(tree)
    oracle = _run_pytest(tree)

    assert ours.returncode == 5, _context("v2", ours)
    assert oracle.returncode == 5, _context("pytest", oracle)
    assert ours.stdout == "", _context("v2", ours)
    assert "0 tests collected" in ours.stderr, _context("v2", ours)


def test_collection_error_exits_2_and_names_the_file(tmp_path: Path) -> None:
    tree = tmp_path / "broken"
    _write(tree / "pytest.ini", "[pytest]\n")
    _write(tree / "test_bad.py", BROKEN_MODULE)
    _write(tree / "test_ok.py", "def test_ok():\n    pass\n")

    ours = _run_v2(tree)
    oracle = _run_pytest(tree)

    assert ours.returncode == 2, _context("v2", ours)
    assert oracle.returncode == 2, _context("pytest", oracle)
    # The healthy file is still collected and still printed -- pytest does the same.
    assert ours.stdout.splitlines() == ["test_ok.py::test_ok"], _context("v2", ours)
    assert "ERROR collecting test_bad.py" in ours.stderr, _context("v2", ours)
    assert "SyntaxError" in ours.stderr, _context("v2", ours)
    assert "1 test collected, 1 error" in ours.stderr, _context("v2", ours)


def test_missing_path_argument_is_a_usage_error(tmp_path: Path) -> None:
    tree = tmp_path / "usage"
    _write(tree / "pytest.ini", "[pytest]\n")

    ours = _run_v2(tree, ["nope"])
    oracle = _run_pytest(tree, ["nope"])

    # pytest raises UsageError here, which is exit 4 -- not 2 and not 5.
    assert ours.returncode == 4, _context("v2", ours)
    assert oracle.returncode == 4, _context("pytest", oracle)
    assert "ERROR: file or directory not found: nope" in ours.stderr, _context("v2", ours)
    assert ours.stdout == "", _context("v2", ours)


# --------------------------------------------------------------------------------------
# Routing: the v2 flag must never touch the v1 path
# --------------------------------------------------------------------------------------


def test_flag_short_circuits_before_v1_run() -> None:
    """``--v2-collect-only`` returns before v1 discovery, so ``core.run`` is never called."""
    with (
        patch("rustest.cli.run") as v1_run,
        patch("rustest.cli.v2_collect_only", return_value=5) as v2,
    ):
        assert cli.main(["--v2-collect-only"]) == 5

    v1_run.assert_not_called()
    assert v2.call_count == 1


def test_v1_invocation_never_reaches_the_v2_path() -> None:
    with (
        patch("rustest.cli.run") as v1_run,
        patch("rustest.cli.v2_collect_only") as v2,
    ):
        v1_run.return_value.collection_errors = ()
        v1_run.return_value.failed = 0
        assert cli.main([]) == 0

    v2.assert_not_called()
    assert v1_run.call_count == 1


def test_absent_path_arguments_are_passed_through_as_none_given() -> None:
    """An omitted path is not ``.``: pytest lets ``testpaths`` decide only when *no* arg is
    given, so the default must not be forwarded as a real argument."""
    seen: list[list[str]] = []

    def fake_collect(invocation_dir: str, args: list[str], python: str, workers: int) -> str:
        del invocation_dir, python, workers
        seen.append(list(args))
        return '{"schema_version":2,"rootdir":"/x","tests":[]}'

    with stub_rust_module(v2_collect=fake_collect):
        _ = cli.main(["--v2-collect-only"])
        _ = cli.main(["--v2-collect-only", "."])

    assert seen == [[], ["."]]


def test_core_passes_sys_executable_to_the_worker_pool() -> None:
    """The interpreter is resolved on the Python side; Rust never guesses one."""
    seen: list[str] = []

    def fake_collect(invocation_dir: str, args: list[str], python: str, workers: int) -> str:
        del invocation_dir, args
        seen.append(python)
        assert workers >= 1
        return '{"schema_version":2,"rootdir":"/x","tests":[]}'

    with stub_rust_module(v2_collect=fake_collect):
        assert core.v2_collect_only(paths=[], workers=None) == 5

    assert seen == [sys.executable]
