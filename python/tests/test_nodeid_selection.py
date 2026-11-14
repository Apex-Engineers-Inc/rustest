"""Node-id arguments (``rustest test_m.py::test_a``), diffed against REAL pytest.

Every invocation below runs through real pytest and through rustest on the same isolated
tree, and must agree on the exit code and on the **list** of executed node ids -- set and
order, duplicates included -- plus, where pytest refuses the argument, on the ``ERROR:``
text it prints.  The first 23 rows are the recorded probe (pytest 8.4.2); the rest cover
pytest's collection-cache rules (a collector some argument walked *through* expands to
nothing; a plain file argument re-collects its directory), ``unittest`` classes, files
``python_files`` would not match, files that fail to import or skip themselves, ``-x``,
``-m``, ``--collect-only`` on both collection tiers, and ``--lf``/``--ff``.

pytest's executed ids are read off its ``-v`` lines, which are printed in execution order.
rustest's come from ``--report-json``, whose ``tests`` list is in selection order.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from .helpers import clean_env, make_tree, run

TEST_M = """\
import pytest


def test_a():
    pass


def test_b():
    assert False


@pytest.mark.parametrize("x", [1, 2])
def test_p(x):
    pass


class TestC:
    def test_one(self):
        pass

    def test_two(self):
        pass

    @pytest.mark.parametrize("x", ["a", "b"], ids=["a", "b"])
    def test_q(self, x):
        pass
"""

FILES = {
    "test_m.py": TEST_M,
    "sub/test_n.py": "def test_a():\n    pass\n\n\ndef test_z():\n    pass\n",
    "test_u.py": (
        "import unittest\n\n\nclass TestU(unittest.TestCase):\n"
        "    def test_x(self):\n        pass\n\n    def test_y(self):\n        pass\n"
    ),
    "test_nest.py": (
        "class TestOuter:\n    class TestInner:\n        def test_i(self):\n            pass\n\n"
        "    def test_o(self):\n        pass\n"
    ),
    "odd/check_x.py": "def test_a():\n    pass\n",
    "odd/broken.py": "import nonexistent_module_for_rustest\n\n\ndef test_a():\n    pass\n",
    "odd/skipmod.py": (
        'import pytest\n\npytest.skip("nope", allow_module_level=True)\n\n\n'
        "def test_a():\n    pass\n"
    ),
    "odd/notes.dat": "x\n",
    "odd/notes.txt": "x\n",
    # Every module instance and every test writes itself to `order.log`, so the comparison
    # covers what actually ran and in which order -- not just the report.
    "conftest.py": """\
import pathlib

import pytest

LOG = pathlib.Path(__file__).with_name("order.log")


def note(text):
    with LOG.open("a", encoding="utf-8") as log:
        log.write(text + "\\n")


@pytest.fixture(scope="module", autouse=True)
def module_instance(request):
    note("setup " + request.path.name)
    yield
    note("teardown " + request.path.name)


@pytest.fixture(autouse=True)
def each_test(request):
    note(request.node.nodeid)
