"""The manifest cache must not change a gate's answer -- on the *second* run.

The three conformance gates already exercise the Tier S manifest cache, but only ever
**cold**: ``_isolate_case`` copies each case into a fresh temporary directory and
explicitly excludes ``.rustest_cache``, so every gate run starts with an empty store and
"run the gate twice" produces two cold runs. That isolation is right -- a gate whose result
depended on what an earlier gate left behind would be untrustworthy -- but it means the
gates cannot, by construction, observe a warm cache.

This file supplies the missing leg: for every corpus case, isolate **once** and collect
**twice in the same tree**, so the second invocation reads what the first one wrote. A
stale-cache bug shows up here as a case whose ids or exit code move between the two runs,
which is precisely the failure the cache key exists to prevent and precisely the one no
amount of cold running can find.

The comparison is against the first run rather than against pytest on purpose: the gate
already establishes run 1 == pytest, so run 2 == run 1 closes the chain, and repeating the
pytest leg here would double the cost for no extra signal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from conformance.__main__ import discover_cases
from conformance.harness.grade import load_case_args
from conformance.harness.runners import _isolate_case, _run

CASES = discover_cases()


def _collect(work: Path, args: list[str]) -> tuple[list[str], int]:
    proc = _run([sys.executable, "-m", "rustest", "--v2-collect-only", *args], work)
    return proc.stdout.splitlines(), proc.returncode


@pytest.mark.parametrize("name,case_dir", CASES, ids=[name for name, _ in CASES])
def test_a_warm_collect_grades_identically(name: str, case_dir: Path) -> None:
    """Two collections of one tree agree, the second one reading the first one's cache."""
    args = load_case_args(case_dir)
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        cold_ids, cold_exit = _collect(work, args)
        warm_ids, warm_exit = _collect(work, args)

    # The store's *existence* is deliberately not asserted per case: a case whose every
    # file is dynamic writes nothing, and that is correct -- only Tier S results are
    # cached. What must never happen is a difference, which is what these pin. The next
    # test keeps that from making the whole suite vacuous.
    assert warm_ids == cold_ids, name
    assert warm_exit == cold_exit, name


def test_at_least_one_corpus_case_actually_warms_the_cache() -> None:
    """The suite above is satisfied by a cache that never stores anything.

    So one case is checked for the store's existence directly. ``collection/class-collection``
    is Tier S in the attribution table (`.superpowers/sdd/p2-task-1-report.md` section 7.4);
    if it ever stops being static this fails loudly rather than leaving the parametrized
    suite quietly vacuous.
    """
    case_dir = next(path for name, path in CASES if name == "collection/class-collection")
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        _collect(work, load_case_args(case_dir))
        store = work / ".rustest_cache" / "v2-manifest"
        assert store.is_dir(), "no manifest cache was written for a Tier S case"
        shards = list(store.glob("*.json"))
        assert len(shards) == 1, shards
        assert "test_classes.py" in shards[0].read_text(encoding="utf-8")


def test_the_cache_can_be_switched_off_from_the_environment() -> None:
    """``RUSTEST_V2_MANIFEST_CACHE=off`` writes nothing and answers the same.

    The escape hatch is what makes a suspected stale entry diagnosable without deleting
    anything, so it has to reach the engine from a real subprocess -- an in-process test
    would prove only that the string was parsed.
    """
    case_dir = next(path for name, path in CASES if name == "collection/class-collection")
    args = load_case_args(case_dir)
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        expected, exit_code = _collect(work, args)
        shutil.rmtree(work / ".rustest_cache", ignore_errors=True)

        env = {**os.environ, "RUSTEST_V2_MANIFEST_CACHE": "off"}
        proc = subprocess.run(
            [sys.executable, "-m", "rustest", "--v2-collect-only", *args],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        assert proc.stdout.splitlines() == expected
        assert proc.returncode == exit_code
        assert not (work / ".rustest_cache" / "v2-manifest").exists()


def test_a_corrupt_shard_is_ignored_rather_than_fatal() -> None:
    """A truncated store is a cache to rebuild, not a run to abort.

    Truncation is the realistic corruption -- a machine that lost power mid-write, a
    filesystem that reordered, a sync tool that copied a file being written -- and the
    dangerous response to it is any response other than "collect the tests anyway".
    """
    case_dir = next(path for name, path in CASES if name == "collection/class-collection")
    args = load_case_args(case_dir)
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        expected, exit_code = _collect(work, args)

        shard = next((work / ".rustest_cache" / "v2-manifest").glob("*.json"))
        whole = shard.read_text(encoding="utf-8")
        shard.write_text(whole[: len(whole) // 2], encoding="utf-8")

        ids, code = _collect(work, args)
        assert ids == expected
        assert code == exit_code
        # ...and the damaged shard is replaced, so one bad write is not permanent.
        assert shard.read_text(encoding="utf-8") == whole


def test_editing_a_file_between_runs_changes_the_ids() -> None:
    """The invalidation that matters most, through the real CLI.

    Everything else in this file asserts that the cache changes *nothing*; a cache that
    is never read would pass all of it. This is the other half: a file edited between two
    runs must collect differently the second time.
    """
    case_dir = next(path for name, path in CASES if name == "collection/class-collection")
    args = load_case_args(case_dir)
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        before, _ = _collect(work, args)

        target = work / "test_classes.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n\ndef test_added_later():\n    pass\n",
            encoding="utf-8",
        )
        after, _ = _collect(work, args)

    assert after == [*before, "test_classes.py::test_added_later"]
