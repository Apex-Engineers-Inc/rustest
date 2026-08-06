#!/usr/bin/env python
"""Generate the demo recordings used in the documentation.

    python scripts/demos.py          # poe demos

Requires the **VHS** CLI (https://github.com/charmbracelet/vhs) and a built rustest
extension; this script checks for both and builds the extension if it is missing.

**Why Python and not the shell script this replaces.** ``poe demos`` ran
``bash scripts/generate-demos.sh``, and on Windows ``bash`` is whichever one wins the PATH
race — in PowerShell that is WSL's, which cannot see ``uv`` or this project's virtualenv.
It is the same defect that made ``poe docs`` unusable; see ``scripts/docs.py``.

The old script also checked for the built extension at
``.venv/lib/python3.11/site-packages/rustest/rust.so``. That path predates the 3.12 floor
and never exists on Windows in any case, so the check always missed and the build always
re-ran. Asking the interpreter whether it can import the module answers the real question on
every platform.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAPES = ("demos/basic-output.tape", "demos/full-suite.tape")

VHS_MISSING = """\
error: VHS is not installed.
  macOS:  brew install vhs
  Linux:  go install github.com/charmbracelet/vhs@latest
  Binary: https://github.com/charmbracelet/vhs/releases\
"""


def _outputs(tape: Path) -> list[str]:
    """The `Output` paths a tape declares.

    Read from the tape rather than hard-coded, so the summary this prints cannot drift from
    what VHS actually wrote — which the previous script's fixed list of six paths could.
    """
    return [
        line.split(None, 1)[1].strip()
        for line in tape.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("Output ")
    ]


def main() -> int:
    if shutil.which("vhs") is None:
        print(VHS_MISSING, file=sys.stderr)
        return 1

    probe = subprocess.run(
        [sys.executable, "-c", "import rustest.rust"],
        cwd=ROOT,
        capture_output=True,
    )
    if probe.returncode != 0:
        print("==> building the rustest extension")
        subprocess.run(["uv", "run", "maturin", "develop"], check=True, cwd=ROOT)

    (ROOT / "docs" / "assets").mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for rel in TAPES:
        tape = ROOT / rel
        print(f"==> recording {rel}")
        subprocess.run(["vhs", rel], check=True, cwd=ROOT)
        written.extend(_outputs(tape))

    print("\nDemo recordings generated:")
    for path in written:
        print(f"   - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
