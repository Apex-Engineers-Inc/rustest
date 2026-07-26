from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from conformance.harness.runners import (
    _check_pytest_exit,
    parse_pytest_collect,
    parse_pytest_summary,
    run_pytest,
    run_rustest,
)

COLLECT_OUTPUT = textwrap.dedent(
    """\
    test_a.py::test_one
    test_a.py::TestBox::test_two[x]

    2 tests collected in 0.01s
    """
)

MINI_SUITE = textwrap.dedent(
    """\
    def test_one():
        assert True


    def test_two():
        assert False


    class TestBox:
        def test_in_class(self):
            assert True
    """
)

MINI_IDS = {
    "test_mini.py::test_one",
    "test_mini.py::test_two",
    "test_mini.py::TestBox::test_in_class",
}


def test_parse_pytest_collect() -> None:
    assert parse_pytest_collect(COLLECT_OUTPUT) == {
        "test_a.py::test_one",
        "test_a.py::TestBox::test_two[x]",
    }


COLLECT_OUTPUT_WITH_TRACEBACK = textwrap.dedent(
    """\
    test_a.py::test_one
    test_a.py::TestBox::test_two[x]

    =================================== ERRORS ====================================
    ______________________ ERROR collecting test_broken.py ________________________
        assert path == "src::main::foo"
    E   AssertionError: mismatch bar::baz

    2 tests collected, 1 error in 0.01s
    """
)


def test_parse_pytest_collect_ignores_traceback_line_with_double_colon() -> None:
    """A traceback/source line containing ``::`` must never read as a phantom nodeid.

    Both extra lines below contain a literal ``::`` and would have slipped past the
    old heuristic (which only excluded blank lines and a fixed set of prefixes) had
    they not happened to start with one of those prefixes. They must still be
    excluded: real nodeids are always flush at column 0, and an indented source
    line or an ``E   ...`` assertion line is not.
    """
    assert parse_pytest_collect(COLLECT_OUTPUT_WITH_TRACEBACK) == {
        "test_a.py::test_one",
        "test_a.py::TestBox::test_two[x]",
    }


def test_parse_pytest_summary() -> None:
    out = parse_pytest_summary("1 failed, 2 passed, 1 skipped in 0.05s\n", exit_code=1)
    assert (out.passed, out.failed, out.skipped, out.errors) == (2, 1, 1, 0)
    assert out.exit_code == 1
    assert out.collection_error is False
    err = parse_pytest_summary("no tests ran in 0.01s\n", exit_code=2)
    assert err.collection_error is True


def test_parse_pytest_summary_counts_errors() -> None:
    singular = parse_pytest_summary("2 passed, 1 error in 0.10s\n", exit_code=1)
    assert (singular.passed, singular.failed, singular.skipped, singular.errors) == (2, 0, 0, 1)
    plural = parse_pytest_summary("1 failed, 3 errors in 0.10s\n", exit_code=1)
    assert (plural.passed, plural.failed, plural.skipped, plural.errors) == (0, 1, 0, 3)


def test_parse_pytest_summary_exit_5_is_not_collection_error() -> None:
    """Exit 5 (no tests collected) is a real outcome, distinct from the exit-2 bucket.

    ``collection_error`` is keyed off exit code 2 specifically; exit 5 must not be
    conflated with it even though both leave zero tests in the passed/failed/skipped
    buckets.
    """
    out = parse_pytest_summary("no tests ran in 0.01s\n", exit_code=5)
    assert out.exit_code == 5
    assert out.collection_error is False
    assert (out.passed, out.failed, out.skipped, out.errors) == (0, 0, 0, 0)


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr="boom")


def test_check_pytest_exit_passes_through_exit_5() -> None:
    """Exit 5 (no tests collected) is a comparable outcome, not a harness fault.

    ``pytest -m nosuchmark`` genuinely exits 5 while rustest exits 0 for the same
    invocation -- a real product divergence the grader must see, not one the harness
    should mask by raising.
    """
    _check_pytest_exit(_completed(5), "run")  # must not raise


@pytest.mark.parametrize("returncode", [3, 4])
def test_check_pytest_exit_raises_on_internal_and_usage_errors(returncode: int) -> None:
    with pytest.raises(RuntimeError, match=r"pytest run failed \(exit \d+\)"):
        _check_pytest_exit(_completed(returncode), "run")


def _write_mini_suite(root: Path) -> None:
    (root / "test_mini.py").write_text(MINI_SUITE, encoding="utf-8")


def test_run_pytest_integration(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)
    result = run_pytest(tmp_path, [])
    assert result.ids == MINI_IDS
    assert (result.outcomes.passed, result.outcomes.failed) == (2, 1)
    assert result.outcomes.errors == 0


def test_run_rustest_integration(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)
    result = run_rustest(tmp_path, [])
    assert result.ids == MINI_IDS
    assert (result.outcomes.passed, result.outcomes.failed) == (2, 1)
    assert result.outcomes.errors == 0


def test_run_pytest_ignores_surrounding_project_config(tmp_path: Path) -> None:
    """A pytest config above the case dir must not influence the run."""
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n", encoding="utf-8")
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    result = run_pytest(case_dir, [])
    assert result.ids == MINI_IDS
    assert (result.outcomes.passed, result.outcomes.failed) == (2, 1)


def test_run_pytest_accepts_relative_case_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative case dir is resolved, so ``--rootdir`` stays valid.

    ``--rootdir`` is resolved against pytest's own cwd, not the harness's, so an
    unresolved relative path used to hand pytest a nonexistent rootdir and abort
    with a usage error (exit 4) that the summary parser read as 0/0/0/0.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    monkeypatch.chdir(tmp_path)

    result = run_pytest(Path("case"), [])

    assert result.ids == MINI_IDS
    assert (result.outcomes.passed, result.outcomes.failed) == (2, 1)


def test_run_rustest_raises_when_no_report_is_written(tmp_path: Path) -> None:
    """A rustest invocation that dies before writing the report is a harness fault.

    An unrecognized flag makes the rustest CLI bail out in argparse, so no report
    file exists. Returning a fabricated all-zeros result here would grade as a
    silent divergence; the harness must surface the real failure instead. This
    drives the real CLI rather than a monkeypatched stand-in, so it stays honest
    if the failure mode changes.
    """
    _write_mini_suite(tmp_path)

    with pytest.raises(RuntimeError, match="rustest wrote no report"):
        run_rustest(tmp_path, ["--definitely-not-a-real-flag"])
