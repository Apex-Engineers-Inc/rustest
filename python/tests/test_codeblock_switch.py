"""The codeblocks switch: CLI flag, config key, and their precedence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _md(tmp_path: Path, body: str, *, enable: bool = True) -> Path:
    """Write a one-page project whose config enables codeblocks unless told otherwise."""
    if enable:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.rustest]\ncodeblocks = true\n", encoding="utf-8"
        )
    page = tmp_path / "page.md"
    page.write_text(body, encoding="utf-8")
    return page


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # rustest writes its human summary to stderr so stdout stays clean for --llm JSONL;
    # callers assert against the combined streams.
    return subprocess.run(
        [sys.executable, "-m", "rustest", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_cli_flag_is_tristate(tmp_path: Path) -> None:
    """No flag means config decides; --no-codeblocks overrides config to off."""
    page = _md(tmp_path, "```python\nassert True\n```\n")

    enabled = _run(str(page), "-q", cwd=tmp_path)
    assert enabled.returncode == 0, enabled.stdout + enabled.stderr
    assert "1 passed" in enabled.stdout + enabled.stderr

    overridden = _run(str(page), "--no-codeblocks", "-q", cwd=tmp_path)
    assert overridden.returncode == 4, overridden.stdout + overridden.stderr


def test_cli_flag_enables_without_config(tmp_path: Path) -> None:
    """--codeblocks works with no config file at all."""
    page = _md(tmp_path, "```python\nassert True\n```\n", enable=False)

    off = _run(str(page), "-q", cwd=tmp_path)
    assert off.returncode == 4, off.stdout + off.stderr

    on = _run(str(page), "--codeblocks", "-q", cwd=tmp_path)
    assert on.returncode == 0, on.stdout + on.stderr
    assert "1 passed" in on.stdout + on.stderr
