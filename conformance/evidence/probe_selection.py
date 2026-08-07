"""Probe pytest's -k / -m matching semantics against a fixed tree."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FILES = {
    "pytest.ini": "[pytest]\nmarkers =\n    slow\n    smoke\n    net\n",
    "alpha/test_first.py": (
        "import pytest\n"
        "@pytest.mark.slow\n"
        "def test_one():\n    pass\n"
        "def test_two():\n    pass\n"
        "@pytest.mark.net(scope='wide', retries=3)\n"
        "def test_three():\n    pass\n"
    ),
    "beta/test_second.py": (
        "import pytest\n"
        "@pytest.mark.smoke\n"
        "class TestBox:\n"
        "    def test_method(self):\n        pass\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_param(n):\n    pass\n"
        "def test_UPPER():\n    pass\n"
    ),
}

QUERIES: list[tuple[str, list[str]]] = [
    ("-k", ["one"]),
    ("-k", ["alpha"]),  # directory name
    ("-k", ["beta"]),
    ("-k", ["test_first"]),  # module name without .py
    ("-k", ["test_first.py"]),  # module name with .py
    ("-k", ["TestBox"]),
    ("-k", ["testbox"]),  # case-insensitive
    ("-k", ["param"]),
    ("-k", ["test_param[1]"]),  # full parametrized name
    ("-k", ["slow"]),  # mark name via -k
    ("-k", ["smoke"]),  # class mark via -k on a method
    ("-k", ["one or two"]),
    ("-k", ["not one"]),
    ("-k", ["test_ and not (one or two)"]),
    ("-k", ["UPPER"]),
    ("-k", ["upper"]),
    ("-k", ["  "]),  # whitespace only -> lstrip -> no filter
    ("-k", ["1"]),  # digit substring, hits test_param[1]
    ("-m", ["slow"]),
    ("-m", ["not slow"]),
    ("-m", ["smoke"]),
    ("-m", ["net"]),
    ("-m", ["net(scope='wide')"]),
    ("-m", ["net(scope='narrow')"]),
    ("-m", ["net(retries=3)"]),
    ("-m", ["net(retries=4)"]),
    ("-m", ["net(scope='wide', retries=3)"]),
    ("-m", ["slow or smoke"]),
    ("-m", ["  "]),  # whitespace only, no lstrip on -m
    ("-m", ["parametrize"]),  # is parametrize a mark for -m?
]


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        for rel, body in FILES.items():
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        for flag, args in QUERIES:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "--collect-only",
                    "-q",
                    flag,
                    *args,
                ],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=120,
            )
            ids = [
                ln
                for ln in proc.stdout.splitlines()
                if "::" in ln and not ln.startswith(("=", " ", "E "))
            ]
            note = ""
            if proc.returncode >= 3 and proc.returncode != 5:
                note = " ERR:" + " ".join(proc.stderr.split())[:110]
            print(f"{flag} {args[0]!r:34} exit={proc.returncode} -> {ids}{note}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
