"""A fixture whose setup raises, as pytest's ``FixtureDef.cached_result`` keeps it: the
exception is raised again for every later request in the same scope instance without running
the fixture again, a finalizer it registered first still runs once, and a function-scoped
fixture runs per test. (The corpus case ``fixtures/wide-scope-setup-raises`` grades the counts
against pytest.)"""

from __future__ import annotations

import sys
from pathlib import Path

from .helpers import make_tree, run

_CONFTEST = """
import os
import pytest

LOG = os.path.join(os.path.dirname(__file__), "calls.log")


def log(line):
    with open(LOG, "a") as handle:
        handle.write(line + "\\n")


@pytest.fixture(scope="module")
def broken(request):
    log("setup")
    request.addfinalizer(lambda: log("finalizer"))
    raise RuntimeError("setup failed")


@pytest.fixture
def broken_each(request):
    log("function setup")
    raise RuntimeError("setup failed")
"""

_TESTS = "".join(f"def test_{n}(broken):\n    pass\n\n\n" for n in range(3)) + "".join(
    f"def test_each_{n}(broken_each):\n    pass\n\n\n" for n in range(2)
)


def test_a_raising_setup_runs_once_per_scope_and_its_finalizer_once(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "raises", {"conftest.py": _CONFTEST, "test_a.py": _TESTS})

    result = run([sys.executable, "-m", "rustest", "-q", "-n", "1"], tree)

    assert result.returncode == 1
    assert "5 errors" in result.stderr, result.stderr
    calls = (tree / "calls.log").read_text(encoding="utf-8").splitlines()
    assert calls.count("setup") == 1
    assert calls.count("finalizer") == 1
    assert calls.count("function setup") == 2
