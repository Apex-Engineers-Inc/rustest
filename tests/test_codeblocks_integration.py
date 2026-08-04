# tests/test_codeblocks_integration.py
"""Documentation code block execution: the accepted consequences, asserted rather than
discovered.

`python/tests/test_codeblock_execution.py` covers the mechanism itself (node shapes,
fixture resolution, the failure model). This file covers the observable, deliberately
accepted behavior a user hits from the CLI once the feature is enabled: a block's body
runs at *collect*, not at execute, so `-m`/`--v2-collect-only`/`-k` interact with it the
same way they do with an ordinary `.py` module's top-level code -- and that is a real,
documented shift from the old wrap-in-a-function mechanism, not an incidental detail.

Built in `tmp_path` and run through a subprocess, the same pattern as
`tests/test_pytest_plugins_fixtures.py`: each test needs its own throwaway rootdir with
`[tool.rustest] codeblocks = true`, since this repository's own `pyproject.toml` enabling
the setting only reaches files under this repository, not a page built in a temp dir with
no config of its own.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _md(tmp_path: Path, body: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.rustest]\ncodeblocks = true\n", encoding="utf-8"
    )
    page = tmp_path / "page.md"
    page.write_text(body, encoding="utf-8")
    return page


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # rustest writes its summary to stderr so stdout stays clean for --llm JSONL.
    return subprocess.run(
        [sys.executable, "-m", "rustest", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_deselection_does_not_gate_body_execution(tmp_path: Path) -> None:
    """Accepted consequence, asserted rather than discovered.

    Deselecting by -m no longer prevents a block body from running, because bodies run
    at collect. Identical to .py semantics.
    """
    page = _md(
        tmp_path,
        "```python\n"
        "from pathlib import Path\n"
        "Path('ran.txt').write_text('yes')\n"
        "```\n",
    )
    proc = _run(str(page), "-m", "not codeblock", "-q", cwd=tmp_path)
    assert (tmp_path / "ran.txt").exists(), (
        "the body should still have executed at collect despite deselection\n"
        + proc.stdout
        + proc.stderr
    )


def test_collect_only_executes_bodies(tmp_path: Path) -> None:
    """--v2-collect-only runs module-level code, exactly as it does for a .py file."""
    page = _md(
        tmp_path,
        "```python\n"
        "from pathlib import Path\n"
        "Path('collected.txt').write_text('yes')\n"
        "```\n",
    )
    proc = _run(str(page), "--v2-collect-only", cwd=tmp_path)
    assert (tmp_path / "collected.txt").exists(), proc.stdout + proc.stderr


def test_k_selects_by_block_and_by_inner_test(tmp_path: Path) -> None:
    page = _md(
        tmp_path,
        "```python\ndef test_alpha():\n    assert True\n```\n\n"
        "```python\ndef test_beta():\n    assert True\n```\n",
    )
    by_block = _run(str(page), "-k", "codeblock_0", "-v", cwd=tmp_path)
    assert "test_alpha" in by_block.stdout + by_block.stderr
    by_test = _run(str(page), "-k", "test_beta", "-v", cwd=tmp_path)
    combined = by_test.stdout + by_test.stderr
    assert "1 passed" in combined and "1 deselected" in combined, combined