""",
}

#: ``(args, message_difference)``.  The second element names a known, deliberate difference
#: in the ``ERROR:`` text as ``(pytest's spelling, rustest's)``, or is ``None``.
CASES: list[tuple[str, tuple[str, str] | None]] = [
    # --- the recorded probe, cases 1-23 ---
    ("test_m.py::test_a", None),
    ("test_m.py::test_p", None),
    ("test_m.py::test_p[1]", None),
    ("test_m.py::TestC", None),
    ("test_m.py::TestC::test_one", None),
    ("test_m.py::TestC::test_q[a]", None),
    ("test_m.py::test_nope", None),
    ("test_m.py::TestC::test_nope", None),
    ("test_m.py::TestNope::test_one", None),
    ("test_m.py::test_a test_m.py::test_b", None),
    ("test_m.py::test_b test_m.py::test_a", None),
    ("test_m.py::test_a test_m.py::test_a", None),
    ("test_m.py test_m.py::test_a", None),
    ("test_m.py::test_a sub/test_n.py", None),
    ("test_m.py::test_a sub", None),
    ("test_m.py::test_a test_m.py::test_nope", None),
    ("test_m.py::test_a -k test_b", None),
    ("nope.py::test_a", None),
    ("test_m.py::", None),
    ("test_m.py::test_p[9]", None),
    ("sub/test_n.py::test_a", None),
    ("sub\\test_n.py::test_a", None),
    ("test_m.py::TestC::test_one::extra", None),
    # --- pytest's collection cache ---
    ("test_m.py::test_a test_m.py", None),
    ("test_m.py::test_p test_m.py::test_p[1]", None),
    ("test_m.py::TestC test_m.py::TestC::test_one", None),
    ("test_m.py::TestC::test_one test_m.py::TestC", None),
    ("test_m.py::TestC test_m.py::TestC", None),
    ("test_m.py::test_a .", None),
    ("sub sub/test_n.py::test_a", None),
    ("sub/test_n.py::test_a sub/test_n.py sub/test_n.py::test_a", None),
    ("sub test_m.py sub/test_n.py::test_a", None),
    ("sub/test_n.py::test_z test_m.py::test_b sub/test_n.py::test_a", None),
    # Plain paths follow the same cache (`. x` drops `.`), and a file named twice is two
    # module instances: its module fixture is built twice.
    ("test_m.py sub/test_n.py test_m.py", None),
    ("sub/test_n.py sub/test_n.py", None),
    (". sub", None),
    ("sub .", None),
    (". test_m.py", None),
    # --- shapes ---
    ("test_m.py::TestC::test_q", None),
    ("test_nest.py::TestOuter", None),
    ("test_nest.py::TestOuter::TestInner::nope", None),
    ("test_m.py::test_a::", None),
    ("test_u.py::TestU::test_x", None),
    ("test_u.py::TestU", None),
    # The manifest does not say a class is a TestCase, so the collector is named `Class`.
    ("test_u.py::TestU::nope", ("<UnitTestCase TestU>", "<Class TestU>")),
    ("odd/check_x.py::test_a", None),
    ("odd/broken.py::test_a", None),
    ("odd/skipmod.py::test_a", None),
    ("odd/notes.dat::x", None),
    ("odd/notes.txt::x", None),
    ("sub::test_a", None),
    ("nope.py::test_a test_m.py::test_nope", None),
    ("test_m.py::test_nope test_m.py::TestC::x", None),
    ("test_m.py::test_a odd/broken.py", None),
    # --- options ---
    ("-x test_m.py::test_b test_m.py::test_a", None),
    ("-x sub/test_n.py::test_z test_m.py::test_b sub/test_n.py::test_a", None),
    ("-m slow test_m.py::test_a", None),
    ("-k test_q test_m.py::TestC", None),
]

#: pytest's ``-v`` progress line: ``<nodeid> <WORD>`` then padding and ``[ NN%]``.
_VERBOSE = re.compile(r"^(\S+) (PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_tree(tmp_path_factory.mktemp("nodeids"), "tree", FILES)


def _env() -> dict[str, str]:
    return clean_env("CI", "BUILD_NUMBER", "RUSTEST_COLLECT_TIER", "RUSTEST_MANIFEST_CACHE")


def _pytest(
    tree: Path, args: list[str], *, cache: bool = False
) -> subprocess.CompletedProcess[str]:
    plugins = [] if cache else ["-p", "no:cacheprovider"]
    return run(
        [sys.executable, "-m", "pytest", *plugins, "-v", "--tb=no", "-rN", *args], tree, _env()
    )


def _rustest(tree: Path, args: list[str]) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """One worker, so ``order.log`` is a single sequence like pytest's."""
    report = tree / "report.json"
    report.unlink(missing_ok=True)
    argv = [sys.executable, "-m", "rustest", "-n", "1", *args, "--report-json", str(report)]
    proc = run(argv, tree, _env())
    if not report.exists():
        return proc, []
    payload: dict[str, list[dict[str, str]]] = json.loads(report.read_text(encoding="utf-8"))
    return proc, [test["id"] for test in payload["tests"]]


def _executed(stdout: str) -> list[str]:
    return [match.group(1) for line in stdout.splitlines() if (match := _VERBOSE.match(line))]


def _order(tree: Path) -> list[str]:
    """Read and clear ``order.log``: setups, tests and teardowns in the order they ran."""
    log = tree / "order.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    log.unlink(missing_ok=True)
    return lines


def _errors(stderr: str) -> str:
    """The ``ERROR:`` text with pytest's trailing blank line(s) normalised away."""
    return stderr.strip()


def _assert_agree(
    tree: Path, args: list[str], difference: tuple[str, str] | None = None, *, cache: bool = False
) -> None:
    _ = _order(tree)
    oracle = _pytest(tree, args, cache=cache)
    oracle_order = _order(tree)
    ours, ids = _rustest(tree, args)
    where = (
        f"args={args}\n--- pytest rc={oracle.returncode}\n{oracle.stdout}\n{oracle.stderr}"
        f"\n--- rustest rc={ours.returncode}\n{ours.stdout}\n{ours.stderr}"
    )
    assert ours.returncode == oracle.returncode, where
    assert ids == _executed(oracle.stdout), where
    assert _order(tree) == oracle_order, where
    if oracle.returncode == 4:
        expected = _errors(oracle.stderr)
        if difference is not None:
            expected = expected.replace(*difference)
        assert _errors(ours.stderr) == expected, where


@pytest.mark.parametrize(("line", "difference"), CASES, ids=[line for line, _ in CASES])
def test_node_id_arguments_select_what_pytest_selects(
    tree: Path, line: str, difference: tuple[str, str] | None
) -> None:
    _assert_agree(tree, line.split(), difference)


def _listed(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if "::" in line]


