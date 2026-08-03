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

import pytest

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


def _run_bytes(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Like :func:`_run` but undecoded, for comparisons that are about *bytes*."""
    return subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
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


def test_unencodable_nodeids_are_escaped_exactly_as_pytest_escapes_them(tmp_path: Path) -> None:
    """A CJK test name must not crash the process, and must print pytest's bytes.

    ``def test_測試():`` is legal Python, so node ids are not ASCII by construction. On a
    redirected Windows stdout (cp1252 here) a bare ``print`` of that id raises
    ``UnicodeEncodeError`` -- the process would die with a traceback and exit 1, escaping
    the whole exit-code contract. Probed on both runners before the fix: pytest emitted
    ``test_u.py::test_\\u6e2c\\u8a66`` and exited 0; v2 exited 1 with a truncated stdout.

    The comparison is on **raw bytes** on purpose. Decoding both sides would normalise away
    the very difference being tested -- whether the escape happened at all.
    """
    tree = tmp_path / "unicode"
    _write(tree / "pytest.ini", "[pytest]\n")
    _write(tree / "test_u.py", "def test_ascii():\n    pass\n\n\ndef test_測試():\n    pass\n")

    oracle = _run_bytes(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], tree
    )
    ours = _run_bytes([sys.executable, "-m", "rustest", "--v2-collect-only"], tree)

    where = (
        f"pytest rc={oracle.returncode} {oracle.stdout!r}\nv2 rc={ours.returncode} {ours.stdout!r}"
    )
    assert ours.returncode == 0, where
    assert oracle.returncode == 0, where
    # pytest's id block ends at the first blank line; splitting on a literal b"\n\n" would
    # miss it, because the child's newlines are CRLF here.
    expected: list[bytes] = []
    for line in oracle.stdout.splitlines():
        if not line.strip():
            break
        expected.append(line)
    assert ours.stdout.splitlines() == expected, where
    # ...and the name really reached the output, so the test cannot pass by both sides
    # dropping or truncating it.
    #
    # **Which form is a property of the platform, not of either runner.** The escape happens
    # only when the child's redirected stdout cannot encode the characters -- cp1252 on
    # Windows, where pytest emits `test_測試`. On a UTF-8 stdout (Linux, macOS) there
    # is nothing to escape and both runners print the id raw, which is why pinning only the
    # escaped spelling failed on the first Linux CI run this project ever had. Accepting
    # either keeps the guard's teeth -- an id that vanished matches neither -- while the
    # byte-for-byte agreement above remains the actual contract.
    escaped = b"\\u6e2c\\u8a66"
    raw = "測試".encode()
    assert any(escaped in line or raw in line for line in expected), where


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


def test_deselecting_everything_exits_5_and_says_how_many(tmp_path: Path) -> None:
    """The exit-5 branch counts what is left **after** selection, which is the whole reason
    the manifest carries a ``deselected`` field.

    Probed: pytest's ``--collect-only -q -m nosuch`` prints ``1 deselected`` and exits 5, not
    0 -- so a v2 that ignored the option (as it did before 1b.2) reported exit 0 for a run
    pytest calls empty. This is the case ``marks/deselect-all`` waives in the v2-collect
    gate.
    """
    tree = tmp_path / "deselect"
    _write(tree / "pytest.ini", "[pytest]\nmarkers =\n    smoke\n")
    _write(
        tree / "test_marks.py",
        "import pytest\n\n\n@pytest.mark.smoke()\ndef test_smoke_only():\n    pass\n",
    )

    for args in (["-m", "nosuchmark"], ["-k", "nosuchname"]):
        ours = _run_v2(tree, args)
        oracle = _run_pytest(tree, args)

        assert ours.returncode == 5, _context("v2", ours)
        assert oracle.returncode == 5, _context("pytest", oracle)
        assert ours.stdout == "", _context("v2", ours)
        assert "0 tests collected, 1 deselected" in ours.stderr, _context("v2", ours)


def test_selection_does_not_suppress_a_collection_error(tmp_path: Path) -> None:
    """Exit 2 outranks exit 5: ``-k`` runs after collection, so deselecting every surviving
    test cannot hide a file that failed to import."""
    tree = tmp_path / "selbroken"
    _write(tree / "pytest.ini", "[pytest]\n")
    _write(tree / "test_bad.py", BROKEN_MODULE)
    _write(tree / "test_ok.py", "def test_ok():\n    pass\n")

    ours = _run_v2(tree, ["-k", "nomatch"])
    oracle = _run_pytest(tree, ["-k", "nomatch"])

    assert ours.returncode == 2, _context("v2", ours)
    assert oracle.returncode == 2, _context("pytest", oracle)
    assert "ERROR collecting test_bad.py" in ours.stderr, _context("v2", ours)


def test_orchestration_failure_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    """A pool that fails is exit 3 (pytest's INTERNAL_ERROR), never a quiet empty collect.

    Reaching this branch for real needs a broken worker pool, so the failure is injected at
    the boundary instead: the Rust side raises ``RuntimeError`` for exactly this class of
    error (pinned on that side by ``v2::py::tests::an_unspawnable_interpreter_is_a_runtime_error``),
    and this pins what the CLI does with it.
    """

    def boom(
        invocation_dir: str,
        args: list[str],
        python: str,
        workers: int,
        keyword: str | None,
        mark_expr: str | None,
        codeblocks: bool,
        collect_tier: str,
        cache_mode: str,
    ) -> str:
        del invocation_dir, args, python, workers, keyword, mark_expr
        del codeblocks, collect_tier, cache_mode
        raise RuntimeError("could not spawn the collection worker `nope -m rustest._v2_worker`")

    with stub_rust_module(v2_collect=boom):
        assert core.v2_collect_only(paths=[], workers=1) == 3

    captured = capsys.readouterr()
    assert captured.out == "", captured
    assert captured.err.startswith("INTERNALERROR: could not spawn"), captured


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
# Routing: collect-only must never execute anything
# --------------------------------------------------------------------------------------


def test_collect_only_never_reaches_the_run_path() -> None:
    """``--v2-collect-only`` returns before the runner, so ``core.v2_run`` is never called.

    The negative half is the load-bearing one. "Collect and print the ids" and "run the
    suite" are answered by different functions, and a router that called both would satisfy
    an assertion on the collect call alone while executing every test -- which is exactly
    the wrong answer to "do not run anything", and the reason a user pointing this at a
    suite with side effects would find out afterwards.
    """
    with (
        patch("rustest.cli.v2_run") as run_path,
        patch("rustest.cli.v2_collect_only", return_value=5) as collect,
    ):
        assert cli.main(["--v2-collect-only"]) == 5

    run_path.assert_not_called()
    assert collect.call_count == 1


def test_a_bare_invocation_runs_rather_than_collects() -> None:
    """...and the converse: no mode flag means execute, and collect-only is not reached."""
    with (
        patch("rustest.cli.v2_collect_only") as collect,
        patch("rustest.cli.v2_run", return_value=0) as run_path,
    ):
        assert cli.main([]) == 0

    collect.assert_not_called()
    assert run_path.call_count == 1


def test_absent_path_arguments_are_passed_through_as_none_given() -> None:
    """An omitted path is not ``.``: pytest lets ``testpaths`` decide only when *no* arg is
    given, so the default must not be forwarded as a real argument."""
    seen: list[list[str]] = []

    def fake_collect(
        invocation_dir: str,
        args: list[str],
        python: str,
        workers: int,
        keyword: str | None,
        mark_expr: str | None,
        codeblocks: bool,
        collect_tier: str,
        cache_mode: str,
    ) -> str:
        del invocation_dir, python, workers, keyword, mark_expr
        del codeblocks, collect_tier, cache_mode
        seen.append(list(args))
        return '{"schema_version":2,"rootdir":"/x","tests":[]}'

    with stub_rust_module(v2_collect=fake_collect):
        _ = cli.main(["--v2-collect-only"])
        _ = cli.main(["--v2-collect-only", "."])

    assert seen == [[], ["."]]


def test_core_passes_sys_executable_to_the_worker_pool() -> None:
    """The interpreter is resolved on the Python side; Rust never guesses one."""
    seen: list[str] = []

    def fake_collect(
        invocation_dir: str,
        args: list[str],
        python: str,
        workers: int,
        keyword: str | None,
        mark_expr: str | None,
        codeblocks: bool,
        collect_tier: str,
        cache_mode: str,
    ) -> str:
        del invocation_dir, args, keyword, mark_expr, codeblocks, collect_tier, cache_mode
        seen.append(python)
        assert workers >= 1
        return '{"schema_version":2,"rootdir":"/x","tests":[]}'

    with stub_rust_module(v2_collect=fake_collect):
        assert core.v2_collect_only(paths=[], workers=None) == 5

    assert seen == [sys.executable]
