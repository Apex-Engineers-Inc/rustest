"""What the dependency recorder (``rustest._deps``) costs.

Run it as ``python -m conformance.bench.deps_overhead``.  It measures, and asserts nothing: the
numbers are the point, and they depend on the machine (a Windows box with a virus scanner spends
~100 us per ``os.stat``, which is most of the per-file cost here).

1. **A synthetic suite.**  2,000 small tests (8 first-party functions across 30 modules, one data
   file read, one environment variable read), run bare and recorded; the per-test overhead is split
   into ``begin``, the test body and ``end``.
2. **A record's size**, as JSON, and how many tests one changed file or one changed environment
   variable affects ("how often is everything affected").
3. **One import-heavy test**: a fresh interpreter imports a 300-module first-party package inside
   one recorded test (the import, and the one-off ``end()`` that builds the static import closure).
"""

# pyright: reportImplicitStringConcatenation=false
from __future__ import annotations

import importlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

REPO_PYTHON = Path(__file__).resolve().parents[2] / "python"
sys.path.insert(0, str(REPO_PYTHON))

from rustest._deps import Recorder, Record, affected_by_paths, stale  # noqa: E402

TESTS = 2_000
MODULES = 30
FUNCTIONS = 20
AGE_SECONDS = 2.5  # files must be older than the recorder's racy margin to be cacheable


def _make_project(root: Path) -> list[ModuleType]:
    for number in range(MODULES):
        body = "\n".join(
            f"def f{i}(x):\n    y = x + {i}\n    return [y * k for k in range(5)]\n"
            for i in range(FUNCTIONS)
        )
        (root / f"bench_mod_{number}.py").write_text(body, encoding="utf-8")
    (root / "bench_data.txt").write_text("data", encoding="utf-8")
    time.sleep(AGE_SECONDS)
    sys.path.insert(0, str(root))
    return [importlib.import_module(f"bench_mod_{number}") for number in range(MODULES)]


def _body(mods: list[ModuleType], root: Path, index: int) -> None:
    for step in range(8):
        getattr(mods[(index + step) % MODULES], f"f{(index + step) % FUNCTIONS}")(index)
    with open(root / "bench_data.txt", encoding="utf-8") as handle:
        handle.read()
    os.environ.get("HOME")


def synthetic() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        mods = _make_project(root)

        def run(recorded: bool) -> tuple[float, list[Record], float, float]:
            recorder = Recorder(root=root) if recorded else None
            if recorder:
                recorder.start()
            records: list[Record] = []
            begin = end = 0.0
            started = time.perf_counter()
            for index in range(TESTS):
                key = f"t{index}"
                if recorder:
                    t0 = time.perf_counter()
                    recorder.begin(key)
                    begin += time.perf_counter() - t0
                _body(mods, root, index)
                if recorder:
                    t0 = time.perf_counter()
                    records.append(recorder.end(key))
                    end += time.perf_counter() - t0
            elapsed = time.perf_counter() - started
            if recorder:
                recorder.stop()
            return elapsed, records, begin, end

        bare = statistics.median(run(False)[0] for _ in range(5))
        runs = [run(True) for _ in range(5)]
        recorded = statistics.median(r[0] for r in runs)
        _, records, begin, end = runs[-1]
        print(
            f"{TESTS} tests: bare {bare / TESTS * 1e6:.0f} us/test, recorded "
            f"{recorded / TESTS * 1e6:.0f} us/test (+{(recorded - bare) / TESTS * 1e6:.0f} us/test: "
            f"begin {begin / TESTS * 1e6:.0f}, end {end / TESTS * 1e6:.0f})"
        )
        sizes = [len(json.dumps(r.dump())) for r in records]
        print(
            f"record size: median {statistics.median(sizes):.0f} bytes, "
            f"{statistics.median(len(r.deps) for r in records):.0f} dependencies"
        )
        by_id = {f"t{i}": r for i, r in enumerate(records)}
        one_file = root / "bench_mod_0.py"
        print(
            f"one edited module affects {len(affected_by_paths(by_id, [one_file]))} of {TESTS} tests; "
            f"{len(stale(by_id))} are stale before any edit"
        )
        os.environ["HOME"] = os.environ.get("HOME", "") + "-changed"
        print(f"one changed environment variable (HOME): {len(stale(by_id))} of {TESTS} stale")


_IMPORT_TEST = """
import sys, time, importlib
sys.path.insert(0, {repo!r}); sys.path.insert(0, {tmp!r})
from rustest._deps import Recorder
rec = Recorder() if {record} else None
if rec:
    rec.start()
    rec.begin('t')
t0 = time.perf_counter()
importlib.import_module('bigpkg')
t1 = time.perf_counter()
r = rec.end('t') if rec else None
t2 = time.perf_counter()
print(t1 - t0, t2 - t1, len(r.deps) if r else 0)
"""


def import_heavy(modules: int = 300) -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        package = root / "bigpkg"
        package.mkdir()
        (package / "__init__.py").write_text(
            "".join(f"from . import m{i}\n" for i in range(modules)), encoding="utf-8"
        )
        for i in range(modules):
            imports = "".join(f"from . import m{j}\n" for j in range(max(0, i - 3), i))
            functions = "".join(f"def g{k}(x):\n    return x + {k}\n" for k in range(10))
            (package / f"m{i}.py").write_text(imports + functions, encoding="utf-8")
        time.sleep(AGE_SECONDS)
        samples: dict[bool, list[tuple[float, ...]]] = {False: [], True: []}
        for _ in range(7):
            for recorded in (False, True):
                code = _IMPORT_TEST.format(repo=str(REPO_PYTHON), tmp=str(root), record=recorded)
                out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                samples[recorded].append(tuple(float(x) for x in out.stdout.split()))
        bare = statistics.median(s[0] for s in samples[False])
        imported = statistics.median(s[0] for s in samples[True])
        closure = statistics.median(s[1] for s in samples[True])
        print(
            f"import-heavy test ({modules}-module first-party package): import {bare * 1000:.0f} ms bare, "
            f"{imported * 1000:.0f} ms recorded; end() {closure * 1000:.0f} ms once per process; "
            f"{int(samples[True][0][2])} dependencies"
        )


if __name__ == "__main__":
    synthetic()
    import_heavy()
