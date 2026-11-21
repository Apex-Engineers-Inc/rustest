"""pytest's base temporary directory (`rustest._basetemp`): numbering, retention, the pool."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from rustest import _basetemp

from .helpers import clean_env, make_tree, run


@pytest.fixture
def temproot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path))
    monkeypatch.setattr(_basetemp, "_worker_basetemp", None)
    monkeypatch.setattr(_basetemp, "_session", None)
    return tmp_path


def _used_session() -> Path:
    path = _basetemp.session_basetemp()
    (path / "used").touch()
    return path


def _sessions(temproot: Path) -> list[str]:
    (root,) = temproot.glob("pytest-of-*")
    return sorted(path.name for path in root.iterdir())


def test_the_newest_three_used_sessions_are_kept(temproot: Path) -> None:
    for _ in range(5):
        _ = _used_session()  # the next one releases it
    _basetemp.release()

    assert _sessions(temproot) == ["pytest-2", "pytest-3", "pytest-4"]


def test_an_unused_session_costs_no_number(temproot: Path) -> None:
    _ = _used_session()
    _ = _basetemp.session_basetemp()  # nothing written: a hand-off, or no tmp_path
    _basetemp.release()

    assert _sessions(temproot) == ["pytest-0"]


def test_a_live_sessions_directory_is_never_removed(temproot: Path) -> None:
    live = _used_session()
    root = live.parent
    _basetemp._session = None  # pyright: ignore[reportPrivateUsage] # another process holds it
    for _ in range(4):
        _ = _used_session()
    _basetemp.release()

    assert (live / ".lock").is_file()
    assert _sessions(temproot) == ["pytest-0", "pytest-2", "pytest-3", "pytest-4"]
    assert root.is_dir()


def test_numbering_ignores_case(tmp_path: Path) -> None:
    """`find_prefixed` lower-cases both sides: on a case-insensitive file system ``Data0``
    and ``data0`` are one name."""
    assert _basetemp.make_numbered_dir(tmp_path, "Data").name == "Data0"
    assert _basetemp.make_numbered_dir(tmp_path, "data").name == "data1"


def test_a_pool_worker_gets_popen_under_the_sessions_directory(
    temproot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _basetemp.session_basetemp()
    monkeypatch.setenv(_basetemp.ENV, str(session))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    (session / "popen-gw1").mkdir()
    (session / "popen-gw1" / "kept").touch()  # a nested run's worker shares the name

    base = _basetemp.worker_basetemp()

    assert base == session / "popen-gw1" and (base / "kept").is_file()  # never emptied
    assert _basetemp.worker_basetemp() is base  # once per process


@pytest.mark.skipif(not hasattr(_basetemp.os, "getuid"), reason="no uid on this platform")
def test_a_root_another_user_owns_is_refused(
    temproot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _basetemp.session_basetemp()  # the root exists, owned by us
    uid = _basetemp.os.getuid()
    monkeypatch.setattr(_basetemp.os, "getuid", lambda: uid + 1)

    with pytest.raises(OSError, match="not owned by the current user"):
        _ = _basetemp.session_basetemp()


def _rustest_in(tree: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "rustest", "-q"], tree, env)


def test_a_temp_root_pytest_could_not_use_fails_only_the_tests_that_use_it(
    tmp_path: Path,
) -> None:
    """pytest makes the base directory lazily: an unusable temp root errors the tests that
    ask for ``tmp_path``, and the rest of the session runs."""
    tree = make_tree(
        tmp_path,
        "root",
        {"test_a.py": "def test_plain():\n    pass\n\n\ndef test_tmp(tmp_path):\n    pass\n"},
    )
    env = {**clean_env(), "PYTEST_DEBUG_TEMPROOT": str(tmp_path / "no" / "such" / "dir")}

    result = _rustest_in(tree, env)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stderr


def test_a_non_default_retention_setting_hands_the_session_to_pytest(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "retention", {"test_a.py": "def test_a():\n    pass\n"})
    _ = (tree / "pytest.ini").write_text("[pytest]\ntmp_path_retention_count = 1\n", "utf-8")

    result = _rustest_in(tree, clean_env())

    assert result.returncode == 0, result.stderr
    assert "tmp_path_retention_count = 1" in result.stderr
