"""``rust.run`` / ``rust.collect`` with ``pytest_fallback``: the binding contract the CLI uses.

On a tree whose conftest defines a hook only pytest can honour, the engine stops at the
collection barrier and says which hooks: no test runs, no live event is sent, the ``--lf``
cache is not written. A hook whose only effect is on terminal text does not stop it. The
option is off by default, which is today's behaviour.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .helpers import ensure_rust_stub, write

_TEST = "def test_a():\n    assert 0\n"


def _tree(root: Path, conftest: str) -> Path:
    write(root / "pytest.ini", "[pytest]\n")
    write(root / "conftest.py", conftest)
    write(root / "test_a.py", _TEST)
    return root


def _run(root: Path, **options: object) -> dict[str, object]:
    rust = ensure_rust_stub()
    return json.loads(rust.run(os.fspath(root), [], sys.executable, 1, **options))


def test_a_hook_only_pytest_can_run_stops_at_the_barrier(tmp_path: Path) -> None:
    root = _tree(tmp_path, "def pytest_configure(config):\n    pass\n")
    events: list[str] = []

    report = _run(root, pytest_fallback=True, on_event=lambda kind, _payload: events.append(kind))

    assert report["pytest_fallback"] == ["conftest.py::pytest_configure"]
    assert report["tests"] == []
    assert events == []
    assert not (root / ".rustest_cache" / "v" / "cache" / "lastfailed").exists()

    manifest = json.loads(
        ensure_rust_stub().collect(os.fspath(root), [], sys.executable, 1, pytest_fallback=True)
    )
    assert manifest["pytest_fallback"] == ["conftest.py::pytest_configure"]
    assert manifest["tests"] == []


def test_off_by_default_and_a_skippable_hook_runs_normally(tmp_path: Path) -> None:
    hooked = _tree(tmp_path / "hooked", "def pytest_configure(config):\n    pass\n")
    header = _tree(tmp_path / "header", "def pytest_report_header(config):\n    return 'x'\n")

    for report in (_run(hooked), _run(header, pytest_fallback=True)):
        assert "pytest_fallback" not in report
        tests: list[dict[str, str]] = report["tests"]  # pyright: ignore[reportAssignmentType]
        assert [test["status"] for test in tests] == ["failed"]
