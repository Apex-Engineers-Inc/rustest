from __future__ import annotations

from pathlib import Path

from conformance.harness.ids import normalize_pytest_nodeid, normalize_rustest_id


def test_normalize_pytest_nodeid_drops_class_and_posixifies() -> None:
    assert (
        normalize_pytest_nodeid("tests\\test_a.py::TestX::test_y[1-2]")
        == "tests/test_a.py::test_y[1-2]"
    )
    assert normalize_pytest_nodeid("test_a.py::test_y") == "test_a.py::test_y"


def test_normalize_rustest_id_relativizes(tmp_path: Path) -> None:
    abs_id = str(tmp_path / "test_a.py") + "::test_y[1]"
    assert normalize_rustest_id(abs_id, tmp_path) == "test_a.py::test_y[1]"
