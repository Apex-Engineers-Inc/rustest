"""The codeblocks switch: CLI flag, config key, and their precedence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from .helpers import run_tree


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


def test_run_tree_can_observe_the_off_by_default(tmp_path: Path) -> None:
    """The in-process helper must be able to see the production default.

    ``helpers.run_tree`` used to hardcode ``codeblocks: bool = True`` and forward it, so no
    ``run_tree``-based test could observe the off-by-default flip at all: every such test
    ran with the tier forced on regardless of config. That is the same shape as the
    ``test_v2_flip_cli.py`` fixtures which encoded the old always-on assumption, and it
    survived the sweep that fixed those.

    With the parameter widened to ``bool | None = None``, "not passed" means "let config
    decide", and a tree with no config collects no markdown.
    """
    (tmp_path / "page.md").write_text(
        "doc\n\n```python\ndef test_in_a_block():\n    assert True\n```\n", encoding="utf-8"
    )

    # No config, no flag: the tier is off, so naming a `.md` is a usage error -- the same
    # answer pytest gives, and the same one the CLI gives as exit 4.
    with pytest.raises(ValueError, match="found no collectors"):
        run_tree(tmp_path / "page.md", invocation_dir=tmp_path)

    # Explicitly on: the block's test runs.
    on = run_tree(tmp_path / "page.md", invocation_dir=tmp_path, codeblocks=True)
    assert on.passed == 1, f"expected the block's test to run, got {on}"
