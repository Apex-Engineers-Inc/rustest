"""Fallback stub for the compiled rustest extension.

This module is packaged with the Python distribution so unit tests can import the
package without building the Rust extension. Individual tests are expected to
monkeypatch the functions they exercise. Keeping this stub lightweight makes it
easy to trigger CI rebuilds without touching the compiled extension itself.

The real module (`src/lib.rs`) exports four functions, all of them the engine's boundary.
The `run`/`getfixturevalue` placeholders that used to sit here backed the v1 engine, deleted
in Phase 4 Task 2.
"""

from __future__ import annotations

from typing import Sequence


def resolve_config(_invocation_dir: str, _args: Sequence[str]) -> str:
    """Placeholder for the v2 config debug surface (see ``src/engine/py.rs``)."""
    raise NotImplementedError(
        "rustest.rust.resolve_config() is only available when the native extension is built."
    )


def collect(
    _invocation_dir: str,
    _args: Sequence[str],
    _python_executable: str,
    _workers: int,
) -> str:
    """Placeholder for the collection entry point (see ``src/engine/py.rs``)."""
    raise NotImplementedError(
        "rustest.rust.collect() is only available when the native extension is built."
    )
