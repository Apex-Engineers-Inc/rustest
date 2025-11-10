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
"""
    )


def test_builtin_fixtures_are_available(tmp_path: Path) -> None:
    module_path = tmp_path / "test_builtin_fixtures.py"
    _write_builtin_fixture_module(module_path)

    report = run(paths=[str(tmp_path)])

    assert report.total == 5
    assert report.passed == 5
    assert os.environ.get("RUSTEST_MONKEYPATCH_SENTINEL") is None
