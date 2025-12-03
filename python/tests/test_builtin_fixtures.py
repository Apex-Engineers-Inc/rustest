"""Integration tests for the built-in fixtures provided by rustest."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rustest import run


@pytest.fixture(autouse=True)
def clear_sentinel_env() -> None:
    os.environ.pop("RUSTEST_MONKEYPATCH_SENTINEL", None)


def _write_builtin_fixture_module(target: Path) -> None:
    target.write_text(
        """
import os
import sys
from pathlib import Path

import pytest

try:
    import py
except Exception:  # pragma: no cover - optional dependency at runtime
    py = None


BASE_INFO = Path(__file__).with_name("base_info.txt")
TMPDIR_BASE_INFO = Path(__file__).with_name("tmpdir_base.txt")
PATHS_SEEN: list[Path] = []
BASES_SEEN: list[Path] = []
SYSPATH_ENTRIES: list[str] = []
CHDIR_TARGET: Path | None = None


class Sample:
    value = "original"


GLOBAL_DICT = {"existing": "value"}


def test_tmp_path(tmp_path):
    file = tmp_path / "example.txt"
    file.write_text("hello")
    assert file.read_text() == "hello"


def test_tmp_path_factory(tmp_path_factory):
    location = tmp_path_factory.mktemp("factory")
    file = location / "data.txt"
    file.write_text("42")
    assert file.read_text() == "42"


def test_tmpdir(tmpdir):
    created = tmpdir / "sample.txt"
    created.write("content")
    assert created.read() == "content"


def test_tmpdir_factory(tmpdir_factory):
    location = tmpdir_factory.mktemp("factory")
    created = location / "data.txt"
    created.write("payload")
    assert created.read() == "payload"


def test_monkeypatch(monkeypatch):
    monkeypatch.setenv("RUSTEST_MONKEYPATCH_SENTINEL", "set")
    assert os.environ["RUSTEST_MONKEYPATCH_SENTINEL"] == "set"


