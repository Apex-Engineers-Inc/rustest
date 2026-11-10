"""Differential oracle: ``rustest._progress.Progress`` against real pytest's progress section.

Real pytest runs a mixed suite in a subprocess; its own per-test outcomes are read back with
the conformance harness's report plugin (no rustest engine anywhere), fed to the renderer as
synthetic events -- once in order, once shuffled into random bursts -- and the bytes written
must equal what pytest printed between ``collected N items`` and its first ``===``/``!!!``
rule (for ``-q``, everything but the final stats line).

Not covered, deliberately: a pass whose *teardown* errors. pytest prints ``.E`` (two letters
for one test); the engine reduces to one status per test, so the renderer prints ``E``.
"""

from __future__ import annotations

import io
import json
import os
import random
import re
import sys
from pathlib import Path

import pytest

from rustest._progress import Progress

from .helpers import PROJECT_ROOT, clean_env, make_tree, run

_PLUGIN_DIR = PROJECT_ROOT / "conformance" / "harness"

_SUITE = {
    "test_a.py": """
import pytest


@pytest.fixture
def boom():
    raise RuntimeError("x")


def test_pass(): pass
def test_fail(): assert 0


@pytest.mark.skip(reason="nope")
def test_skip(): pass


@pytest.mark.xfail(reason="known")
def test_xfail(): assert 0


@pytest.mark.xfail(reason="odd")
def test_xpass(): pass


def test_error(boom): pass


@pytest.mark.skip(reason="a very long reason " * 6)
def test_longskip(): pass
""",
    "test_b.py": "def test_one(): pass\ndef test_two(): pass\n",
    "sub/test_long.py": (
        'import pytest\n\n\n@pytest.mark.parametrize("n", range(60))\ndef test_many(n): pass\n'
    ),
}

#: Reasons the report plugin does not carry (it reduces to a status only).
_REASONS = {
    "test_a.py::test_skip": "nope",
    "test_a.py::test_xfail": "known",
    "test_a.py::test_xpass": "odd",
    "test_a.py::test_longskip": "a very long reason " * 6,
}

_REASON_STATUSES = {"skipped", "xfailed", "xpassed"}


def _pytest(tree: Path, columns: int, args: list[str], report: Path | None = None):
    env = clean_env("PY_COLORS", "NO_COLOR", "FORCE_COLOR")
    env["COLUMNS"] = str(columns)
    argv = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--color=no", *args]
    if report is not None:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_PLUGIN_DIR), env.get("PYTHONPATH")]))
        env["CONFORMANCE_REAL_REPORT"] = str(report)
        argv += ["-p", "_real_report_plugin"]
    return run(argv, tree, env)


#: Where the progress ends, for either runner: a ruled section (``=== FAILURES ===``,
#: ``!!! stopping ... !!!``) or, under ``-q``, pytest's bare stats line.
_END = re.compile(
    r"^(?:[=!]|\d+ (?:failed|passed|skipped|xfailed|xpassed|errors?)\b.* in \d)", re.MULTILINE
)


def _progress_section(stdout: str) -> str:
    """The progress block of *stdout*, extracted identically for pytest and rustest.

    pytest prints a header and ``collected N items`` plus a blank line first; rustest prints
    neither, so the block simply starts at the top of its stdout.
    """
    match = re.search(r"collected \d+ items?\n\n", stdout)
    rest = stdout[match.end() :] if match else stdout
    end = _END.search(rest)
    return rest[: end.start()] if end else rest


def _events(
    ids: list[str], statuses: dict[str, str], rootdir: Path, shuffle_seed: int | None
) -> list[tuple[str, object]]:
    results = [
        (ids.index(node), status, _REASONS.get(node) if status in _REASON_STATUSES else None)
        for node, status in statuses.items()
    ]
    bursts: list[list[tuple[int, str, str | None]]]
    if shuffle_seed is None:
        bursts = [[r] for r in results]
    else:
        rng = random.Random(shuffle_seed)
        rng.shuffle(results)
        bursts, i = [], 0
        while i < len(results):
            step = rng.randint(1, 9)
            bursts.append(results[i : i + step])
            i += step
    return [("collected", (ids, str(rootdir))), *[("results", b) for b in bursts]]


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_tree(tmp_path_factory.mktemp("progress_oracle"), "suite", _SUITE)


@pytest.fixture(scope="module")
def ids(tree: Path) -> list[str]:
    """Every node id, in pytest's collection order (collected once for the module)."""
    collected = _pytest(tree, 80, ["--collect-only", "-q"])
    found = [line for line in collected.stdout.splitlines() if "::" in line]
    assert len(found) == 69
    return found


_MODES = {
    "default": ([], 0, True, False),
    "v": (["-v"], 1, True, False),
    "q": (["-q"], -1, True, False),
    "x": (["-x"], 0, True, True),
    "s": (["-s"], 0, False, False),
    "s_v": (["-s", "-v"], 1, False, False),
    "s_q": (["-s", "-q"], -1, False, False),
    "x_v": (["-x", "-v"], 1, True, True),
}


