"""High level Python API wrapping the Rust extension."""

from __future__ import annotations

from collections.abc import Sequence

from . import rust
from .reporting import RunReport


def run(
    *,
    paths: Sequence[str],
    pattern: str | None = None,
    workers: int | None = None,
    capture_output: bool = True,
    enable_codeblocks: bool = True,
) -> RunReport:
    """Execute tests and return a rich report."""

    raw_report = rust.run(list(paths), pattern, workers, capture_output, enable_codeblocks)
    return RunReport.from_py(raw_report)