@pytest.mark.parametrize("tier", ["auto", "d"])
@pytest.mark.parametrize(
    "line",
    [
        "test_m.py::test_a",
        "test_m.py::TestC",
        "test_m.py::test_b test_m.py::test_a",
        "test_m.py::test_a test_m.py::test_a",
        "test_m.py::test_a test_m.py",
        "test_m.py::test_a -k test_b",
        "test_m.py::test_nope",
        # Repeated and overlapping plain paths: duplicate targets on the cached tier.
        "test_m.py sub/test_n.py test_m.py",
        ". sub",
    ],
)
def test_collect_only_lists_what_pytest_lists(tree: Path, line: str, tier: str) -> None:
    """``auto``: the static tier cold, then warm off the manifest cache; ``d``: the dynamic
    tier, which never caches, twice."""
    args = ["--collect-only", *line.split()]
    oracle = run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *args], tree, _env()
    )
    env = {**_env(), "RUSTEST_COLLECT_TIER": tier}
    shutil.rmtree(tree / ".rustest_cache", ignore_errors=True)
    for _ in range(2):
        ours = run([sys.executable, "-m", "rustest", *args], tree, env)
        where = f"{args}\n{oracle.stdout}{oracle.stderr}\n---\n{ours.stdout}{ours.stderr}"
        assert ours.returncode == oracle.returncode, where
        assert _listed(ours.stdout) == _listed(oracle.stdout), where
        if oracle.returncode == 4:
            assert _errors(ours.stderr) == _errors(oracle.stderr), where


@pytest.mark.parametrize(
    "line",
    [
        "--lf test_m.py::test_a",
        "--lf test_m.py::test_a test_m.py::test_b",
        # The module holding a failure is collected first, so pytest's
        # `LFPluginCollSkipfiles` empties every later module outside the failures.
        "--lf test_m.py::test_b sub/test_n.py::test_a",
        "--lf sub/test_n.py::test_a test_m.py::test_b",
        "--ff test_m.py::test_a test_m.py::test_b",
        "--ff sub/test_n.py::test_a test_m.py::test_b",
    ],
)
def test_last_failed_applies_after_node_id_selection(tmp_path: Path, line: str) -> None:
    """Each engine is seeded by its own first run (``test_m.py::test_b`` fails)."""
    tree = make_tree(tmp_path, "lf", FILES)
    seed = ["test_m.py", "sub"]
    assert _pytest(tree, seed, cache=True).returncode == 1
    assert _rustest(tree, seed)[0].returncode == 1

    _assert_agree(tree, line.split(), cache=True)


def test_failed_first_rebuilds_a_module_it_returns_to(tmp_path: Path) -> None:
    """``--ff`` with failures in two files: pytest runs ``items = failed + passed``, so each
    module is left and re-entered, and its module fixture is built twice.  One worker, so the
    order log is one sequence."""
    module = "def test_pass():\n    pass\n\n\ndef test_fail():\n    assert False\n"
    files = {"conftest.py": FILES["conftest.py"], "test_a.py": module, "test_b.py": module}
    tree = make_tree(tmp_path, "ff", files)
    assert _pytest(tree, [], cache=True).returncode == 1
    assert _rustest(tree, [])[0].returncode == 1

    _assert_agree(tree, ["--ff"], cache=True)


# --------------------------------------------------------------------------------------
# Markdown code blocks: rustest's own tier, so there is no oracle -- the rule is the .py one
# applied to the ids the tier emits (`doc.md::codeblock_<i>_line_<n>[::test_x]`).
# --------------------------------------------------------------------------------------

DOC = """\
# Doc

```python
assert True
```

```python
def test_x():
    pass


def test_y():
    pass
```
"""


def test_node_ids_select_markdown_code_blocks_by_the_same_rule(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, "md", {"doc.md": DOC})

    def ids(*args: str) -> tuple[int, list[str]]:
        proc, executed = _rustest(tree, ["--codeblocks", *args])
        return proc.returncode, executed

    _, everything = ids("doc.md")
    block = everything[1].rsplit("::", 1)[0]
    assert everything == ["doc.md::codeblock_0_line_3", f"{block}::test_x", f"{block}::test_y"]

    # A block with tests is a collector: its id selects all of them, like a class.
    assert ids(block) == (0, [f"{block}::test_x", f"{block}::test_y"])
    assert ids(f"{block}::test_y") == (0, [f"{block}::test_y"])
    assert ids("doc.md::codeblock_0_line_3") == (0, ["doc.md::codeblock_0_line_3"])

    missing, _ = ids("doc.md::nope")
    assert missing == 4
    # ...and without the tier the file has no collector, pytest's own answer for a `.md`.
    proc, _ = _rustest(tree, ["--no-codeblocks", "doc.md::codeblock_0_line_3"])
    assert proc.returncode == 4
    assert "(no match in any of [<Dir md>])" in proc.stderr, proc.stderr
