#!/usr/bin/env python
"""Build or preview the documentation site.

    python scripts/docs.py build     # writes great-docs/_site (poe docs-build)
    python scripts/docs.py preview   # local server           (poe docs)

Everything after the first argument is forwarded to ``great-docs``.

**Why Python and not the shell script this replaces.** ``poe docs`` ran
``bash scripts/docs.sh preview``, and on Windows ``bash`` is whichever one wins the PATH
race. In PowerShell that is ``C:\\windows\\system32\\bash.exe`` -- **WSL**, not Git Bash --
which runs the script inside Linux, where ``uv`` is absent, a Windows Quarto install is
invisible, and ``.venv-docs/Scripts/python.exe`` is the wrong kind of binary. It could not
work, and the failure it produced ("the Quarto CLI is not on PATH") pointed at the wrong
thing. Python is already present -- poe runs under it -- so this has no such ambiguity.

**Why a separate environment.** The docs toolchain lives in ``.venv-docs``, never in
``.venv``. great-docs depends on jupyter, which depends on anyio, which registers a
**pytest plugin** -- and the conformance gates run real pytest out of ``.venv`` and must
keep loading exactly what an unpolluted pytest loads. The ``[dependency-groups] docs``
comment in ``pyproject.toml`` has the long form.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv-docs"

QUARTO_MISSING = """\
error: the Quarto CLI is not on PATH.
  Windows: winget install --id Posit.Quarto -e
  macOS:   brew install --cask quarto
  Linux:   https://quarto.org/docs/get-started/\
"""


def _venv_bin(name: str) -> Path:
    """Path to an executable inside `.venv-docs`, on either layout."""
    if os.name == "nt" or (VENV / "Scripts").is_dir():
        return VENV / "Scripts" / f"{name}.exe"
    return VENV / "bin" / name


def main(argv: list[str]) -> int:
    python = _venv_bin("python")
    great_docs = _venv_bin("great-docs")

    if shutil.which("quarto") is None:
        print(QUARTO_MISSING, file=sys.stderr)
        return 1

    if not python.is_file():
        print(f"==> bootstrapping {VENV.name}")
        subprocess.run(["uv", "venv", str(VENV), "--python", "3.12"], check=True, cwd=ROOT)

    # Both are needed on every run, and both are cheap when already satisfied: the `docs`
    # group brings great-docs, and installing this project is what lets great-docs
    # introspect `python/rustest` for the auto-generated API reference.
    #
    # **NOT `--editable`.** A maturin editable install writes the compiled extension into
    # the source tree next to the Python package, and this venv is a *different
    # interpreter* from `.venv` -- so `--editable` here drops a `rust.cp312-win_amd64.pyd`
    # into `python/rustest/` alongside the `cp314` one that `maturin develop` put there. It
    # is inert under 3.14 (the loader only takes its own ABI tag), which is exactly what
    # makes it dangerous: it sits in the tree unnoticed until someone runs the suite under
    # 3.12 and gets a stale binary. A plain install copies the package into this venv and
    # leaves the tree alone; great-docs reads the copy, which is what it should be
    # documenting anyway.
    for target in (["--group", "docs"], ["."]):
        subprocess.run(
            ["uv", "pip", "install", "--quiet", "--python", str(python), *target],
            check=True,
            cwd=ROOT,
        )

    # `great-docs` writes generated pages with the interpreter's default encoding, and at
    # least one rustest docstring carries a non-cp1252 character (U+2220). Without this the
    # build dies at the reference step on a Windows console.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    return subprocess.run([str(great_docs), *argv], cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
