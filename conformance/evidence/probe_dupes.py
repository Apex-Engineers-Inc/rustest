"""Probe pytest's duplicate-argument and not-found-collector behaviour."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FILES = {
    "test_a.py": "def test_one():\n    assert True\ndef test_two():\n    assert True\n",
    "pkg/test_b.py": "def test_three():\n    assert True\n",
    "notes.txt": "hi\n",
}

ARGSETS: list[list[str]] = [
    ["notes.txt"],
    ["test_a.py", "test_a.py"],
    ["test_a.py", "test_a.py", "test_a.py"],
    ["test_a.py", "./test_a.py"],
    ["pkg", "pkg/test_b.py"],
    ["pkg/test_b.py", "pkg"],
    [".", "test_a.py"],
    [".", "."],
    ["pkg", "pkg"],
    ["test_a.py::test_one", "test_a.py::test_one"],
    ["test_a.py::test_one", "test_a.py"],
    ["test_a.py", "pkg/test_b.py"],
]


def run(args: list[str], collect_only: bool) -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        for rel, body in FILES.items():
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=no"]
        if collect_only:
            cmd.append("--collect-only")
        proc = subprocess.run([*cmd, *args], cwd=tmp, capture_output=True, text=True, timeout=120)
        body_lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        err = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        label = "collect" if collect_only else "run    "
        print(f"{label} {str(args):55} exit={proc.returncode}")
        for line in (body_lines + err)[:12]:
            print(f"        {line}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for argset in ARGSETS:
        run(argset, collect_only=True)
        print()
