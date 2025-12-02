"""
Integration test to ensure directory-scoped runs load ancestor conftest.py files.

These tests are executed via pytest (subprocess) and are skipped when running
with rustest itself to avoid recursive invocations.
"""

import os
import subprocess
import sys
from pathlib import Path

# Skip when running inside rustest itself to avoid recursive invocation
if os.environ.get("RUSTEST_RUNNING") != "1":

    def _python_executable() -> str:
        """Prefer system python if available to match CI behavior."""
        candidate = Path("/usr/local/bin/python")
        return str(candidate) if candidate.exists() else sys.executable

    def test_directory_run_loads_parent_autouse() -> None:
        """Running rustest on a nested directory should still load parent fixtures."""
        repo_root = Path(__file__).resolve().parents[2]
        target = repo_root / "tests" / "test_conftest_nested" / "subdir"

        cmd = [
            _python_executable(),
            "-m",
            "rustest",
            str(target),
            "--color",
            "never",
        ]
        result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)

        assert result.returncode == 0, (
            "rustest failed when running nested directory:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        summary = result.stdout + result.stderr
        assert "passed" in summary and "failed" not in summary, (
            "Expected nested directory run to succeed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