@pytest.mark.parametrize("columns", [80, 50])
@pytest.mark.parametrize("mode", list(_MODES))
def test_renderer_matches_pytest_byte_for_byte(
    tree: Path, ids: list[str], tmp_path: Path, mode: str, columns: int
):
    flags, verbosity, capture, stopped = _MODES[mode]

    report = tmp_path / "report.json"
    proc = _pytest(tree, columns, ["--tb=no", "--no-summary", *flags], report)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    statuses: dict[str, str] = json.loads(report.read_text(encoding="utf-8"))["statuses"]
    assert len(statuses) == (62 if stopped else 69)  # -x stops at the first failure
    expected = _progress_section(proc.stdout)

    for seed in (None, 1, 2, 3):
        out = io.StringIO()
        renderer = Progress(out, verbosity=verbosity, width=columns, cwd=str(tree), capture=capture)
        for event in _events(ids, statuses, tree, seed):
            renderer(*event)
        renderer("done", stopped)
        assert out.getvalue() == expected, f"seed={seed}"


def test_a_cwd_below_the_rootdir_prints_pytest_relative_paths(tree: Path, tmp_path: Path):
    """``cd sub && pytest ..`` -- the rootdir differs from the invocation dir."""
    for flags, verbosity in (([], 0), (["-v"], 1)):
        report = tmp_path / f"r{verbosity}.json"
        proc = _pytest(tree / "sub", 80, ["--tb=no", "--no-summary", *flags, ".."], report)
        statuses: dict[str, str] = json.loads(report.read_text(encoding="utf-8"))["statuses"]
        ids = list(statuses)  # a full run: every id, rootdir-relative, in run order
        out = io.StringIO()
        renderer = Progress(out, verbosity=verbosity, width=80, cwd=str(tree / "sub"))
        for event in _events(ids, statuses, tree, None):
            renderer(*event)
        renderer("done", False)
        assert out.getvalue() == _progress_section(proc.stdout)


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("columns", [80, 50])
@pytest.mark.parametrize("mode", ["default", "v", "q", "x", "s"])
def test_rustest_stdout_progress_equals_pytests(
    tree: Path, tmp_path: Path, mode: str, columns: int, workers: int
):
    """End to end: the engine's live output, at one worker and at four, against real pytest."""
    if mode == "x" and workers > 1:
        # `-x` stops *dispatching* at a file boundary; with four workers another file is
        # already running when the failure lands, so what else prints is a race, not a
        # rendering rule.  Serial `-x` is the comparable claim.
        pytest.skip("-x with a pool is nondeterministic by design")
    flags = _MODES[mode][0]
    expected = _progress_section(_pytest(tree, columns, ["--tb=no", "--no-summary", *flags]).stdout)
    assert len(expected) > 40

    env = clean_env("PY_COLORS", "NO_COLOR", "FORCE_COLOR")
    env["COLUMNS"] = str(columns)
    proc = run([sys.executable, "-m", "rustest", *flags, "-n", str(workers)], tree, env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _progress_section(proc.stdout) == expected


def _both(tree: Path, flags: list[str], columns: int = 50) -> tuple[str, str, int]:
    """(pytest progress, rustest progress, rustest exit code) for *tree*."""
    expected = _progress_section(_pytest(tree, columns, ["--tb=no", "--no-summary", *flags]).stdout)
    env = clean_env("PY_COLORS", "NO_COLOR", "FORCE_COLOR")
    env["COLUMNS"] = str(columns)
    proc = run([sys.executable, "-m", "rustest", *flags, "-n", "1"], tree, env)
    return expected, _progress_section(proc.stdout), proc.returncode


@pytest.mark.parametrize("flags", [[], ["-v"], ["-q"]])
def test_x_whose_failure_is_the_last_test_prints_no_final_percent(tmp_path: Path, flags: list[str]):
    tree = make_tree(
        tmp_path, "xlast", {"test_a.py": "def test_ok(): pass\ndef test_bad(): assert 0\n"}
    )
    expected, got, code = _both(tree, ["-x", *flags])
    # pytest raises at the failure even when it was the last test, so the default and `-q`
    # rungs get *no* final percent; `-v` has one on every line.
    assert ("[100%]" in expected) == ("-v" in flags)
    assert got == expected and code == 1


@pytest.mark.parametrize("flags", [[], ["-v"], ["-q"]])
def test_a_collection_error_leaves_both_runners_with_no_progress_lines(
    tmp_path: Path, flags: list[str]
):
    """pytest stops at "Interrupted: 1 error during collection" and runs nothing, passing
    files included; rustest does the same, so neither writes a progress section."""
    tree = make_tree(
        tmp_path,
        "collerr",
        {"test_bad.py": "import nonexistent_mod_xyz\n", "test_ok.py": "def test_ok(): pass\n"},
    )
    expected, got, code = _both(tree, flags)
    # Not byte-identical: `pytest -q` still writes the bare newline that ends its empty
    # progress line, and rustest writes nothing for a session that ran no tests.
    assert expected.strip() == got == ""
    assert code == 2
