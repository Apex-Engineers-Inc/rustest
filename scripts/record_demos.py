#!/usr/bin/env python
"""Record the terminal sessions the documentation site plays.

    python scripts/record_demos.py            # poe demos -- re-records every scene
    python scripts/record_demos.py failure    # just one

Each scene in ``demos/scenes.toml`` is materialized into a fresh temporary directory, run
for real, and written to ``demos/<name>.termshow``. great-docs pre-renders those into SVG
keyframes at build time; ``{{< termshow file="<name>" >}}`` on a page plays them.

**What is real and what is not.** Every byte of program output is real, captured from the
live process with the arrival time of the chunk it came in. The two synthesized parts are
the shell prompt and the keystrokes of the command itself: there is no shell involved, so
the typing has to be drawn. Everything after the newline is the process talking.

**Why this exists rather than ``great-docs termshow record``.** That recorder opens a PTY
through ``pty``, ``termios`` and ``fcntl``, none of which exist on Windows, and it records
an interactive shell session rather than a named command -- so a recording made that way
would differ every time it was made and could not be regenerated in CI. This records a
declared command list instead, which is reproducible on any platform.

**Why no PTY at all, on any platform, and how the terminal display is still recorded.**
rustest picks its display by whether stdout is a terminal, and through a pipe it writes
pytest's own progress lines. A recording should show what a user sees in a terminal, so each
``rustest`` command runs through ``scripts/_demo_terminal.py``, which is the real CLI told that
stdout is a terminal, with ``rich`` writing 16-colour escape sequences into the pipe. The
live region (cursor-up, erase-line, the bar redrawing in place) is part of the bytes, and
great-docs' renderer replays it with a real terminal emulator, one SVG keyframe per output
event. Writes that arrive within ``COALESCE`` seconds of each other are merged into one
event, which bounds the page weight without changing what a frame shows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCENES = ROOT / "demos" / "scenes.toml"
OUT_DIR = ROOT / "demos"

#: Seconds per keystroke while the command is drawn onto the prompt line.
KEYSTROKE = 0.045
#: Pause between the command being typed and it running, as if Enter were pressed.
BEFORE_RUN = 0.35
#: Pause after a command's output before the next prompt appears.
AFTER_RUN = 0.6
#: A recording is clipped to its content, but never grows past this many rows.
MAX_ROWS = 26
MIN_ROWS = 4

#: Output writes closer together than this are one event (one keyframe on the site).
COALESCE = 0.08

PROMPT = "\x1b[1;32m$\x1b[0m "


def _scene_root() -> Path:
    """A short, neutral directory to build scene projects under.

    This is cosmetic and it matters. A traceback prints the absolute path of the file it
    came from, so whatever directory a scene runs in ends up on the documentation site --
    and ``tempfile.mkdtemp()`` produces
    ``C:\\Users\\<name>\\AppData\\Local\\Temp\\rustest-demo-failure-wg98dieh``, which is
    both wider than the terminal and somebody's username. ``/tmp`` or ``C:\\tmp`` reads
    like a path anyone might have, and stays the same across re-records.
    """
    for candidate in (Path("/tmp"), Path("C:/tmp")):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".rustest-demo-probe"
            probe.touch()
            probe.unlink()
        except OSError:
            continue
        return candidate
    return Path(tempfile.gettempdir())


def _load_scenes() -> list[dict[str, Any]]:
    with SCENES.open("rb") as fh:
        return tomllib.load(fh).get("scene", [])


_TOKENS = re.compile(r"(\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r|\n)")


def _rows_used(text: str, cols: int) -> int:
    """The deepest terminal row `text` ever reaches at `cols` wide, which is how tall the
    recording has to be: the live region grows below the output and is erased again, so the
    final screen alone would clip it.  Only the cursor's row is followed (newline, wrap,
    cursor up and down), which is all a height needs."""
    row = col = deepest = 0
    for token in _TOKENS.split(text):
        if not token or token.startswith("\x1b]"):
            continue
        if token == "\n":
            row, col = row + 1, 0
        elif token == "\r":
            col = 0
        elif token.startswith("\x1b["):
            count = int(re.sub(r"\D", "", token[2:-1]) or 1)
            if token[-1] == "A":
                row = max(0, row - count)
            elif token[-1] == "B":
                row += count
        else:
            col += len(token)
            row, col = row + (col - 1) // cols, (col - 1) % cols + 1
        deepest = max(deepest, row)
    return deepest + 1


def _capture(argv: list[str], cwd: Path, cols: int) -> list[tuple[float, str]]:
    """Run `argv` and return its output as (arrival time, text) chunks.

    stderr is merged into stdout so the two land in the order the process wrote them.
    rustest splits its output across both -- per-test lines on stdout, the summary on
    stderr -- and a recording that reordered them would not be what a terminal shows.
    """
    # Colour is part of what a terminal shows, so nothing in the caller's environment may
    # switch it off (or force it on for the piped parts).
    ambient = {
        k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR", "PY_COLORS")
    }
    env = {
        **ambient,
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "COLUMNS": str(cols),
        "TERM": "xterm-256color",
        # Keep the run reproducible: a stale --lf/--ff store in the scene directory would
        # change what a later command in the same scene does.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    chunks: list[tuple[float, str]] = []
    start = time.monotonic()
    assert proc.stdout is not None
    while True:
        # `bufsize=0` makes this a raw FileIO, so one call is one read syscall: it returns
        # as soon as the process has written something rather than waiting for a full
        # buffer. That is what gives the recording its timing.
        data = proc.stdout.read(65536)
        if not data:
            break
        chunks.append((time.monotonic() - start, data.decode("utf-8", errors="replace")))
    proc.wait()
    return chunks


def _record(scene: dict[str, Any]) -> Path:
    name = scene["name"]
    cols = int(scene.get("cols", 72))

    if scene.get("cwd") == ".":
        workdir = ROOT
        tmp = None
    else:
        workdir = _scene_root() / scene.get("dir", name)
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True)
        tmp = str(workdir)
        for rel, body in scene.get("files", {}).items():
            target = workdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body.lstrip("\n"), encoding="utf-8")

    events: list[list[Any]] = []
    transcript = ""
    # Every event carries the gap since the previous one, which is the .termshow format's
    # own convention -- absolute times would make a cut or a re-order rewrite the file.
    pending = 0.0

    def emit(gap: float, text: str) -> None:
        nonlocal pending
        events.append([round(gap + pending, 3), "o", text])
        pending = 0.0

    try:
        for i, command in enumerate(scene["commands"]):
            display = " ".join(command)
            # The prompt shows `rustest`, which is what a reader types. The recording runs
            # `python -m rustest` so it uses this checkout rather than whatever `rustest`
            # a stray PATH entry resolves to.
            argv = (
                [sys.executable, str(ROOT / "scripts" / "_demo_terminal.py"), *command[1:]]
                if command[0] == "rustest"
                else list(command)
            )

            emit(AFTER_RUN if i else 0.0, PROMPT)
            for char in display:
                emit(KEYSTROKE, char)
            emit(BEFORE_RUN, "\r\n")
            transcript += PROMPT + display + "\r\n"

            print(f"    $ {display}")
            merged: list[tuple[float, str]] = []
            for arrival, text in _capture(argv, workdir, cols):
                if merged and arrival - merged[-1][0] < COALESCE:
                    merged[-1] = (arrival, merged[-1][1] + text)
                else:
                    merged.append((arrival, text))
            previous = 0.0
            for arrival, text in merged:
                text = text.replace("\r\n", "\n").replace("\n", "\r\n")
                emit(round(arrival - previous, 3), text)
                transcript += text
                previous = arrival

        emit(AFTER_RUN, PROMPT)
        transcript += PROMPT
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    rows = max(MIN_ROWS, min(MAX_ROWS, _rows_used(transcript, cols)))
    header = {
        "version": 1,
        "format": "termshow",
        "term": {"cols": cols, "rows": rows, "type": "xterm-256color"},
        "title": scene.get("title", name),
    }
    out = OUT_DIR / f"{name}.termshow"
    lines = [json.dumps(header)] + [json.dumps(event) for event in events]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    scenes = _load_scenes()
    if argv:
        wanted = set(argv)
        scenes = [s for s in scenes if s["name"] in wanted]
        missing = wanted - {s["name"] for s in scenes}
        if missing:
            print(f"error: no such scene: {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    probe = subprocess.run(
        [sys.executable, "-c", "import rustest.rust"], cwd=ROOT, capture_output=True
    )
    if probe.returncode != 0:
        print("==> building the rustest extension")
        subprocess.run(["uv", "run", "maturin", "develop"], check=True, cwd=ROOT)

    for scene in scenes:
        print(f"==> recording {scene['name']}")
        written = _record(scene)
        size = written.stat().st_size
        print(f"    -> {written.relative_to(ROOT).as_posix()} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
