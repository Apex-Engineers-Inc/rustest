"""Fallback stub for the compiled rustest extension.

This module is packaged with the Python distribution so unit tests can import the
package without building the Rust extension. Individual tests are expected to
monkeypatch the functions they exercise.
"""

from __future__ import annotations

from typing import Any, Sequence


def run(
    _paths: Sequence[str],
    _pattern: str | None,
    _workers: int | None,
    _capture_output: bool,
) -> Any:
    """Placeholder implementation that mirrors the extension signature."""

    raise NotImplementedError(
        "The rustest native extension is unavailable. Tests must patch rustest.rust.run."
    )
