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
