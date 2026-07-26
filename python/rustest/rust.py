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


def v2_resolve_config(_invocation_dir: str, _args: Sequence[str]) -> str:
    """Placeholder for the v2 config debug surface (see ``src/v2/py.rs``)."""
    raise NotImplementedError(
        "rustest.rust.v2_resolve_config() is only available when the native extension is built."
    )


def v2_collect(
    _invocation_dir: str,
    _args: Sequence[str],
    _python_executable: str,
    _workers: int,
) -> str:
    """Placeholder for the v2 collection entry point (see ``src/v2/py.rs``)."""
    raise NotImplementedError(
        "rustest.rust.v2_collect() is only available when the native extension is built."
    )
