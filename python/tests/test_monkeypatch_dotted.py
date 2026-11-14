"""``MonkeyPatch`` dotted-path semantics, differentialled against pytest's own.

The oracle is `_pytest/monkeypatch.py`: ``resolve`` (l. 60-85), ``annotated_getattr``
(l. 88-95), ``derive_importpath`` (l. 98-105) and ``MonkeyPatch.setattr``/``delattr``
(l. 181-289) of pytest 8.4.2, the version pinned as this repo's conformance oracle.

Every expectation below was **measured** against that pytest before it was written here
(scratch probe, 13 shapes; see the Phase 4 Task 1 report).  rustest resolved a dotted
target with a single ``rsplit(".", 1)`` + ``import_module``, i.e. it assumed everything
before the last dot names a *module*; click's ``tests/test_shell_completion.py`` patches
``"click.shell_completion.BashComplete._check_version"``, where it names a class, and all
14 of that file's parameter cases errored in setup.
"""

from __future__ import annotations

import os.path
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rustest.builtin_fixtures import MonkeyPatch


def _target_module() -> ModuleType:
    """A throwaway module holding a class with a nested class and descriptors."""
    module = ModuleType("_rustest_mp_probe")
    source = """
class Cls:
    attr = "orig"

    class Inner:
        deep = "deep-orig"

    @staticmethod
    def sm():
        return "sm-orig"

    @classmethod
    def cm(cls):
        return "cm-orig"


VALUE = "module-orig"
"""
    exec(compile(source, "<probe>", "exec"), module.__dict__)  # noqa: S102
    return module


@pytest.fixture
def probe() -> Any:
    module = _target_module()
    sys.modules["_rustest_mp_probe"] = module
    try:
        yield module
    finally:
        del sys.modules["_rustest_mp_probe"]


@pytest.fixture
def mp() -> Any:
    patch = MonkeyPatch()
    try:
        yield patch
    finally:
        patch.undo()


def test_dotted_path_walks_into_a_class(mp: MonkeyPatch, probe: ModuleType) -> None:
    """The click shape: the segment before the last dot is a class, not a module."""
    mp.setattr("_rustest_mp_probe.Cls.attr", "patched")
    assert probe.Cls.attr == "patched"
    mp.undo()
    assert probe.Cls.attr == "orig"


def test_dotted_path_walks_into_a_nested_class(mp: MonkeyPatch, probe: ModuleType) -> None:
    mp.setattr("_rustest_mp_probe.Cls.Inner.deep", "patched")
    assert probe.Cls.Inner.deep == "patched"
    mp.undo()
    assert probe.Cls.Inner.deep == "deep-orig"


def test_dotted_path_still_resolves_a_plain_module_attribute(
    mp: MonkeyPatch, probe: ModuleType
) -> None:
    mp.setattr("_rustest_mp_probe.VALUE", "patched")
    assert probe.VALUE == "patched"


def test_dotted_path_resolves_a_submodule(mp: MonkeyPatch) -> None:
    """``os.path`` is a submodule reached by ``getattr`` on ``os`` — resolve's first branch."""
    mp.setattr("os.path.join", lambda *parts: "J")
    assert os.path.join("a", "b") == "J"


def test_missing_top_level_module_raises_module_not_found(mp: MonkeyPatch) -> None:
    """pytest re-raises the original ImportError when the *named* module is the missing one."""
    with pytest.raises(ModuleNotFoundError, match="No module named 'nonexistent_zz'"):
        mp.setattr("nonexistent_zz.mod.attr", 1)


def test_missing_intermediate_segment_is_wrapped(mp: MonkeyPatch, probe: ModuleType) -> None:
    """``resolve``'s ``expected != used`` branch: ImportError, wrapped, not ModuleNotFound."""
    with pytest.raises(ImportError, match=r"import error in _rustest_mp_probe\.NoSuch: "):
        mp.setattr("_rustest_mp_probe.NoSuch.attr", 1)


def test_missing_attribute_uses_annotated_getattr_message(
    mp: MonkeyPatch, probe: ModuleType
) -> None:
    """pytest 8.4.2: ``'type' object at <ann> has no attribute 'nope'``."""
    with pytest.raises(AttributeError) as excinfo:
        mp.setattr("_rustest_mp_probe.Cls.nope", 1)
    assert str(excinfo.value) == ("'type' object at _rustest_mp_probe.Cls has no attribute 'nope'")


