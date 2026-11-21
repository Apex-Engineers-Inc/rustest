"""Skip reasons that pytest prints, through the real engine.

A bare ``skip`` is pytest's ``unconditional skip``; a string ``skipif``/``xfail`` condition is
evaluated at run time against ``os``/``sys``/``platform`` and the module's globals, with
``condition: <source>`` as the default reason (`_pytest/skipping.py::evaluate_condition`).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .helpers import ensure_rust_stub, write

_SRC = """\
import pytest
import rustest

FLAG = True


@pytest.mark.skip
def test_bare_pytest_skip():
    pass


@rustest.mark.skip
def test_bare_rustest_skip():
    pass


@pytest.mark.skipif("sys.platform != 'nope'")
def test_string_condition():
    pass


@pytest.mark.skipif("FLAG", reason="module global")
def test_string_condition_reads_module_globals():
    pass


@pytest.mark.skipif("1 == 2")
def test_false_string_condition_runs():
    pass


@pytest.mark.xfail("1 == 1")
def test_xfail_string_condition():
    assert False
"""


def test_skip_reasons_match_pytest(tmp_path: Path) -> None:
    write(tmp_path / "pytest.ini", "[pytest]\n")
    write(tmp_path / "test_r.py", _SRC)
    rust = ensure_rust_stub()
    report = json.loads(rust.run(os.fspath(tmp_path), [], sys.executable, 1))
    got = {
        test["id"].split("::")[1]: (test["status"], test.get("message")) for test in report["tests"]
    }

    assert got == {
        "test_bare_pytest_skip": ("skipped", "unconditional skip"),
        "test_bare_rustest_skip": ("skipped", "unconditional skip"),
        "test_string_condition": ("skipped", "condition: sys.platform != 'nope'"),
        "test_string_condition_reads_module_globals": ("skipped", "module global"),
        "test_false_string_condition_runs": ("passed", None),
        "test_xfail_string_condition": ("xfailed", "condition: 1 == 1"),
    }
