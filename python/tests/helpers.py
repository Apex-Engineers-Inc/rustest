from __future__ import annotations

import importlib
import os
import subprocess
import sys
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_INSTALLED = False


def _candidate_maturin_commands() -> list[list[str]]:
    manifest = PROJECT_ROOT / "pyproject.toml"
    return [
        [
            "uv",
            "run",
            "maturin",
            "develop",
            "--manifest-path",
            os.fspath(manifest),
            "--quiet",
        ],
        [sys.executable, "-m", "maturin", "develop", "--quiet"],
        ["maturin", "develop", "--quiet"],
    ]


def _run_maturin_develop() -> bool:
    errors: list[str] = []
    for command in _candidate_maturin_commands():
        try:
            _ = subprocess.run(command, cwd=PROJECT_ROOT, check=True, capture_output=True)
            return True
        except FileNotFoundError:
            errors.append(f"missing executable: {' '.join(command)}")
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="ignore") if exc.stderr else ""
            errors.append(
                "command failed: "
                + " ".join(command)
                + (f"\nstderr:\n{stderr.strip()}" if stderr else "")
            )
    message = "; ".join(errors)
    _ = warnings.warn(
        "Unable to run `maturin develop`; falling back to importing the in-repo sources. " + message
    )
    return False


def ensure_develop_installed() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if os.environ.get("RUSTEST_TESTS_SKIP_MATURIN") == "1":
        _ = warnings.warn("Skipping `maturin develop` due to RUSTEST_TESTS_SKIP_MATURIN=1.")
    else:
        succeeded = _run_maturin_develop()
        if not succeeded and os.environ.get("RUSTEST_TESTS_REQUIRE_MATURIN") == "1":
            raise RuntimeError(
                "maturin develop failed and fallback is disabled (set RUSTEST_TESTS_SKIP_MATURIN=1 to bypass)."
            )
    _purge_rustest_modules()
    _ = importlib.invalidate_caches()
    _ = importlib.import_module("rustest")
    _INSTALLED = True


def _purge_rustest_modules() -> None:
    for name in list(sys.modules):
        if name == "rustest" or name.startswith("rustest."):
            del sys.modules[name]


def ensure_rust_stub() -> ModuleType:
    ensure_develop_installed()
    module = importlib.import_module("rustest.rust")
    return module


@contextmanager
def stub_rust_module(**attrs: object) -> Iterator[ModuleType]:
    module = ensure_rust_stub()
    previous = {name: getattr(module, name, _MISSING) for name in attrs}
    for name, value in attrs.items():
        setattr(module, name, value)
    try:
        yield module
    finally:
        for name, original in previous.items():
            if original is _MISSING:
                delattr(module, name)
            else:
                setattr(module, name, original)


class _Missing:
    pass


_MISSING = _Missing()
