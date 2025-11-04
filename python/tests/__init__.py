"""Test utilities for the rustest Python package."""

from __future__ import annotations

from .helpers import ensure_develop_installed, ensure_rust_stub

__all__ = ["ensure_develop_installed", "ensure_rust_stub"]

# Build and install the project into the active environment (or gracefully
# degrade to the in-repo sources) before tests import the package.
ensure_develop_installed()
# Ensure the compiled extension is stubbed so package imports succeed during tests.
ensure_rust_stub()
