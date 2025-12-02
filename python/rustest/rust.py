"""Fallback stub for the compiled rustest extension.

This module is packaged with the Python distribution so unit tests can import the
package without building the Rust extension. Individual tests are expected to
monkeypatch the functions they exercise. Keeping this stub lightweight makes it
easy to trigger CI rebuilds without touching the compiled extension itself.
"""

from __future__ import annotations

from typing import Any, Sequence


def run(
    paths: Sequence[str],
    pattern: str | None = None,
    mark_expr: str | None = None,
    workers: int | None = None,
    capture_output: bool = True,
    enable_codeblocks: bool = True,
    last_failed_mode: str = "none",
    fail_fast: bool = False,
    pytest_compat: bool = False,
    verbose: bool = False,
    ascii: bool = False,
    no_color: bool = False,
    event_callback: Any | None = None,
) -> Any:
    """Placeholder implementation that mirrors the PyO3 extension signature."""

    raise NotImplementedError(
        "rustest.rust.run() is only available when the native extension is built. "
        + "Tests that import rustest without compiling the extension should monkeypatch "
        + "rustest.rust.run with a stub implementation."
    )


def getfixturevalue(_name: str) -> Any:
    """Placeholder matching the native helper exported by the extension."""
    raise RuntimeError("request.getfixturevalue() is only available inside an active rustest test")
