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


def test_parse_pytest_collect() -> None:
    assert parse_pytest_collect(COLLECT_OUTPUT) == {
        "test_a.py::test_one",
        "test_a.py::TestBox::test_two[x]",
    }


def test_parse_pytest_summary() -> None:
    out = parse_pytest_summary("1 failed, 2 passed, 1 skipped in 0.05s\n", exit_code=1)
    assert (out.passed, out.failed, out.skipped) == (2, 1, 1)
    assert out.exit_code == 1
    assert out.collection_error is False
    err = parse_pytest_summary("no tests ran in 0.01s\n", exit_code=2)
    assert err.collection_error is True


def _write_mini_suite(root: Path) -> None:
    (root / "test_mini.py").write_text(
        "def test_one():\n    assert True\n\n\ndef test_two():\n    assert False\n",
        encoding="utf-8",
    )


def test_run_pytest_integration(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)
    result = run_pytest(tmp_path, [])
    assert result.ids == {"test_mini.py::test_one", "test_mini.py::test_two"}
    assert (result.outcomes.passed, result.outcomes.failed) == (1, 1)


def test_run_rustest_integration(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)
    result = run_rustest(tmp_path, [])
    assert result.ids == {"test_mini.py::test_one", "test_mini.py::test_two"}
    assert (result.outcomes.passed, result.outcomes.failed) == (1, 1)
