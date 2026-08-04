from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

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


class RunTally(NamedTuple):
    """The outcome tally of one engine run, plus the ids and the exit code.

    **Why this exists.** Suites that need "run this tree and tell me what happened" used to
    call ``rustest.run(paths=[...])`` and read a ``rustest.reporting.RunReport`` object. That
    was the v1 engine's Python API; both it and the report model went in Phase 4 Task 2, and
    ``rustest.run`` is now the v2 runner, which returns pytest's **exit code** and prints its
    report. This is the same question asked of the v2 boundary.

    Six status buckets, not v1's three -- ``xfailed`` and ``xpassed`` are their own
    categories in schema v2 and folding either into ``skipped``/``passed`` is exactly what
    made an X invisible under the old model.
    """

    total: int
    passed: int
    failed: int
    skipped: int
    xfailed: int
    xpassed: int
    error: int
    deselected: int
    exit_code: int
    ids: list[str]
    collection_errors: list[str]


def run_tree(
    *paths: str | os.PathLike[str],
    workers: int = 1,
    keyword: str | None = None,
    mark_expr: str | None = None,
    capture: bool = True,
    # `None` means "not passed, let config decide", matching the production default and
    # the CLI's tri-state. A plain `bool` here forced the tier ON for every run_tree
    # test, so none of them could observe the off-by-default flip at all.
    codeblocks: bool | None = None,
    invocation_dir: str | os.PathLike[str] | None = None,
) -> RunTally:
    """Run *paths* through the real engine in this process and return the tally.

    Drives ``rustest.rust.v2_run`` -- the same boundary ``rustest.core.v2_run`` and therefore
    the CLI drives -- so the workers, the compat shim and the assertion rewriter are all the
    real ones. Only the *reporting* is different: the JSON report is parsed here instead of
    being printed.

    ``workers=1`` by default, and deliberately. These suites assert on fixture *sharing* and
    on setup counts, and a session- or package-scoped fixture is cached per worker
    (``python/rustest/_v2_worker.py``'s ``FixtureRunner``), so a multi-worker pool would make
    the answer depend on how the files were bin-packed. One worker asks the question the
    tests mean to ask.

    ``invocation_dir`` defaults to the first path when it is a directory, and to its parent
    otherwise, so a tree built under ``tmp_path`` resolves its own rootdir rather than
    inheriting this repository's ``[tool.pytest.ini_options]``.
    """
    rust = ensure_rust_stub()
    resolved = [os.fspath(path) for path in paths]
    if invocation_dir is None:
        first = Path(resolved[0]) if resolved else Path.cwd()
        invocation_dir = first if first.is_dir() else first.parent
    payload: str = rust.v2_run(
        os.fspath(invocation_dir),
        resolved,
        sys.executable,
        workers,
        keyword,
        mark_expr,
        False,
        0,
        "none",
        not capture,
        codeblocks,
    )
    report: dict[str, object] = json.loads(payload)
    summary: dict[str, int] = report["summary"]  # pyright: ignore[reportAssignmentType]
    tests: Sequence[dict[str, object]] = report["tests"]  # pyright: ignore[reportAssignmentType]
    errors: Sequence[dict[str, str]] = report["collection_errors"]  # pyright: ignore[reportAssignmentType]
    return RunTally(
        total=summary["total"],
        passed=summary["passed"],
        failed=summary["failed"],
        skipped=summary["skipped"],
        xfailed=summary["xfailed"],
        xpassed=summary["xpassed"],
        error=summary["error"],
        deselected=summary["deselected"],
        exit_code=int(report["exit_code"]),  # pyright: ignore[reportArgumentType]
        ids=[str(test["id"]) for test in tests],
        collection_errors=[f"{error['path']}: {error['message']}" for error in errors],
    )
