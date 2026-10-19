"""Async fixtures declared with ``pytest_asyncio.fixture`` under ``--pytest-compat``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .helpers import ensure_develop_installed

ASYNC_FIXTURE_TESTS = """
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def value():
    return 41


@pytest_asyncio.fixture
async def resource():
    state = {"open": True}
    yield state
    state["open"] = False


@pytest.mark.asyncio
async def test_async_fixture(value):
    assert value + 1 == 42


@pytest.mark.asyncio
async def test_async_generator_fixture(resource):
    assert resource["open"] is True
"""

STALE_WARNING = "async fixtures"


def _run_rustest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    ensure_develop_installed()
    return subprocess.run(
        [sys.executable, "-m", "rustest", *args, "--color", "never", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_pytest_asyncio_fixture_works_under_pytest_compat(tmp_path: Path) -> None:
    (tmp_path / "test_async_fixtures.py").write_text(ASYNC_FIXTURE_TESTS)

    result = _run_rustest(tmp_path, "--pytest-compat")

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "2 passed" in output, output
    assert STALE_WARNING not in output.lower()


def test_pytest_fixture_hint_does_not_claim_async_fixtures_unsupported(
    tmp_path: Path,
) -> None:
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef plain():\n    return 1\n"
    )
    (tmp_path / "test_plain.py").write_text("def test_it(plain):\n    assert plain == 1\n")

    result = _run_rustest(tmp_path)

    output = result.stdout + result.stderr
    assert "--pytest-compat" in output, output
    assert STALE_WARNING not in output.lower(), output
