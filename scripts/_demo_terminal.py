"""Run the rustest CLI as if its output were a terminal, for the documentation recordings.

    python scripts/_demo_terminal.py [rustest arguments ...]

The recorder captures a scene through a pipe, and through a pipe rustest writes pytest's
progress lines instead of its live display.  This is the one thing it does differently from
``python -m rustest``: it tells rustest that stdout is a terminal and gives ``rich`` a console
that is forced to behave like one, 16 colours so the documentation theme's palette applies.
Everything else is the real CLI, so what is recorded is what a user sees.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from rich.console import Console

import rustest._progress as progress
import rustest._rich_progress as rich_progress
from rustest import cli


def _console(stream: TextIO, color: str) -> Console:
    del color  # recordings are coloured: that is part of what a terminal shows
    return Console(
        file=stream,
        force_terminal=True,
        color_system="standard",
        legacy_windows=False,
        width=int(os.environ["COLUMNS"]),
        highlight=False,
        emoji=False,
    )


def main() -> int:
    progress.is_terminal = lambda stream: True
    rich_progress.make_console = _console
    return cli.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
