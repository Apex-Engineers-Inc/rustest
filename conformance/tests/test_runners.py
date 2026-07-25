from __future__ import annotations

import textwrap
from pathlib import Path

from conformance.harness.runners import (
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
