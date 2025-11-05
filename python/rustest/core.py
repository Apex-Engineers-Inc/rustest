"""High level Python API wrapping the Rust extension."""

from __future__ import annotations

from collections.abc import Sequence

from . import _rust
from ._reporting import RunReport


def run(
    *,
    paths: Sequence[str],
    pattern: str | None,
    workers: int | None,
    capture_output: bool,
) -> RunReport:
    """Execute tests and return a rich report."""

    raw_report = _rust.run(list(paths), pattern, workers, capture_output)
    return RunReport.from_py(raw_report)