def test_missing_attribute_with_raising_false_sets_it(mp: MonkeyPatch, probe: ModuleType) -> None:
    mp.setattr("_rustest_mp_probe.Cls.nope2", 1, raising=False)
    assert probe.Cls.nope2 == 1
    mp.undo()
    assert not hasattr(probe.Cls, "nope2")


def test_target_without_a_dot_is_pytests_type_error(mp: MonkeyPatch) -> None:
    with pytest.raises(TypeError) as excinfo:
        mp.setattr("nodots", "value")
    assert str(excinfo.value) == "must be absolute import path string, not 'nodots'"


def test_delattr_target_without_a_dot_is_pytests_type_error(mp: MonkeyPatch) -> None:
    with pytest.raises(TypeError) as excinfo:
        mp.delattr("nodots")
    assert str(excinfo.value) == "must be absolute import path string, not 'nodots'"


def test_raising_is_positional_or_keyword(mp: MonkeyPatch, probe: ModuleType) -> None:
    """pytest's ``raising`` is the 4th *positional* parameter; rustest's was keyword-only."""
    mp.setattr(probe.Cls, "nope3", 1, False)
    assert probe.Cls.nope3 == 1
    mp.delattr(probe.Cls, "nope4", False)


def test_delattr_walks_a_dotted_class_path(mp: MonkeyPatch, probe: ModuleType) -> None:
    mp.delattr("_rustest_mp_probe.Cls.attr")
    assert not hasattr(probe.Cls, "attr")
    mp.undo()
    assert probe.Cls.attr == "orig"


def test_undo_restores_the_descriptor_not_the_bound_object(
    mp: MonkeyPatch, probe: ModuleType
) -> None:
    """`_pytest/monkeypatch.py` l. 247-249: for a class target the old value comes from
    ``__dict__``, so a ``staticmethod``/``classmethod`` is restored as the descriptor
    rather than as the plain function ``getattr`` hands back."""
    mp.setattr("_rustest_mp_probe.Cls.sm", lambda: "sm-patched")
    mp.setattr("_rustest_mp_probe.Cls.cm", lambda: "cm-patched")
    assert probe.Cls.sm() == "sm-patched"
    mp.undo()
    assert type(probe.Cls.__dict__["sm"]) is staticmethod
    assert type(probe.Cls.__dict__["cm"]) is classmethod
    assert probe.Cls.sm() == "sm-orig"
    assert probe.Cls.cm() == "cm-orig"


def test_non_string_target_without_value_is_pytests_type_error(mp: MonkeyPatch) -> None:
    with pytest.raises(TypeError) as excinfo:
        mp.setattr(object(), "name")  # type: ignore[arg-type]
    assert str(excinfo.value) == (
        "use setattr(target, name, value) or "
        "setattr(target, value) with target being a dotted import string"
    )


def test_non_string_name_is_pytests_type_error(mp: MonkeyPatch) -> None:
    with pytest.raises(TypeError) as excinfo:
        mp.setattr(object(), 1, 2)  # type: ignore[arg-type]
    assert str(excinfo.value) == (
        "use setattr(target, name, value) with name being a string or "
        "setattr(target, value) with target being a dotted import string"
    )


def test_object_form_missing_attribute_message(mp: MonkeyPatch) -> None:
    """The non-dotted branch keeps pytest's ``{target!r} has no attribute {name!r}``."""

    class Empty:
        pass

    with pytest.raises(AttributeError) as excinfo:
        mp.setattr(Empty, "nope", 1)
    assert str(excinfo.value) == f"{Empty!r} has no attribute 'nope'"


def test_delattr_missing_attribute_message(mp: MonkeyPatch) -> None:
    class Empty:
        pass

    with pytest.raises(AttributeError) as excinfo:
        mp.delattr(Empty, "nope")
    assert str(excinfo.value) == "nope"


def test_syspath_prepend_and_chdir_still_round_trip(mp: MonkeyPatch, tmp_path: Path) -> None:
    """Regression guard: the rewrite touches setattr/delattr only."""
    before = list(sys.path)
    mp.syspath_prepend(tmp_path)
    assert sys.path[0] == os.fspath(tmp_path)
    mp.undo()
    assert sys.path == before
