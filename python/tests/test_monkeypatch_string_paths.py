"""The string forms of ``monkeypatch.setattr`` / ``delattr`` resolve dotted class paths."""

from __future__ import annotations

from pathlib import Path

from rustest import run


def _run_module(tmp_path: Path, source: str):
    (tmp_path / "test_string_paths.py").write_text(source)
    return run(paths=[str(tmp_path)])


def test_setattr_string_path_to_class_attribute_is_undone(tmp_path: Path) -> None:
    report = _run_module(
        tmp_path,
        """
from pathlib import Path

_ORIGINAL_HOME = Path.home


def test_a_patches(monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: "patched")
    assert Path.home() == "patched"


def test_b_restored():
    assert Path.home == _ORIGINAL_HOME
""",
    )

    assert report.total == 2
    assert report.passed == 2


def test_delattr_string_path_to_class_attribute_is_undone(tmp_path: Path) -> None:
    report = _run_module(
        tmp_path,
        """
from pathlib import Path

_ORIGINAL_HOME = Path.__dict__["home"]


def test_a_deletes(monkeypatch):
    monkeypatch.delattr("pathlib.Path.home")
    assert "home" not in Path.__dict__


def test_b_restored():
    assert Path.__dict__["home"] is _ORIGINAL_HOME
""",
    )

    assert report.total == 2
    assert report.passed == 2


def test_string_path_through_package_and_nested_class(tmp_path: Path) -> None:
    pkg = tmp_path / "mp_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "inner.py").write_text(
        """
class Outer:
    class Nested:
        label = "orig"

        @classmethod
        def build(cls):
            return "orig-build"
"""
    )
    report = _run_module(
        tmp_path,
        """
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from mp_pkg.inner import Outer

_RAW_BUILD = Outer.Nested.__dict__["build"]


def test_a_patches(monkeypatch):
    monkeypatch.setattr("mp_pkg.inner.Outer.Nested.label", "patched")
    monkeypatch.setattr("mp_pkg.inner.Outer.Nested.build", lambda: "patched-build")
    assert Outer.Nested.label == "patched"
    assert Outer.Nested.build() == "patched-build"


def test_b_restored():
    assert Outer.Nested.label == "orig"
    assert Outer.Nested.__dict__["build"] is _RAW_BUILD
    assert Outer.Nested.build() == "orig-build"
""",
    )

    assert report.total == 2
    assert report.passed == 2


def test_module_attribute_string_form_and_missing_targets(tmp_path: Path) -> None:
    report = _run_module(
        tmp_path,
        """
import os.path
import pytest


def test_module_attr_still_patches(monkeypatch):
    monkeypatch.setattr("os.path.sep", "|")
    assert os.path.sep == "|"
    monkeypatch.delattr("os.path.extsep")
    assert not hasattr(os.path, "extsep")


def test_missing_final_attribute_respects_raising(monkeypatch):
    with pytest.raises(AttributeError):
        monkeypatch.setattr("os.path.no_such_attr", 1)
    with pytest.raises(AttributeError):
        monkeypatch.delattr("os.path.no_such_attr")
    monkeypatch.setattr("os.path.no_such_attr", 1, raising=False)
    assert os.path.no_such_attr == 1
    monkeypatch.delattr("os.path.no_such_other", raising=False)


def test_missing_class_attribute_respects_raising(monkeypatch):
    with pytest.raises(AttributeError):
        monkeypatch.setattr("pathlib.Path.no_such_attr", 1)
    monkeypatch.delattr("pathlib.Path.no_such_attr", raising=False)


def test_unresolvable_path_gives_clear_error(monkeypatch):
    with pytest.raises(ImportError, match="no_such_module_xyz"):
        monkeypatch.setattr("no_such_module_xyz.attr", 1)
    with pytest.raises(AttributeError, match="no_such_member"):
        monkeypatch.setattr("pathlib.no_such_member.attr", 1)
    with pytest.raises(ImportError, match="no_such_module_xyz"):
        monkeypatch.delattr("no_such_module_xyz.attr", raising=False)
""",
    )

    assert report.total == 4
    assert report.passed == 4
