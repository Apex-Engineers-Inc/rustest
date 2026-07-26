"""Probe real pytest for the full-run exit-code table (Task 4)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CASES: dict[str, tuple[dict[str, str], list[str]]] = {}


def case(name: str, files: dict[str, str], args: list[str] | None = None) -> None:
    CASES[name] = (files, args or [])


case("all-pass", {"test_a.py": "def test_one():\n    assert True\n"})
case("failure", {"test_a.py": "def test_one():\n    assert False\n"})
case(
    "setup-error",
    {
        "test_a.py": (
            "import pytest\n"
            "@pytest.fixture\n"
            "def boom():\n"
            "    raise ValueError('setup boom')\n"
            "def test_one(boom):\n"
            "    pass\n"
        )
    },
)
case(
    "teardown-error",
    {
        "test_a.py": (
            "import pytest\n"
            "@pytest.fixture\n"
            "def boom():\n"
            "    yield 1\n"
            "    raise ValueError('teardown boom')\n"
            "def test_one(boom):\n"
            "    assert True\n"
        )
    },
)
case(
    "teardownclass-error",
    {
        "test_a.py": (
            "import unittest\n"
            "class TestBox(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def tearDownClass(cls):\n"
            "        raise ValueError('tearDownClass boom')\n"
            "    def test_one(self):\n"
            "        self.assertTrue(True)\n"
        )
    },
)
case("collection-error", {"test_a.py": "import nope_does_not_exist\n"})
case(
    "collection-error-plus-pass",
    {
        "test_a.py": "import nope_does_not_exist\n",
        "test_b.py": "def test_ok():\n    assert True\n",
    },
)
case(
    "collection-error-plus-fail",
    {
        "test_a.py": "import nope_does_not_exist\n",
        "test_b.py": "def test_bad():\n    assert False\n",
    },
)
case("empty-tree", {"notes.txt": "nothing here\n"})
case("no-tests-in-file", {"test_a.py": "def helper():\n    pass\n"})
case(
    "deselect-all-k",
    {"test_a.py": "def test_one():\n    assert True\n"},
    ["-k", "nomatch"],
)
case(
    "deselect-all-m",
    {"test_a.py": "def test_one():\n    assert True\n"},
    ["-m", "nosuchmark"],
)
case(
    "deselect-some-k-rest-fail",
    {"test_a.py": ("def test_keep():\n    assert False\ndef test_drop():\n    assert True\n")},
    ["-k", "keep"],
)
case(
    "xfail-plain",
    {"test_a.py": ("import pytest\n@pytest.mark.xfail\ndef test_one():\n    assert False\n")},
)
case(
    "xpass-plain",
    {"test_a.py": ("import pytest\n@pytest.mark.xfail\ndef test_one():\n    assert True\n")},
)
case(
    "xpass-strict",
    {
        "test_a.py": (
            "import pytest\n@pytest.mark.xfail(strict=True)\ndef test_one():\n    assert True\n"
        )
    },
)
case(
    "skipped-only",
    {
        "test_a.py": (
            "import pytest\n@pytest.mark.skip(reason='nope')\ndef test_one():\n    assert False\n"
        )
    },
)
case("bad-path-arg", {"test_a.py": "def test_one():\n    pass\n"}, ["nope_missing_dir"])
case(
    "non-python-file-arg",
    {"test_a.py": "def test_one():\n    pass\n", "notes.txt": "hi\n"},
    ["notes.txt"],
)
case(
    "bad-k-expression",
    {"test_a.py": "def test_one():\n    pass\n"},
    ["-k", "and and"],
)
case(
    "bad-m-expression",
    {"test_a.py": "def test_one():\n    pass\n"},
    ["-m", "not not not ("],
)
case(
    "duplicate-path-args",
    {"test_a.py": "def test_one():\n    assert True\n"},
    ["test_a.py", "test_a.py"],
)
case(
    "duplicate-dir-and-file",
    {"pkg/test_a.py": "def test_one():\n    assert True\n"},
    ["pkg", "pkg/test_a.py"],
)
case(
    "error-and-failure",
    {
        "test_a.py": (
            "import pytest\n"
            "@pytest.fixture\n"
            "def boom():\n"
            "    raise ValueError('x')\n"
            "def test_err(boom):\n"
            "    pass\n"
            "def test_fail():\n"
            "    assert False\n"
        )
    },
)
case(
    "skip-plus-xfail-only",
    {
        "test_a.py": (
            "import pytest\n"
            "@pytest.mark.skip\n"
            "def test_s():\n    pass\n"
            "@pytest.mark.xfail\n"
            "def test_x():\n    assert False\n"
        )
    },
)


def run(name: str) -> None:
    files, args = CASES[name]
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        for rel, body in files.items():
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=no", *args],
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=120,
        )
        tail = [ln for ln in proc.stdout.splitlines() if ln.strip()][-2:]
        err = [ln for ln in proc.stderr.splitlines() if ln.strip()][-1:]
        print(f"{name:32} exit={proc.returncode}  {' | '.join(tail + err)[:150]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for name in CASES:
        run(name)