def test_tmp_path_is_isolated(tmp_path, tmp_path_factory):
    PATHS_SEEN.append(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("marker")

    other = tmp_path_factory.mktemp("tmp_path_extra")
    assert marker.exists()
    assert not (other / "marker.txt").exists()
    assert tmp_path.parent == tmp_path_factory.getbasetemp()


def test_tmp_path_is_unique_between_tests(tmp_path):
    assert len(PATHS_SEEN) == 1
    assert PATHS_SEEN[0] != tmp_path
    assert PATHS_SEEN[0].exists()
    assert tmp_path.exists()


def test_tmp_path_factory_creates_unique_directories(tmp_path_factory):
    first = tmp_path_factory.mktemp("custom")
    second = tmp_path_factory.mktemp("custom")
    assert first != second
    assert first.name.startswith("custom")
    assert second.name.startswith("custom")
    assert first.parent == tmp_path_factory.getbasetemp()
    assert second.parent == tmp_path_factory.getbasetemp()


def test_tmp_path_factory_numbered_false(tmp_path_factory):
    unique = tmp_path_factory.mktemp("plain", numbered=False)
    assert unique.name == "plain"
    with pytest.raises(FileExistsError):
        tmp_path_factory.mktemp("plain", numbered=False)


def test_tmp_path_factory_records_base(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    BASES_SEEN.append(base)
    if not BASE_INFO.exists():
        BASE_INFO.write_text(str(base))


def test_tmp_path_factory_reuses_base_between_tests(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    BASES_SEEN.append(base)
    assert len({str(path) for path in BASES_SEEN}) == 1


def test_tmpdir_records_base(tmpdir_factory, tmpdir):
    if py is None:
        pytest.skip("py library is required for tmpdir fixtures")

    created = tmpdir_factory.mktemp("tmpdir_custom", numbered=False)
    TMPDIR_BASE_INFO.write_text(str(tmpdir_factory.getbasetemp()))
    created.join("payload.txt").write("payload")
    assert created.join("payload.txt").read() == "payload"
    assert isinstance(tmpdir, py.path.local)


def test_monkeypatch_setattr_and_items(monkeypatch):
    monkeypatch.setattr(Sample, "value", "patched")
    monkeypatch.setitem(GLOBAL_DICT, "new", "value")
    monkeypatch.delitem(GLOBAL_DICT, "existing")

    assert Sample.value == "patched"
    assert GLOBAL_DICT["new"] == "value"
    assert "existing" not in GLOBAL_DICT


def test_monkeypatch_environment_and_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("RUSTEST_ENV_VAR", "value")
    monkeypatch.setenv("RUSTEST_ENV_VAR", "prefix", prepend=":")
    monkeypatch.delenv("RUSTEST_ENV_VAR", raising=False)

    path = tmp_path / "syspath"
    path.mkdir()
    monkeypatch.syspath_prepend(str(path))
    SYSPATH_ENTRIES.append(str(path))
    assert sys.path[0] == str(path)

    target = tmp_path / "cwd"
    target.mkdir()
    monkeypatch.chdir(target)

    global CHDIR_TARGET
    CHDIR_TARGET = target
    assert Path.cwd() == target


def test_monkeypatch_restores_state():
    assert Sample.value == "original"
    assert GLOBAL_DICT == {"existing": "value"}
    assert "RUSTEST_ENV_VAR" not in os.environ

    if SYSPATH_ENTRIES:
        for entry in SYSPATH_ENTRIES:
            assert entry not in sys.path

    if CHDIR_TARGET is not None:
        assert Path.cwd() != CHDIR_TARGET
"""
    )


def test_builtin_fixtures_are_available(tmp_path: Path) -> None:
    module_path = tmp_path / "test_builtin_fixtures.py"
    _write_builtin_fixture_module(module_path)

    report = run(paths=[str(tmp_path)])

    assert report.total == 15
    assert report.passed == 15

    base_info_path = tmp_path / "base_info.txt"
    assert base_info_path.exists()
    base_path = Path(base_info_path.read_text())
    assert not base_path.exists()

    tmpdir_base_info = tmp_path / "tmpdir_base.txt"
    if tmpdir_base_info.exists():
        tmpdir_base_path = Path(tmpdir_base_info.read_text())
        assert not tmpdir_base_path.exists()

    assert os.environ.get("RUSTEST_MONKEYPATCH_SENTINEL") is None


def _write_monkeypatch_edge_cases_module(target: Path) -> None:
    """Write module testing MonkeyPatch edge cases."""
    target.write_text(
        """
import os
import pytest

class Target:
    existing_attr = "original"

GLOBAL_DICT = {"key": "value"}


def test_monkeypatch_setattr_dotted_path_requires_dot(monkeypatch):
    '''Test that setattr with dotted path requires at least one dot.'''
    with pytest.raises(TypeError, match="at least one dot"):
        monkeypatch.setattr("nodots", "value")


def test_monkeypatch_delattr_dotted_path_requires_dot(monkeypatch):
    '''Test that delattr with dotted path requires at least one dot.'''
    with pytest.raises(TypeError, match="at least one dot"):
        monkeypatch.delattr("nodots")


def test_monkeypatch_setattr_raising_false(monkeypatch):
    '''Test setattr with raising=False on non-existent attribute.'''
    monkeypatch.setattr(Target, "nonexistent", "value", raising=False)
    assert Target.nonexistent == "value"


def test_monkeypatch_setattr_raising_true(monkeypatch):
    '''Test setattr with raising=True on non-existent attribute.'''
    with pytest.raises(AttributeError):
        monkeypatch.setattr(Target, "nonexistent_raising", "value", raising=True)


def test_monkeypatch_delattr_raising_false(monkeypatch):
    '''Test delattr with raising=False on non-existent attribute.'''
    # Should not raise
    monkeypatch.delattr(Target, "nonexistent", raising=False)


def test_monkeypatch_delattr_raising_true(monkeypatch):
    '''Test delattr with raising=True on non-existent attribute.'''
    with pytest.raises(AttributeError):
        monkeypatch.delattr(Target, "nonexistent", raising=True)


def test_monkeypatch_delitem_raising_false(monkeypatch):
    '''Test delitem with raising=False on non-existent key.'''
    # Should not raise
    monkeypatch.delitem(GLOBAL_DICT, "nonexistent", raising=False)


def test_monkeypatch_delitem_raising_true(monkeypatch):
    '''Test delitem with raising=True on non-existent key.'''
    with pytest.raises(KeyError):
        monkeypatch.delitem(GLOBAL_DICT, "nonexistent", raising=True)


def test_monkeypatch_delenv_raising_false(monkeypatch):
    '''Test delenv with raising=False on non-existent variable.'''
    # Should not raise
    monkeypatch.delenv("NONEXISTENT_VAR_12345", raising=False)


def test_monkeypatch_delenv_raising_true(monkeypatch):
    '''Test delenv with raising=True on non-existent variable.'''
    with pytest.raises(KeyError):
        monkeypatch.delenv("NONEXISTENT_VAR_12345", raising=True)


def test_monkeypatch_setenv_prepend_existing(monkeypatch):
    '''Test setenv with prepend on existing variable.'''
    os.environ["TEST_PREPEND_VAR"] = "original"
    monkeypatch.setenv("TEST_PREPEND_VAR", "prepended", prepend=":")
    assert os.environ["TEST_PREPEND_VAR"] == "prepended:original"


def test_monkeypatch_setenv_prepend_new(monkeypatch):
    '''Test setenv with prepend on non-existing variable.'''
    os.environ.pop("TEST_NEW_PREPEND_VAR", None)
    monkeypatch.setenv("TEST_NEW_PREPEND_VAR", "value", prepend=":")
    # prepend has no effect when var doesn't exist
    assert os.environ["TEST_NEW_PREPEND_VAR"] == "value"


def test_monkeypatch_context_manager():
    '''Test MonkeyPatch as context manager.'''
    from rustest.builtin_fixtures import MonkeyPatch

    original_value = Target.existing_attr

    with MonkeyPatch.context() as mp:
        mp.setattr(Target, "existing_attr", "modified")
        assert Target.existing_attr == "modified"

    # Should be restored after context
    assert Target.existing_attr == original_value
"""
    )


def test_monkeypatch_edge_cases(tmp_path: Path) -> None:
    """Test MonkeyPatch edge cases and error conditions."""
    module_path = tmp_path / "test_monkeypatch_edge_cases.py"
    _write_monkeypatch_edge_cases_module(module_path)

    report = run(paths=[str(tmp_path)])

    assert report.total == 13
    assert report.passed == 13


def _write_cache_edge_cases_module(target: Path) -> None:
    """Write module testing Cache edge cases."""
    target.write_text(
        """
import pytest


def test_cache_get_default(cache):
    '''Test cache.get with default value.'''
    result = cache.get("nonexistent/key", "default_value")
    assert result == "default_value"


def test_cache_set_and_get(cache):
    '''Test cache set and get.'''
    cache.set("test/key", {"data": 123})
    result = cache.get("test/key")
    assert result == {"data": 123}


def test_cache_dict_style_access(cache):
    '''Test cache dict-style access.'''
    cache["dict/key"] = [1, 2, 3]
    assert cache["dict/key"] == [1, 2, 3]


def test_cache_contains(cache):
    '''Test cache __contains__.'''
    cache.set("exists/key", "value")
    assert "exists/key" in cache
    assert "nonexistent/key" not in cache


def test_cache_mkdir(cache, tmp_path):
    '''Test cache.mkdir.'''
    dir_path = cache.mkdir("test_dir")
    assert dir_path.exists()
    assert dir_path.is_dir()


def test_cache_various_types(cache):
    '''Test cache with various JSON-serializable types.'''
    cache.set("string", "hello")
    cache.set("number", 42)
    cache.set("float", 3.14)
    cache.set("bool", True)
    cache.set("none", None)
    cache.set("list", [1, 2, 3])
    cache.set("dict", {"nested": {"data": 1}})

    assert cache.get("string") == "hello"
    assert cache.get("number") == 42
    assert cache.get("float") == 3.14
    assert cache.get("bool") is True
    assert cache.get("none") is None
    assert cache.get("list") == [1, 2, 3]
    assert cache.get("dict") == {"nested": {"data": 1}}
"""
    )


def test_cache_edge_cases(tmp_path: Path) -> None:
    """Test Cache edge cases."""
    module_path = tmp_path / "test_cache_edge_cases.py"
    _write_cache_edge_cases_module(module_path)

    report = run(paths=[str(tmp_path)])

    assert report.total == 6
    assert report.passed == 6


def _write_capture_fixture_module(target: Path) -> None:
    """Write module testing capsys and capfd fixtures.

    Note: rustest's capture behavior may differ from pytest's in how it handles
    output capture. These tests verify the basic fixture availability and API,
    not exact capture semantics.
    """
    target.write_text(
        """
import sys


def test_capsys_available(capsys):
    '''Test capsys fixture is available and has correct API.'''
    # Verify the fixture has the expected methods
    assert hasattr(capsys, 'readouterr')

    # Verify readouterr returns a named tuple with out and err
    captured = capsys.readouterr()
    assert hasattr(captured, 'out')
    assert hasattr(captured, 'err')
    assert isinstance(captured.out, str)
    assert isinstance(captured.err, str)


def test_capsys_empty(capsys):
    '''Test capsys with no output returns empty strings.'''
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_capfd_available(capfd):
    '''Test capfd fixture is available and has correct API.'''
    # Verify the fixture has the expected methods
    assert hasattr(capfd, 'readouterr')

    # Verify readouterr returns a named tuple with out and err
    captured = capfd.readouterr()
    assert hasattr(captured, 'out')
    assert hasattr(captured, 'err')
    assert isinstance(captured.out, str)
    assert isinstance(captured.err, str)
"""
    )


def test_capture_fixtures(tmp_path: Path) -> None:
    """Test capsys and capfd fixtures are available."""
    module_path = tmp_path / "test_capture_fixtures.py"
    _write_capture_fixture_module(module_path)

    report = run(paths=[str(tmp_path)])

    assert report.total == 3
    assert report.passed == 3


def _write_caplog_fixture_module(target: Path) -> None:
    """Write module testing caplog fixture."""
    target.write_text(
        """
import logging


def test_caplog_basic(caplog):
    '''Test basic caplog functionality.'''
    logging.info("test message")
    assert "test message" in caplog.text
    assert len(caplog.records) == 1


def test_caplog_levels(caplog):
    '''Test caplog with different levels.'''
    logging.debug("debug msg")
    logging.info("info msg")
    logging.warning("warning msg")
    logging.error("error msg")

    assert len(caplog.records) == 4
    levels = [r.levelname for r in caplog.records]
    assert "DEBUG" in levels
    assert "INFO" in levels
    assert "WARNING" in levels
    assert "ERROR" in levels


def test_caplog_record_tuples(caplog):
    '''Test caplog.record_tuples.'''
    logging.info("tuple test")
    tuples = caplog.record_tuples
    assert len(tuples) == 1
    name, level, message = tuples[0]
    assert message == "tuple test"
    assert level == logging.INFO


def test_caplog_messages(caplog):
    '''Test caplog.messages property.'''
    logging.info("msg1")
    logging.info("msg2")
    assert caplog.messages == ["msg1", "msg2"]


def test_caplog_clear(caplog):
    '''Test caplog.clear().'''
    logging.info("before clear")
    assert len(caplog.records) == 1

    caplog.clear()
    assert len(caplog.records) == 0


def test_caplog_set_level(caplog):
    '''Test caplog.set_level().'''
    caplog.set_level(logging.WARNING)
    logging.debug("should not appear")
    logging.warning("should appear")

    assert len(caplog.records) == 1
    assert "should appear" in caplog.text


def test_caplog_at_level(caplog):
    '''Test caplog.at_level() context manager.'''
    with caplog.at_level(logging.ERROR):
        logging.warning("not captured in context")
        logging.error("captured in context")

    # Only error should be captured
    assert len(caplog.records) == 1
    assert "captured in context" in caplog.text
"""
    )


def test_caplog_fixture(tmp_path: Path) -> None:
    """Test caplog fixture."""
    module_path = tmp_path / "test_caplog_fixture.py"
    _write_caplog_fixture_module(module_path)

    report = run(paths=[str(tmp_path)])

    assert report.total == 7
    assert report.passed == 7
