from __future__ import annotations

from pathlib import Path

from conformance.harness.ids import normalize_pytest_nodeid, normalize_rustest_id


def test_normalize_pytest_nodeid_posixifies_and_keeps_class() -> None:
    assert (
        normalize_pytest_nodeid("tests\\test_a.py::TestX::test_y[1-2]")
        == "tests/test_a.py::TestX::test_y[1-2]"
    )
    assert normalize_pytest_nodeid("test_a.py::test_y") == "test_a.py::test_y"


def test_normalize_pytest_nodeid_keeps_distinct_classes_distinct() -> None:
    assert normalize_pytest_nodeid("f.py::TestA::test_x") != normalize_pytest_nodeid(
        "f.py::TestB::test_x"
    )


def test_normalize_rustest_id_relativizes(tmp_path: Path) -> None:
    abs_id = str(tmp_path / "test_a.py") + "::test_y[1]"
    assert normalize_rustest_id(abs_id, tmp_path) == "test_a.py::test_y[1]"


def test_normalize_rustest_id_keeps_class_segment(tmp_path: Path) -> None:
    assert (
        normalize_rustest_id("sub\\test_nested.py::TestBox::test_in_class", tmp_path)
        == "sub/test_nested.py::TestBox::test_in_class"
    )
