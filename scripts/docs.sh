#!/usr/bin/env bash
# Build or preview the documentation site.
#
#   ./scripts/docs.sh build     # writes great-docs/_site (poe docs-build)
#   ./scripts/docs.sh preview   # local server           (poe docs)
#
# Everything after the first argument is forwarded to `great-docs`.
#
# Why a script and not `uv run great-docs`: the docs toolchain lives in its own
# environment (`.venv-docs`), never in `.venv`. great-docs depends on jupyter, which
# depends on anyio, which registers a **pytest plugin** -- and the conformance gates run
# real pytest out of `.venv` and must keep loading exactly what an unpolluted pytest
# loads. The `[dependency-groups] docs` comment in `pyproject.toml` has the long form.
set -euo pipefail

cd "$(dirname "$0")/.."

VENV=".venv-docs"
PY="$VENV/bin/python"
BIN="$VENV/bin/great-docs"
if [ ! -d "$VENV" ] || [ -d "$VENV/Scripts" ]; then
    PY="$VENV/Scripts/python.exe"
    BIN="$VENV/Scripts/great-docs.exe"
fi

if ! command -v quarto >/dev/null 2>&1; then
    echo "error: the Quarto CLI is not on PATH." >&2
    echo "  Windows: winget install --id Posit.Quarto -e" >&2
    echo "  macOS:   brew install --cask quarto" >&2
    echo "  Linux:   https://quarto.org/docs/get-started/" >&2
    exit 1
fi

if [ ! -x "$PY" ]; then
    echo "==> bootstrapping $VENV"
    uv venv "$VENV" --python 3.12
fi

# Both are needed on every run, and both are cheap when already satisfied: the `docs`
# group brings great-docs, and installing this project is what lets great-docs introspect
# `python/rustest` for the auto-generated API reference.
#
# **NOT `--editable`.** A maturin editable install writes the compiled extension into the
# source tree next to the Python package, and this venv is a *different interpreter* from
# `.venv` -- so `--editable` here drops a `rust.cp312-win_amd64.pyd` into
# `python/rustest/` alongside the `cp314` one that `maturin develop` put there. It is inert
# under 3.14 (the loader only takes its own ABI tag), which is exactly what makes it
# dangerous: it sits in the tree unnoticed until someone runs the suite under 3.12 and gets
# a stale binary. A plain install copies the package into this venv and leaves the tree
# alone; great-docs reads the copy, which is what it should be documenting anyway.
uv pip install --quiet --python "$PY" --group docs
uv pip install --quiet --python "$PY" .

# `great-docs` writes generated pages with the interpreter's default encoding, and at
# least one rustest docstring carries a non-cp1252 character (U+2220). Without this the
# build dies at the reference step on a Windows console.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

exec "$BIN" "$@"
