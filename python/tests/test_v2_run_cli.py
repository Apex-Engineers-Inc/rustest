"""The default ``rustest`` CLI surface (the v2 engine), diffed against REAL pytest.

This is the second user-reachable v2 surface and the first that *runs* anything: it drives
the whole v2 spine (config -> walk -> worker pool -> manifest -> ``-k``/``-m`` selection ->
warm dispatch -> report). Every claim below is checked by running **real pytest** on the
same isolated tree in a subprocess and comparing the two.

**What is compared, and what is not.** Outcome *counts* per bucket and the process *exit
code*, never prose. pytest's skip reasons, traceback formatting and summary wording are its
own; pinning them here would make every upstream reword a red gate, and none of them is
what parity means. Node ids are compared where the question is selection.

**Why the trees are isolated.** Every layout gets its own ``pytest.ini``. Without one, both
runners walk *out* of ``tmp_path`` looking for a config file and land on this repository's
``pyproject.toml`` (it has ``[tool.pytest.ini_options]``), which makes rootdir the repo root
and every node id repo-relative.

**Why most marks below are called.** The bare (uncalled) forms used to be destroyed by the
v1 compat shim -- ``mark.skip`` was a plain method and ``mark.xfail``/``mark.skipif`` were
properties returning bound methods, so the bare form passed the test function in as
``reason``/``condition`` and the module attribute became a closure (#136) or a
``MarkDecorator`` (#137, which made the test vanish from collection under both engines).
Fixed in 1b.2 Task 6 by porting pytest's own discrimination into
``decorators.py::_mark_decoration_target``, and pinned by the ``marks/bare-marks``
conformance case, ``python/tests/test_bare_marks.py`` and
:func:`test_bare_marks_match_pytest_bucket_for_bucket` below. The other corpora here keep
calling their marks so that each test stays about the one thing it names.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

# The compiled extension is built and installed by `python/tests/__init__.py`, which runs
# `ensure_develop_installed()` before any test module is imported -- these subprocess tests
# exercise the real `rust.v2_run`, not a pure-Python stub.
from .helpers import stub_rust_module
from rustest import cli, core

# pytest's terminal summary line: "1 failed, 2 passed, 1 xfailed in 0.05s".
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|deselected|xfailed|xpassed|errors|error)\b")

_BUCKETS = ("passed", "failed", "skipped", "deselected", "xfailed", "xpassed", "error")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _clean_env() -> dict[str, str]:
    """A child environment with the ambient pytest/rustest session stripped out."""
    env = dict(os.environ)
    for leak in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST", "RUSTEST_RUNNING"):
        _ = env.pop(leak, None)
    return env


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, env=_clean_env(), check=False
    )


def _run_pytest(tree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return _run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", *args], tree
    )


def _run_v2(tree: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """The **default** invocation: since the Phase 1c flip, no mode flag means v2.

    ``--v2`` is still accepted but now prints a deprecation line to stderr, which the
    stderr-reading assertions below would have to special-case for no benefit.  The alias's
    own behaviour is pinned in ``test_v2_flip_cli.py``.
    """
    return _run([sys.executable, "-m", "rustest", *args], tree)


def _pytest_counts(stdout: str) -> dict[str, int]:
    """The per-bucket counts from pytest's terminal summary line.

    Read from the **last** line carrying any count, which is the summary; earlier lines can
    mention numbers (progress, ``FAILED ...`` entries) and must not be mistaken for it.
    ``1 error`` and ``2 errors`` are the same bucket.
    """
    counts = dict.fromkeys(_BUCKETS, 0)
    for line in reversed(stdout.splitlines()):
        found = {kind: int(number) for number, kind in _SUMMARY_RE.findall(line)}
        if found:
            for bucket in _BUCKETS:
                counts[bucket] = found.get(bucket, 0)
            counts["error"] = found.get("error", 0) + found.get("errors", 0)
            break
    return counts


def _v2_counts(report: dict[str, object]) -> dict[str, int]:
    """The same buckets out of the schema-v2 JSON report.

    ``error`` folds in ``collection_errors``: pytest counts a collect-report failure in the
    very same ``N error`` bucket as a broken fixture, so keeping them apart here would make
    every collection-error case read as a divergence about arithmetic.
    """
    summary = report["summary"]
    assert isinstance(summary, dict)
    errors = report["collection_errors"]
    assert isinstance(errors, list)
    counts = {bucket: int(summary[bucket]) for bucket in _BUCKETS}
    counts["error"] += len(errors)
    return counts


def _pytest_summary_line(stdout: str) -> str:
    """pytest's terminal summary line, with its ``in <n>s`` tail removed.

    That tail is the only part of the line that is not a claim about outcomes, so stripping
    it leaves something v2's summary can be compared to byte-for-byte. The line is found by
    scanning backwards for one carrying a count (or the literal ``no tests ran``), because
    ``FAILED ...`` entries and the progress line sit above it.
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if _SUMMARY_RE.search(stripped) or stripped.startswith("no tests ran"):
            return re.sub(r"\s+in \d[\d.]*s$", "", stripped)
    raise AssertionError(f"no pytest summary line in:\n{stdout}")


def _last_line(text: str) -> str:
    return text.strip().splitlines()[-1]


def _v2_summary_line(stderr: str) -> str:
    """v2's summary line with its ``in <n>s`` tail removed -- the same treatment
    :func:`_pytest_summary_line` gives pytest's, so the two can be compared byte for byte.

    Task 2 gave the line pytest's duration tail. It is stripped rather than matched by
    prefix so that a bucket which should not be there still fails the comparison.
    """
    return re.sub(r"\s+in \d[\d.]*s(?: \(\d+:\d\d:\d\d\))?$", "", _last_line(stderr))


def _context(label: str, proc: subprocess.CompletedProcess[str]) -> str:
    return f"--- {label} rc={proc.returncode} ---\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def _assert_matches_pytest(
    tree: Path, args: list[str] | None = None, *, counts: bool = True
) -> tuple[dict[str, int], dict[str, object]]:
    """Run both engines on the same tree; diff the outcome counts and the exit code.

    ``counts=False`` compares the exit code only, for the one shape where pytest's buckets
    are structurally uncomparable: pytest counts *reports* (up to three per test) while the
    v2 wire carries one reduced status per test, so a passing body with a broken teardown is
    ``1 passed, 1 error`` there and ``1 error`` here. See
    :func:`test_a_passing_body_with_a_broken_teardown_agrees_on_the_exit_code_only`, which
    pins that divergence rather than letting this flag hide it.
    """
    args = args or []
    report_path = tree / ".v2-report.json"
    oracle = _run_pytest(tree, args)
    ours = _run_v2(tree, [*args, "--report-json", str(report_path)])
    where = f"tree={tree} args={args}\n{_context('pytest', oracle)}\n{_context('v2', ours)}"

    report: dict[str, object] = json.loads(report_path.read_text(encoding="utf-8"))
    actual = _v2_counts(report)
    if counts:
        expected = _pytest_counts(oracle.stdout)
        assert actual == expected, f"outcome counts diverge\n{where}\nreport={report}"
    assert ours.returncode == oracle.returncode, f"exit codes diverge\n{where}"
    return actual, report


def _tree(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    tree = tmp_path / name
    _write(tree / "pytest.ini", "[pytest]\n")
    for rel, body in files.items():
        _write(tree / rel, body)
    return tree


# --------------------------------------------------------------------------------------
# The mini full-run differential
# --------------------------------------------------------------------------------------

MIXED = """\
import pytest


def test_pass():
    assert True


def test_fail():
    assert 1 == 2


@pytest.mark.skip(reason="nope")
def test_skip():
    pass


@pytest.mark.xfail(reason="known")
def test_xfail():
    assert False


@pytest.mark.xfail(reason="surprise")
def test_xpass():
    assert True


@pytest.fixture
def boom():
    raise ValueError("setup boom")


def test_error(boom):
    assert True
"""


def test_a_mixed_tree_matches_pytest_bucket_for_bucket(tmp_path: Path) -> None:
    """The headline differential: every one of the six statuses, in one run.

    A tree that produces only passes would agree with pytest for the wrong reason.
    """
    tree = _tree(tmp_path, "mixed", {"test_mixed.py": MIXED})
    counts, report = _assert_matches_pytest(tree)

    assert counts == {
        "passed": 1,
        "failed": 1,
        "skipped": 1,
        "deselected": 0,
        "xfailed": 1,
        "xpassed": 1,
        "error": 1,
    }
    tests = report["tests"]
    assert isinstance(tests, list)
    assert [test["status"] for test in tests] == [
        "passed",
        "failed",
        "skipped",
        "xfailed",
        "xpassed",
        "error",
    ]


BARE_MARKS = """\
import pytest


@pytest.mark.skip
def test_bare_skip():
    raise AssertionError("must not run")


@pytest.mark.skip(reason="called")
def test_called_skip():
    raise AssertionError("must not run")


@pytest.mark.skipif
def test_bare_skipif():
    raise AssertionError("must not run")


@pytest.mark.xfail
def test_bare_xfail():
    raise AssertionError("expected")


@pytest.mark.xfail(reason="called")
def test_called_xfail():
    raise AssertionError("expected")


def test_control():
    assert True
"""


def test_bare_marks_match_pytest_bucket_for_bucket(tmp_path: Path) -> None:
    """Defects #136 and #137, differentially: the bare forms against real pytest.

    Collection is asserted separately from the counts and deliberately *first*, because
    #137's signature was a test that was simply **not there**: ``test_bare_xfail`` and
    ``test_bare_skipif`` left no trace in either engine's output, and the run exited 0 with
    a green summary. A bucket comparison alone cannot see that -- absent tests contribute
    nothing to any bucket -- so the ids are what pins it.

    Bare ``skipif`` is included because pytest treats a conditionless ``skipif`` as an
    *unconditional skip* rather than an error (``_pytest/skipping.py::evaluate_skip_marks``
    l. 177-179), which is a rule worth having pinned against the real thing.
    """
    tree = _tree(tmp_path, "baremarks", {"test_bare.py": BARE_MARKS})

    collected = _run_pytest(tree, ["--collect-only"])
    counts, report = _assert_matches_pytest(tree)

    tests = report["tests"]
    assert isinstance(tests, list)
    ours = [str(test["id"]) for test in tests]
    theirs = [line.strip() for line in collected.stdout.splitlines() if "::" in line]
    assert ours == theirs, f"collected ids diverge\n{_context('pytest --collect-only', collected)}"

    assert counts == {
        "passed": 1,
        "failed": 0,
        "skipped": 3,
        "deselected": 0,
        "xfailed": 2,
        "xpassed": 0,
        "error": 0,
    }


def test_results_stay_in_manifest_order_across_a_worker_pool(tmp_path: Path) -> None:
    """The report is assembled by manifest index, not by completion order, so the id list is
    identical however many interpreters produced it."""
    tree = _tree(
        tmp_path,
        "ordered",
        {
            "test_a.py": "def test_a1():\n    pass\n\n\ndef test_a2():\n    pass\n",
            "sub/test_b.py": "def test_b1():\n    pass\n",
            "test_c.py": "def test_c1():\n    pass\n",
        },
    )

    orders: list[list[str]] = []
    for workers in ("1", "2", "4"):
        report_path = tree / f".r{workers}.json"
        proc = _run_v2(tree, ["-n", workers, "--report-json", str(report_path)])
        assert proc.returncode == 0, _context("v2", proc)
        report: dict[str, object] = json.loads(report_path.read_text(encoding="utf-8"))
        tests = report["tests"]
        assert isinstance(tests, list)
        orders.append([str(test["id"]) for test in tests])

    assert orders[0] == [
        "sub/test_b.py::test_b1",
        "test_a.py::test_a1",
        "test_a.py::test_a2",
        "test_c.py::test_c1",
    ]
    assert orders[1] == orders[0]
    assert orders[2] == orders[0]


# --------------------------------------------------------------------------------------
# Exit codes: one differential per probed row
# --------------------------------------------------------------------------------------

TEARDOWN_ERROR_MODULE = (
    "import pytest\n\n\n@pytest.fixture\ndef boom():\n    yield 1\n"
    "    raise ValueError('x')\n\n\ndef test_one(boom):\n    assert True\n"
)

EXIT_CODE_CASES: list[tuple[str, dict[str, str], list[str], int]] = [
    # (label, files, args, expected exit code -- asserted against pytest, not trusted)
    ("all-pass", {"test_a.py": "def test_one():\n    assert True\n"}, [], 0),
    ("failure", {"test_a.py": "def test_one():\n    assert False\n"}, [], 1),
    (
        "setup-error",
        {
            "test_a.py": "import pytest\n\n\n@pytest.fixture\ndef boom():\n"
            "    raise ValueError('x')\n\n\ndef test_one(boom):\n    pass\n"
        },
        [],
        1,
    ),
    ("collection-error", {"test_a.py": "import nope_does_not_exist\n"}, [], 2),
    (
        "collection-error-beside-a-good-file",
        {
            "test_a.py": "import nope_does_not_exist\n",
            "test_b.py": "def test_ok():\n    assert True\n",
        },
        [],
        2,
    ),
    (
        "collection-error-beside-a-failing-file",
        {
            "test_a.py": "import nope_does_not_exist\n",
            "test_b.py": "def test_bad():\n    assert False\n",
        },
        [],
        2,
    ),
    ("empty-tree", {"notes.md": "nothing here\n"}, [], 5),
    ("no-tests-in-file", {"test_a.py": "def helper():\n    pass\n"}, [], 5),
    ("deselect-all-k", {"test_a.py": "def test_one():\n    pass\n"}, ["-k", "nomatch"], 5),
    ("deselect-all-m", {"test_a.py": "def test_one():\n    pass\n"}, ["-m", "nosuch"], 5),
    (
        "deselect-some-rest-fails",
        {"test_a.py": "def test_keep():\n    assert False\n\n\ndef test_drop():\n    pass\n"},
        ["-k", "keep"],
        1,
    ),
    (
        "xfail",
        {
            "test_a.py": "import pytest\n\n\n@pytest.mark.xfail(reason='known')\n"
            "def test_one():\n    assert False\n"
        },
        [],
        0,
    ),
    (
        "xpass-plain-is-not-a-failure",
        {
            "test_a.py": "import pytest\n\n\n@pytest.mark.xfail(reason='surprise')\n"
            "def test_one():\n    assert True\n"
        },
        [],
        0,
    ),
    (
        "xpass-strict-is-a-failure",
        {
            "test_a.py": "import pytest\n\n\n@pytest.mark.xfail(reason='s', strict=True)\n"
            "def test_one():\n    assert True\n"
        },
        [],
        1,
    ),
    (
        "skipped-only",
        {
            "test_a.py": "import pytest\n\n\n@pytest.mark.skip(reason='nope')\n"
            "def test_one():\n    assert False\n"
        },
        [],
        0,
    ),
    (
        "error-and-failure",
        {
            "test_a.py": "import pytest\n\n\n@pytest.fixture\ndef boom():\n"
            "    raise ValueError('x')\n\n\ndef test_err(boom):\n    pass\n\n\n"
            "def test_fail():\n    assert False\n"
        },
        [],
        1,
    ),
]


@pytest.mark.parametrize(
    ("label", "files", "args", "expected"),
    EXIT_CODE_CASES,
    ids=[case[0] for case in EXIT_CODE_CASES],
)
def test_exit_codes_match_pytest(
    tmp_path: Path, label: str, files: dict[str, str], args: list[str], expected: int
) -> None:
    """One row per probed exit-code shape, each checked twice.

    The literal ``expected`` is a second, independent claim: without it a bug that made
    *both* engines wrong -- most plausibly a harness fault that fails to run either -- would
    read as agreement. pytest is the oracle; the literal is the tripwire.
    """
    tree = _tree(tmp_path, label, files)
    _, report = _assert_matches_pytest(tree, args)
    assert report["exit_code"] == expected


def test_a_passing_body_with_a_broken_teardown_agrees_on_the_exit_code_only(
    tmp_path: Path,
) -> None:
    """The one exit-code row whose *buckets* cannot match, stated rather than skipped.

    pytest's summary counts **reports**: ``runtestprotocol`` produces up to three per test,
    so a passing body with a raising teardown is ``1 passed, 1 error``. The v2 wire carries
    one **reduced** status per test -- the worker collapses the three phases before the
    result is ever sent (`src/v2/protocol.rs::TestResult`), and the earliest non-plain-pass
    phase wins -- so the same test is exactly ``1 error``.

    Both are right about the run: exit 1 either way, and neither reports a green test. The
    divergence is one of arithmetic, and it is pinned here so a future change to the
    reduction cannot alter it silently.
    """
    tree = _tree(tmp_path, "teardown-error", {"test_a.py": TEARDOWN_ERROR_MODULE})
    counts, report = _assert_matches_pytest(tree, counts=False)

    assert report["exit_code"] == 1
    assert counts["error"] == 1
    assert counts["passed"] == 0

    oracle = _run_pytest(tree, [])
    assert _pytest_counts(oracle.stdout) == {**dict.fromkeys(_BUCKETS, 0), "passed": 1, "error": 1}


def test_a_missing_path_argument_is_a_usage_error(tmp_path: Path) -> None:
    """Exit 4, with pytest's own ``file or directory not found`` wording."""
    tree = _tree(tmp_path, "usage", {"test_a.py": "def test_one():\n    pass\n"})

    oracle = _run_pytest(tree, ["nope_missing"])
    ours = _run_v2(tree, ["nope_missing"])

    assert oracle.returncode == 4, _context("pytest", oracle)
    assert ours.returncode == 4, _context("v2", ours)
    assert "file or directory not found: nope_missing" in ours.stderr


def test_a_non_python_file_argument_is_a_usage_error(tmp_path: Path) -> None:
    """``found no collectors`` -- exit 4, and probed as the *other* half of a suffix split:
    ``.txt``/``.rst`` are claimed by pytest's doctest tier and exit 5 instead."""
    tree = _tree(
        tmp_path,
        "nocollectors",
        {"test_a.py": "def test_one():\n    pass\n", "notes.dat": "hi\n", "notes.txt": "hi\n"},
    )

    for arg, expected in (("notes.dat", 4), ("notes.txt", 5)):
        oracle = _run_pytest(tree, [arg])
        ours = _run_v2(tree, [arg])
        assert oracle.returncode == expected, _context(f"pytest {arg}", oracle)
        assert ours.returncode == expected, _context(f"v2 {arg}", ours)


@pytest.mark.parametrize("flag", ["-k", "-m"])
def test_a_malformed_selection_expression_is_a_usage_error(tmp_path: Path, flag: str) -> None:
    """pytest's ``UsageError`` shape, message included: the wording and the 1-based column
    are what users grep for, so the port reproduces both rather than inventing a dialect."""
    tree = _tree(tmp_path, f"badexpr{flag[-1]}", {"test_a.py": "def test_one():\n    pass\n"})

    oracle = _run_pytest(tree, [flag, "and and"])
    ours = _run_v2(tree, [flag, "and and"])

    assert oracle.returncode == 4, _context("pytest", oracle)
    assert ours.returncode == 4, _context("v2", ours)
    expected = (
        f"Wrong expression passed to '{flag}': and and: "
        "at column 1: expected not OR left parenthesis OR identifier; got and"
    )
    assert expected in oracle.stderr, _context("pytest", oracle)
    assert expected in ours.stderr, _context("v2", ours)


# --------------------------------------------------------------------------------------
# Selection differentials
# --------------------------------------------------------------------------------------

SELECTION_TREE = {
    "pytest.ini": "[pytest]\nmarkers =\n    slow\n    smoke\n    net\n",
    "alpha/test_first.py": (
        "import pytest\n\n\n"
        "@pytest.mark.slow()\n"
        "def test_one():\n    pass\n\n\n"
        "def test_two():\n    pass\n\n\n"
        "@pytest.mark.net(scope='wide', retries=3)\n"
        "def test_three():\n    pass\n"
    ),
    "beta/test_second.py": (
        "import pytest\n\n\n"
        "@pytest.mark.smoke()\n"
        "class TestBox:\n"
        "    def test_method(self):\n        pass\n\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_param(n):\n    pass\n\n\n"
        "def test_UPPER():\n    pass\n"
    ),
    # A module-level `pytestmark` applies its marks to every test in the file
    # (`_pytest/python.py::Module` -> `get_unpacked_marks`), which is a different code path
    # from a decorator and from a class mark -- so `-m net` has to reach these too.
    "gamma/test_third.py": (
        "import pytest\n\n"
        "pytestmark = [pytest.mark.net(scope='wide', retries=3), pytest.mark.slow()]\n\n\n"
        "def test_module_marked():\n    pass\n"
    ),
}

SELECTION_QUERIES: list[list[str]] = [
    ["-k", "one"],
    ["-k", "alpha"],
    ["-k", "beta"],
    ["-k", "test_first"],
    ["-k", "test_first.py"],
    ["-k", "TestBox"],
    ["-k", "testbox"],
    ["-k", "param"],
    ["-k", "test_param[1]"],
    ["-k", "slow"],
    ["-k", "smoke"],
    ["-k", "one or two"],
    ["-k", "not one"],
    ["-k", "test_ and not (one or two)"],
    ["-k", "UPPER"],
    ["-k", "upper"],
    ["-k", "   "],
    ["-k", "1"],
    ["-m", "slow"],
    ["-m", "not slow"],
    ["-m", "smoke"],
    ["-m", "net"],
    ["-m", "net(scope='wide')"],
    ["-m", "net(scope='narrow')"],
    ["-m", "net(retries=3)"],
    ["-m", "net(retries=4)"],
    ["-m", "net(scope='wide', retries=3)"],
    ["-m", "slow or smoke"],
    # `and not` and a negated group: the two mark-expression shapes users actually type that
    # the flat rows above never build, and the ones a single-precedence-level parser gets
    # wrong.
    ["-m", "slow and not net"],
    ["-m", "not (slow or smoke)"],
    # Precedence at the CLI level, not just in the Rust unit tests: `and` binds tighter, so
    # this is `slow or (smoke and net)` and must not select the smoke-only test.
    ["-k", "slow or smoke and net"],
    # Module-level `pytestmark` is a third way a mark reaches a test, after the decorator
    # and the class. The `-m net` / `-m net(scope='wide')` rows above now cross that path
    # too, because `gamma/test_third.py` carries `net` at module level -- so those rows
    # select from two files by two different mechanisms, and this row addresses the
    # module-marked test by name so a regression says *which* half broke.
    ["-k", "module_marked"],
    ["-m", "   "],
    ["-k", "test_", "-m", "slow"],
    ["-k", "one", "-m", "smoke"],
]


@pytest.mark.parametrize("query", SELECTION_QUERIES, ids=lambda q: " ".join(q).replace(" ", "_"))
def test_selection_picks_the_same_tests_as_pytest(tmp_path: Path, query: list[str]) -> None:
    """Node ids, in order, from ``--v2-collect-only`` against ``pytest --collect-only -q``.

    Collect-only is the right surface for this: it isolates *which* tests selection keeps
    from what happens when they run, and the un-waiving of ``marks/mark-filter`` in the
    v2-collect gate depends on exactly this behaviour.
    """
    tree = _tree(tmp_path, "sel", SELECTION_TREE)

    oracle = _run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *query],
        tree,
    )
    ours = _run([sys.executable, "-m", "rustest", "--v2-collect-only", *query], tree)
    where = f"query={query}\n{_context('pytest', oracle)}\n{_context('v2', ours)}"

    expected: list[str] = []
    for line in oracle.stdout.splitlines():
        if not line.strip():
            break
        expected.append(line)

    assert ours.stdout.splitlines() == expected, f"selected ids diverge\n{where}"
    assert ours.returncode == oracle.returncode, f"exit codes diverge\n{where}"


def test_selection_never_hides_a_collection_error(tmp_path: Path) -> None:
    """``-k`` runs *after* collection, so a file that failed to import still exits 2 even
    when the expression would have deselected everything in it."""
    tree = _tree(
        tmp_path,
        "selerror",
        {
            "test_broken.py": "import nope_does_not_exist\n",
            "test_ok.py": "def test_ok():\n    pass\n",
        },
    )
    _assert_matches_pytest(tree, ["-k", "nomatch"])


# --------------------------------------------------------------------------------------
# Duplicate path arguments
# --------------------------------------------------------------------------------------


def test_a_file_named_twice_runs_twice_exactly_as_pytest_does(tmp_path: Path) -> None:
    """pytest's collection cache has a documented hole for files named directly on the
    command line (``Session.collect``: *"files given directly multiple times on the command
    line should not be deduplicated"*), so ``pytest a.py a.py`` really does run both tests
    twice. Deduplicating would silently run half of what was asked for."""
    tree = _tree(
        tmp_path,
        "dupes",
        {"test_a.py": "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n"},
    )

    counts, report = _assert_matches_pytest(tree, ["test_a.py", "test_a.py"])
    assert counts["passed"] == 4
    tests = report["tests"]
    assert isinstance(tests, list)
    assert [str(test["id"]) for test in tests] == [
        "test_a.py::test_one",
        "test_a.py::test_two",
        "test_a.py::test_one",
        "test_a.py::test_two",
    ]


def test_a_directory_and_a_file_inside_it_run_the_file_once(tmp_path: Path) -> None:
    """The other half of the rule, in both argument orders: the cache hole is only punched
    for a *file* argument, so a directory argument that already collected the file wins."""
    tree = _tree(tmp_path, "dupmix", {"pkg/test_b.py": "def test_three():\n    pass\n"})

    for args in (["pkg", "pkg/test_b.py"], ["pkg/test_b.py", "pkg"]):
        counts, _ = _assert_matches_pytest(tree, args)
        assert counts["passed"] == 1, args


# --------------------------------------------------------------------------------------
# The schema-v2 JSON report
# --------------------------------------------------------------------------------------


def test_the_json_report_is_schema_v2_with_six_statuses(tmp_path: Path) -> None:
    """The document the conformance harness consumes. Schema v1 had three statuses and could
    not express an xfail or an xpass at all; the whole point of v2 is that it can."""
    tree = _tree(tmp_path, "report", {"test_mixed.py": MIXED})
    report_path = tmp_path / "report.json"

    proc = _run_v2(tree, ["--report-json", str(report_path)])
    assert proc.returncode == 1, _context("v2", proc)

    report: dict[str, object] = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["version"] == 2
    assert report["exit_code"] == 1
    assert report["summary"] == {
        "total": 6,
        "passed": 1,
        "failed": 1,
        "skipped": 1,
        "xfailed": 1,
        "xpassed": 1,
        "error": 1,
        "deselected": 0,
        "duration": pytest.approx(report["summary"]["duration"]),  # pyright: ignore[reportIndexIssue]
    }
    assert report["collection_errors"] == []

    tests = report["tests"]
    assert isinstance(tests, list)
    by_id = {str(test["id"]): test for test in tests}
    assert by_id["test_mixed.py::test_fail"]["message"]
    # A plain pass carries no message at all -- an omit-when-empty rule, not a bug.
    assert "message" not in by_id["test_mixed.py::test_pass"]


def test_the_json_report_records_deselection_and_collection_errors(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        "reportmore",
        {
            "test_a.py": "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n",
            "test_broken.py": "import nope_does_not_exist\n",
        },
    )
    report_path = tmp_path / "report2.json"

    proc = _run_v2(tree, ["-k", "one", "--report-json", str(report_path)])
    assert proc.returncode == 2, _context("v2", proc)

    report: dict[str, object] = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert summary["deselected"] == 1
    errors = report["collection_errors"]
    assert isinstance(errors, list)
    assert len(errors) == 1
    assert errors[0]["path"] == "test_broken.py"
    # Nothing ran: `pytest_runtestloop` raises `Interrupted` when collection failed.
    assert report["tests"] == []


def test_a_clean_run_prints_nothing_on_stdout(tmp_path: Path) -> None:
    """stdout is reserved for failure detail, so a green run is silent there and the summary
    goes to stderr -- the same stdout/stderr split ``--v2-collect-only`` uses."""
    tree = _tree(tmp_path, "quiet", {"test_a.py": "def test_one():\n    pass\n"})

    proc = _run_v2(tree, [])
    assert proc.returncode == 0
    assert proc.stdout == "", _context("v2", proc)
    assert "1 passed" in proc.stderr


def test_a_failure_is_reported_on_stdout_without_a_report_file(tmp_path: Path) -> None:
    """A red run must be diagnosable from a terminal alone."""
    tree = _tree(tmp_path, "loud", {"test_a.py": "def test_one():\n    assert 1 == 2\n"})

    proc = _run_v2(tree, [])
    assert proc.returncode == 1
    assert "FAILED test_a.py::test_one" in proc.stdout, _context("v2", proc)
    assert "assert 1 == 2" in proc.stdout, _context("v2", proc)


def test_boundary_teardown_output_is_surfaced_but_never_fails_the_run(tmp_path: Path) -> None:
    """Class- and module-scoped teardown output is drained outside the per-test capture
    window (1b.2 Task 3's documented divergence), so it arrives on the worker's stderr on a
    completely green run. Discarding it would lose a user's ``print``; grading it would fail
    green runs."""
    tree = _tree(
        tmp_path,
        "boundary",
        {
            "test_a.py": "import pytest\n\n\n@pytest.fixture(scope='module')\n"
            "def noisy():\n    yield 1\n    print('TEARDOWN-MODULE')\n\n\n"
            "def test_one(noisy):\n    assert noisy == 1\n"
        },
    )

    proc = _run_v2(tree, [])
    assert proc.returncode == 0, _context("v2", proc)
    assert "TEARDOWN-MODULE" in proc.stderr, _context("v2", proc)


def test_a_teardown_failure_after_the_last_test_reddens_the_run(tmp_path: Path) -> None:
    """The false green this whole path exists to prevent: a module-scoped teardown that
    raises has no test left to own it, so without the worker's exit-3 channel the run would
    report ``1 passed`` and exit 0. pytest reports ``1 passed, 1 error`` and exits 1."""
    tree = _tree(
        tmp_path,
        "lateteardown",
        {
            "test_a.py": "import pytest\n\n\n@pytest.fixture(scope='module')\n"
            "def broken():\n    yield 1\n    raise ValueError('teardown boom')\n\n\n"
            "def test_one(broken):\n    assert broken == 1\n"
        },
    )
    report_path = tmp_path / "td.json"

    oracle = _run_pytest(tree, [])
    ours = _run_v2(tree, ["--report-json", str(report_path)])

    assert oracle.returncode == 1, _context("pytest", oracle)
    assert ours.returncode == 1, _context("v2", ours)

    report: dict[str, object] = json.loads(report_path.read_text(encoding="utf-8"))
    teardown_errors = report["teardown_errors"]
    assert isinstance(teardown_errors, list)
    assert len(teardown_errors) == 1
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert summary["passed"] == 1


# --------------------------------------------------------------------------------------
# CLI plumbing (no subprocess -- the wiring, not the engine)
# --------------------------------------------------------------------------------------


def test_the_cli_forwards_selection_pool_size_and_the_report_path() -> None:
    """``-k``, ``-m``, ``-n`` and ``--report-json`` all cross into v2, and the raw strings
    are forwarded rather than re-interpreted: v2 owns pytest's expression grammar, including
    its usage errors."""
    seen: list[tuple[list[str], int, str | None, str | None]] = []

    def fake_run(
        invocation_dir: str,
        args: list[str],
        python: str,
        workers: int,
        keyword: str | None,
        mark_expr: str | None,
        fail_fast: bool,
        max_fail: int,
        last_failed_mode: str,
        no_capture: bool,
        codeblocks: bool,
        assert_rewrite: str,
        coverage: str | None,
    ) -> str:
        del invocation_dir, python, fail_fast, last_failed_mode, no_capture, codeblocks
        del assert_rewrite, coverage, max_fail
        seen.append((list(args), workers, keyword, mark_expr))
        return json.dumps(
            {
                "version": 2,
                "rootdir": "/x",
                "exit_code": 0,
                "summary": {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "xfailed": 0,
                    "xpassed": 0,
                    "error": 0,
                    "deselected": 0,
                    "duration": 0.0,
                },
                "tests": [],
                "collection_errors": [],
            }
        )

    with stub_rust_module(v2_run=fake_run):
        assert cli.main(["--v2", "-k", "a and b", "-m", "slow", "-n", "3"]) == 0

    assert seen == [([], 3, "a and b", "slow")]


def test_an_absent_path_argument_is_not_forwarded_as_a_dot() -> None:
    """An omitted path is not ``.``: pytest lets ``testpaths`` decide only when *no* argument
    is given, so argparse's default object must not be forwarded as a real one."""
    seen: list[list[str]] = []

    def fake_run(
        invocation_dir: str,
        args: list[str],
        python: str,
        workers: int,
        keyword: str | None,
        mark_expr: str | None,
        fail_fast: bool,
        max_fail: int,
        last_failed_mode: str,
        no_capture: bool,
        codeblocks: bool,
        assert_rewrite: str,
        coverage: str | None,
    ) -> str:
        del invocation_dir, python, workers, keyword, mark_expr
        del fail_fast, max_fail, last_failed_mode, no_capture, codeblocks, assert_rewrite
        del coverage
        seen.append(list(args))
        return json.dumps(
            {
                "version": 2,
                "rootdir": "/x",
                "exit_code": 5,
                "summary": {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "xfailed": 0,
                    "xpassed": 0,
                    "error": 0,
                    "deselected": 0,
                    "duration": 0.0,
                },
                "tests": [],
                "collection_errors": [],
            }
        )

    with stub_rust_module(v2_run=fake_run):
        _ = cli.main(["--v2"])
        _ = cli.main(["--v2", "."])

    assert seen == [[], ["."]]


def test_an_orchestration_failure_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    """A broken pool is an INTERNALERROR (3), never a quietly empty run. Injected at the
    boundary because reaching it for real needs a broken worker pool; the Rust side raises
    ``RuntimeError`` for exactly this class of failure."""

    def boom(
        invocation_dir: str,
        args: list[str],
        python: str,
        workers: int,
        keyword: str | None,
        mark_expr: str | None,
        fail_fast: bool,
        max_fail: int,
        last_failed_mode: str,
        no_capture: bool,
        codeblocks: bool,
        assert_rewrite: str,
        coverage: str | None,
    ) -> str:
        del invocation_dir, args, python, workers, keyword, mark_expr
        del fail_fast, max_fail, last_failed_mode, no_capture, codeblocks, assert_rewrite
        del coverage
        raise RuntimeError("could not spawn the collection worker `nope -m rustest._v2_worker`")

    with stub_rust_module(v2_run=boom):
        assert core.v2_run(paths=[]) == 3

    captured = capsys.readouterr()
    assert captured.out == "", captured
    assert captured.err.startswith("INTERNALERROR: could not spawn"), captured


ALL_BUCKETS = """\
import pytest


def test_pass():
    assert True


def test_fail():
    assert False


@pytest.mark.skip(reason="x")
def test_skip():
    pass


@pytest.mark.xfail(reason="x")
def test_xfail():
    assert False


@pytest.mark.xfail(reason="x")
def test_xpass():
    assert True


@pytest.fixture
def boom():
    raise ValueError("x")


def test_error(boom):
    assert True


def test_drop():
    pass
"""


def test_the_summary_line_matches_pytests_wording_and_bucket_order(tmp_path: Path) -> None:
    """The summary line, diffed against pytest's own -- not against a hand-written literal.

    The order is ``_pytest/terminal.py::KNOWN_TYPES`` (l. 63-72) and it is **failed-first**:
    ``failed, passed, skipped, deselected, xfailed, xpassed, warnings, error``. This test
    was originally written with a literal in the *wrong* (passed-first) order, and it
    happily pinned the bug -- the exact shape the method note warns about, in the one place
    this file departed from its differential rule. It no longer departs.

    The comparison is byte-for-byte after stripping pytest's ``in <n>s`` tail, which is the
    only part of the line that is not a claim about outcomes.
    """
    tree = _tree(tmp_path, "buckets", {"test_o.py": ALL_BUCKETS})

    oracle = _run_pytest(tree, ["-k", "not drop"])
    ours = _run_v2(tree, ["-k", "not drop"])

    expected = _pytest_summary_line(oracle.stdout)
    assert expected == (
        "1 failed, 1 passed, 1 skipped, 1 deselected, 1 xfailed, 1 xpassed, 1 error"
    ), _context("pytest", oracle)
    assert _v2_summary_line(ours.stderr) == expected, _context("v2", ours)


# --------------------------------------------------------------------------------------
# The failure report (Task 2): pytest's section structure, not pytest's tracebacks
# --------------------------------------------------------------------------------------

FAILING_TREE = """\
import pytest


def test_pass():
    assert True


def test_bad():
    x = 1
    assert x == 2


@pytest.fixture
def boom():
    raise ValueError("setup boom")


def test_err(boom):
    assert True


class TestBox:
    def test_method(self):
        assert False
"""


def _rules(text: str, sepchar: str) -> list[str]:
    """Every ``=== title ===`` (or ``___ title ___``) title in *text*, in order.

    Reads the titles rather than the whole lines so the comparison is about *structure* --
    which sections exist and what heads each block -- and not about the console width the
    two processes happened to see.
    """
    pattern = re.compile(rf"^{re.escape(sepchar)}+ (.+?) {re.escape(sepchar)}+$")
    return [match.group(1) for line in text.splitlines() if (match := pattern.match(line))]


def test_the_failure_report_has_pytests_section_structure(tmp_path: Path) -> None:
    """``ERRORS``, then ``FAILURES``, then ``short test summary info`` -- diffed against
    real pytest's own default-verbosity run rather than against a transcribed literal.

    pytest is invoked *without* ``-q --tb=no`` here, unlike everywhere else in this module:
    ``--tb=no`` suppresses the very sections under test. Only the section titles and their
    order are compared. The traceback *inside* a block is produced by the v2 worker's
    ``traceback`` module rather than by pytest's ``ExceptionInfo``, and pinning its wording
    would make every upstream reword a red gate for no parity gained.
    """
    tree = _tree(tmp_path, "failreport", {"test_f.py": FAILING_TREE})

    oracle = _run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], tree)
    ours = _run_v2(tree, [])

    assert ours.returncode == oracle.returncode == 1, _context("v2", ours)
    oracle_rules = _rules(oracle.stdout, "=")
    assert oracle_rules[:4] == [
        "test session starts",
        "ERRORS",
        "FAILURES",
        "short test summary info",
    ], _context("pytest", oracle)
    # pytest's fifth rule is its summary line, wrapped in `=` at default verbosity.
    assert re.fullmatch(r"2 failed, 1 passed, 1 error in .+", oracle_rules[4]), oracle_rules

    # Two deliberate absences on the v2 side, asserted rather than left to be noticed:
    # there is no session-start banner (v2 prints no platform/plugin preamble at all, so the
    # first thing on stdout is the first failure section), and the summary line is **not**
    # wrapped in a rule -- it stays pytest's bare `-q` spelling on **stderr**, which is what
    # keeps it byte-comparable and keeps a redirected stdout free of diagnostics.
    assert _rules(ours.stdout, "=") == [
        "ERRORS",
        "FAILURES",
        "short test summary info",
    ], _context("v2", ours)
    assert _rules(ours.stderr, "=") == [], _context("v2", ours)


def test_each_failure_block_is_headed_by_pytests_domain(tmp_path: Path) -> None:
    """The ``___ headline ___`` line: pytest's ``TestReport.head_line``, diffed.

    The headline is the node id **minus its path**, with ``::`` written as ``.`` -- so a
    class method reads ``TestBox.test_method``. Taking the full id instead would be the
    obvious shortcut and would not be pytest-shaped; taking the bare function name would
    lose the class. Both halves are in this tree.

    v2 heads an error block ``ERROR <domain>`` where pytest writes ``ERROR at setup of
    <domain>``: the wire carries a *reduced* status per test and not the phase that produced
    it, so the phase would be a guess. That single deviation is asserted here rather than
    left to be noticed.
    """
    tree = _tree(tmp_path, "headlines", {"test_f.py": FAILING_TREE})

    oracle = _run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], tree)
    ours = _run_v2(tree, [])

    assert _rules(oracle.stdout, "_") == [
        "ERROR at setup of test_err",
        "test_bad",
        "TestBox.test_method",
    ], _context("pytest", oracle)
    assert _rules(ours.stdout, "_") == [
        "ERROR test_err",
        "test_bad",
        "TestBox.test_method",
    ], _context("v2", ours)


def test_the_short_summary_lists_full_node_ids_failures_before_errors(tmp_path: Path) -> None:
    """``short test summary info`` is where the *full* node id lives, on both sides.

    Order is pytest's default ``-r`` value, ``fE`` -- failures then errors -- which is why
    the section is not simply the report in test order (``test_err`` is collected before
    ``TestBox.test_method`` and listed after it).
    """
    tree = _tree(tmp_path, "shortsummary", {"test_f.py": FAILING_TREE})

    oracle = _run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], tree)
    ours = _run_v2(tree, [])

    def entries(text: str) -> list[str]:
        return [
            line.split(" - ")[0].strip()
            for line in text.splitlines()
            if line.startswith(("FAILED ", "ERROR "))
        ]

    expected = [
        "FAILED test_f.py::test_bad",
        "FAILED test_f.py::TestBox::test_method",
        "ERROR test_f.py::test_err",
    ]
    assert entries(oracle.stdout) == expected, _context("pytest", oracle)
    assert entries(ours.stdout) == expected, _context("v2", ours)


def test_the_failure_body_still_carries_the_workers_message(tmp_path: Path) -> None:
    """Structure is pytest's; the *content* is still the ``message`` the worker sent.

    Without this the sections above could be perfectly shaped and empty, which is a worse
    terminal experience than the un-sectioned block they replaced.
    """
    tree = _tree(tmp_path, "body", {"test_a.py": "def test_one():\n    assert 1 == 2\n"})

    ours = _run_v2(tree, [])

    assert "assert 1 == 2" in ours.stdout, _context("v2", ours)
    assert "ValueError" not in ours.stdout


@pytest.mark.parametrize("title", ["FAILURES", "ERRORS", "short test summary info", "x", "a" * 200])
@pytest.mark.parametrize("sepchar", ["=", "_"])
def test_the_separator_arithmetic_is_pytests_own(sepchar: str, title: str) -> None:
    """``core._sep`` against ``_pytest._io.terminalwriter.TerminalWriter.sep`` itself.

    This is the one place the port can be diffed against pytest *in process* rather than
    through a subprocess, so it is: same width source, same title-centring arithmetic, same
    "add a final sepchar if one more fits" rule, same ``win32`` column reservation. The long
    and single-character titles are the two ends where the ``max(..., 1)`` clamp and the
    trailing-fill rule respectively decide the answer.
    """
    from _pytest._io.terminalwriter import TerminalWriter

    buffer = io.StringIO()
    TerminalWriter(buffer).sep(sepchar, title)

    assert core._sep(sepchar, title) == buffer.getvalue().rstrip("\n")  # pyright: ignore[reportPrivateUsage]


# --------------------------------------------------------------------------------------
# The summary line
# --------------------------------------------------------------------------------------


def test_the_summary_line_carries_pytests_duration_tail(tmp_path: Path) -> None:
    """``N passed in 0.05s`` -- the tail pytest puts on its own ``-q`` summary line.

    Diffed on *shape*, since a wall clock cannot be compared to a literal: both lines must
    match the same tail pattern, and stripping the tail must leave the same bucket text.
    ``summary.duration`` is v2's orchestrator wall clock, so the two numbers are not
    expected to agree -- only the fact that a duration is reported at all.
    """
    tree = _tree(tmp_path, "duration", {"test_a.py": "def test_one():\n    assert True\n"})

    oracle = _run_pytest(tree, [])
    ours = _run_v2(tree, [])

    tail = re.compile(r"^1 passed in \d+\.\d\ds$")
    assert tail.match(_last_line(oracle.stdout)), _context("pytest", oracle)
    assert tail.match(_last_line(ours.stderr)), _context("v2", ours)
    # ...and the part before the tail is still byte-identical, which is what the bucket
    # comparisons elsewhere in this module rely on.
    assert _v2_summary_line(ours.stderr) == _pytest_summary_line(oracle.stdout)


def test_a_long_run_gets_pytests_bracketed_hms_duration() -> None:
    """Above a minute pytest appends ``(H:MM:SS)``; ``in 3754.10s`` is not a readable number.

    Unit-tested rather than run for a minute. Both spellings and the 60-second threshold are
    ``_pytest/_io/terminalwriter.py::format_session_duration``'s.
    """
    assert core._format_duration(0.0) == "0.00s"  # pyright: ignore[reportPrivateUsage]
    assert core._format_duration(59.994) == "59.99s"  # pyright: ignore[reportPrivateUsage]
    assert core._format_duration(60.0) == "60.00s (0:01:00)"  # pyright: ignore[reportPrivateUsage]
    assert core._format_duration(3754.1) == "3754.10s (1:02:34)"  # pyright: ignore[reportPrivateUsage]


def test_an_empty_run_says_no_tests_ran_exactly_as_pytest_does(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "emptysummary", {"notes.md": "nothing\n"})

    oracle = _run_pytest(tree, [])
    ours = _run_v2(tree, [])

    assert _pytest_summary_line(oracle.stdout) == "no tests ran", _context("pytest", oracle)
    assert _v2_summary_line(ours.stderr) == "no tests ran", _context("v2", ours)


@pytest.mark.parametrize("broken", [1, 2], ids=["one-error", "two-errors"])
def test_a_collection_error_run_says_error_not_no_tests_ran(tmp_path: Path, broken: int) -> None:
    """The terminal line for an interrupted run must not read like an empty tree.

    pytest reports a failed collect report in the *same* ``error`` bucket a broken fixture
    gets, so it prints ``1 error`` and exits 2. Before the fold, v2 printed ``no tests
    ran`` -- literally its exit-5 wording -- for a run stopped by a broken import.

    Parametrized over one and two broken files because ``error`` is the one bucket pytest
    spells by count (``1 error`` but ``2 errors``), and a single-file case cannot see that.
    """
    files = {f"test_bad{i}.py": f"import nope_missing_{i}\n" for i in range(broken)}
    tree = _tree(tmp_path, f"cerr{broken}", files)

    oracle = _run_pytest(tree, [])
    ours = _run_v2(tree, [])

    assert oracle.returncode == 2 and ours.returncode == 2, _context("v2", ours)
    expected = _pytest_summary_line(oracle.stdout)
    assert expected == ("1 error" if broken == 1 else "2 errors"), _context("pytest", oracle)
    assert _v2_summary_line(ours.stderr) == expected, _context("v2", ours)


# --------------------------------------------------------------------------------------
# The Phase 4 final polish wave (Task 1c review findings I2 and I8)
# --------------------------------------------------------------------------------------


def test_a_flagless_module_level_skip_is_refused_in_pytests_own_words(tmp_path: Path) -> None:
    """FINDING I8 -- the refusal message was ported and never pinned.

    `pytest.skip()` at module scope *without* ``allow_module_level=True`` is not a skip: it
    is a collection error, and the error text is a teaching message pytest wrote deliberately
    (`_pytest/python.py::importtestmodule` l. 538-542). It is the only thing standing between
    an author and a file that silently stops being collected, so a paraphrase would be worse
    than useless -- and a ported constant with no test is one refactor away from a paraphrase.

    Both runners must refuse, both must exit 2, and the *sentence* must be pytest's.
    """
    tree = _tree(
        tmp_path,
        "flagless",
        {
            "test_flagless.py": "import pytest\n\npytest.skip('no flag')\n\n\ndef test_x():\n    pass\n",
        },
    )

    # NOT `_run_pytest`: that helper passes `--tb=no`, which is right for every counts-only
    # assertion in this file and suppresses the very sentence under test here.
    oracle = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], tree)
    ours = _run_v2(tree, ["-v"])

    assert oracle.returncode == 2, _context("pytest", oracle)
    assert ours.returncode == 2, _context("v2", ours)

    sentence = (
        "Using pytest.skip outside of a test will skip the entire module. "
        "If that's your intention, pass `allow_module_level=True`. "
        "If you want to skip a specific test or an entire class, "
        "use the @pytest.mark.skip or @pytest.mark.skipif decorators."
    )
    assert sentence in oracle.stdout, _context("pytest", oracle)
    assert sentence in (ours.stdout + ours.stderr), _context("v2", ours)


@pytest.mark.parametrize(
    ("call", "typename"),
    [
        ("pytest.fail('boom at module level')", "Failed"),
        ("pytest.xfail('xf at module level')", "XFailed"),
    ],
    ids=["fail", "xfail"],
)
def test_a_module_level_fail_or_xfail_is_a_collection_error_not_a_dead_worker(
    tmp_path: Path, call: str, typename: str
) -> None:
    """The semantics-review regression: exit **3** where pytest says **2**.

    ``pytest.skip()`` at module scope had an arm (``MODULE_SKIP_EXCEPTIONS``) and its two
    siblings did not. ``OutcomeException`` is a ``BaseException`` on purpose, so
    ``collect_file``'s ``except Exception`` handler could not see a ``Failed``/``XFailed``:
    it escaped the worker, the worker died, and ``src/v2/py.rs`` reported an *internal*
    failure at exit 3 -- **the whole run lost to one bad file**, and the second file below
    never ran. pytest files it as an ordinary collection error at exit 2 and keeps going.

    The second file is the load-bearing half of this test. Exit 2 alone would also be
    produced by a worker that died in a slightly tidier way; ``test_ok.py`` still having been
    collected is what distinguishes "one file errored" from "the run stopped".
    """
    tree = _tree(
        tmp_path,
        f"modoutcome-{typename}",
        {
            "test_modoutcome.py": f"import pytest\n\n{call}\n\n\ndef test_never():\n    pass\n",
            "test_ok.py": "def test_y():\n    pass\n",
        },
    )

    # NOT `_run_pytest`: `--tb=no` would suppress the message this asserts on.
    oracle = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], tree)
    ours = _run_v2(tree, [])

    assert oracle.returncode == 2, _context("pytest", oracle)
    assert ours.returncode == 2, _context("v2", ours)

    for label, proc in (("pytest", oracle), ("v2", ours)):
        blob = proc.stdout + proc.stderr
        assert typename in blob, _context(label, proc)
        assert "at module level" in blob, _context(label, proc)
        assert "1 error" in blob, _context(label, proc)


def test_a_module_level_fail_without_a_traceback_prints_the_message_alone(
    tmp_path: Path,
) -> None:
    """``pytrace=False`` is the one branch that is not "format the traceback".

    `_pytest/nodes.py::Node._repr_failure_py` l. 481-484 renders a ``fail.Exception`` that
    asked for no traceback with ``style="value"`` -- the message and nothing else. A terse
    "this platform is unsupported, and here is why" line is the whole point of the flag, and
    burying it in ten frames of import plumbing would undo it.
    """
    tree = _tree(
        tmp_path,
        "modfailnp",
        {
            "test_modfailnp.py": (
                "import pytest\n\npytest.fail('terse and deliberate', pytrace=False)\n"
            ),
        },
    )

    oracle = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], tree)
    ours = _run_v2(tree, [])

    assert oracle.returncode == 2, _context("pytest", oracle)
    assert ours.returncode == 2, _context("v2", ours)

    blob = ours.stdout + ours.stderr
    assert "terse and deliberate" in blob, _context("v2", ours)
    # The discriminator: with a traceback the raising frame is quoted by name.
    assert "pytest.fail(" not in blob, _context("v2", ours)
    assert "pytest.fail(" not in oracle.stdout, _context("pytest", oracle)


def test_the_flagged_form_is_a_skip_not_an_error(tmp_path: Path) -> None:
    """The control: one word of difference turns the refusal above into a clean skip."""
    tree = _tree(
        tmp_path,
        "flagged",
        {
            "test_flagged.py": (
                "import pytest\n\npytest.skip('with flag', allow_module_level=True)\n"
                "\n\ndef test_x():\n    pass\n"
            ),
            "test_ok.py": "def test_y():\n    pass\n",
        },
    )
    counts, _report = _assert_matches_pytest(tree)
    assert counts["skipped"] == 1 and counts["passed"] == 1 and counts["error"] == 0


_XUNIT_LOG = """\
import pathlib

LOG = pathlib.Path(__file__).with_name("events.log")


def _note(event):
    with LOG.open("a", encoding="utf-8") as handle:
        print(event, file=handle)


def setup_module(module):
    _note("setup_module")


def teardown_module(module):
    _note("teardown_module")


def setup_function(function):
    _note("setup_function:" + function.__name__)


def teardown_function(function):
    _note("teardown_function:" + function.__name__)


class TestBox:
    @classmethod
    def setup_class(cls):
        _note("setup_class")

    @classmethod
    def teardown_class(cls):
        _note("teardown_class")

    def setup_method(self, method):
        _note("setup_method:" + method.__name__)

    def teardown_method(self, method):
        _note("teardown_method:" + method.__name__)

    def test_in_class(self):
        _note("test_in_class")


def test_at_module_level():
    _note("test_at_module_level")
"""


def test_every_xunit_teardown_runs_in_pytests_order(tmp_path: Path) -> None:
    """FINDING I8 -- ``teardown_module`` had no assertion anywhere.

    The corpus case `collection/xunit-setup` asserts ``teardown_function``,
    ``teardown_method`` and ``teardown_class`` from inside the module, which is as far as an
    in-process event log can see: **``teardown_module`` runs after the last test in the
    file**, so nothing in that file can ever observe it. It was recorded and never checked.

    A file on disk can see it, and this compares the whole log against real pytest's --
    order included. That also makes it a live pin on the *interleaving* (a teardown belongs
    between two tests, not batched at the end), which is the property a naive "run all the
    finalizers at shutdown" implementation gets wrong while still calling every hook.
    """
    logs: dict[str, list[str]] = {}
    for label, runner in (("pytest", _run_pytest), ("v2", _run_v2)):
        tree = _tree(tmp_path, f"xunit-{label}", {"test_xunit_log.py": _XUNIT_LOG})
        proc = runner(tree, [])
        assert proc.returncode == 0, _context(label, proc)
        logs[label] = (tree / "test_xunit_log.py").with_name("events.log").read_text().split()

    assert logs["pytest"] == [
        "setup_module",
        "setup_class",
        "setup_method:test_in_class",
        "test_in_class",
        "teardown_method:test_in_class",
        "teardown_class",
        "setup_function:test_at_module_level",
        "test_at_module_level",
        "teardown_function:test_at_module_level",
        "teardown_module",
    ]
    assert logs["v2"] == logs["pytest"]


def test_an_approx_failure_prints_the_repr_compare_table(tmp_path: Path) -> None:
    """FINDING I2 -- ``ApproxBase._repr_compare`` reaching the failure output, end to end.

    The port of `_pytest/python_api.py` has carried ``_repr_compare`` since Phase 4 Task 1
    (M9), but `_assertion.py`'s ``_compare_eq_any`` never called it -- and its module
    docstring still said the method did not exist. So a failing ``assert x == approx(y)``
    printed the generic explanation while the table pytest prints sat unreachable in the port.

    This is the end-to-end shape: through the assertion **rewriter**, out of the real CLI, and
    compared against real pytest's own rendering of the same failure. The header and the
    ``Index | Obtained | Expected`` columns are pytest's, so they are asserted literally
    rather than by substring-of-a-substring.
    """
    body = "def test_seq():\n    assert [0.1, 0.2] == __import__('pytest').approx([0.1, 0.3])\n"
    tree = _tree(tmp_path, "approxtable", {"test_approx_table.py": body})

    oracle = _run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--tb=long", "-q"], tree
    )
    ours = _run_v2(tree, [])

    assert oracle.returncode == 1, _context("pytest", oracle)
    assert ours.returncode == 1, _context("v2", ours)

    for fragment in (
        "comparison failed",
        "Index | Obtained",
        "Expected",
    ):
        assert fragment in oracle.stdout, _context("pytest", oracle)
        assert fragment in (ours.stdout + ours.stderr), _context("v2", ours)


def _approx_explanation(tmp_path: Path, name: str, body: str) -> tuple[str, str]:
    """Both runners' rendering of one failing assertion, as (pytest, v2) blobs.

    ``--tb=long`` on the oracle and no ``-q`` on ours, because the explanation block under
    the ``assert`` line is the entire subject and both helpers' defaults suppress it.
    """
    tree = _tree(tmp_path, name, {f"test_{name}.py": body})
    oracle = _run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--tb=long", "-q"], tree
    )
    ours = _run_v2(tree, [])
    assert oracle.returncode == 1, _context("pytest", oracle)
    assert ours.returncode == 1, _context("v2", ours)
    return oracle.stdout, ours.stdout + ours.stderr


def test_the_approx_table_appears_when_approx_is_the_LEFT_operand(tmp_path: Path) -> None:
    """FINDING I2, second branch -- ``_compare_eq_any`` picks the approx side either way.

    `_assertion.py` reads *"Although the common order should be obtained == expected, this
    ensures both ways"* -- pytest's own comment -- and then selects
    ``left if isinstance(left, ApproxBase) else right``. Only the right-hand spelling was
    pinned, so the selector could be simplified to ``right`` and every existing test would
    still pass while ``assert approx(y) == x`` silently lost its table.
    """
    oracle, ours = _approx_explanation(
        tmp_path,
        "approxleft",
        "import pytest\n\n\ndef test_left():\n    assert pytest.approx([0.1, 0.3]) == [0.1, 0.2]\n",
    )
    for fragment in ("comparison failed", "Index | Obtained", "Max relative difference"):
        assert fragment in oracle, f"--- pytest ---\n{oracle}"
        assert fragment in ours, f"--- v2 ---\n{ours}"


def test_the_approx_numpy_shape_mismatch_is_its_own_two_line_answer(tmp_path: Path) -> None:
    """FINDING I2, the branch that returns *before* building a table.

    ``ApproxNumpy._repr_compare`` refuses two arrays of different shapes with two lines and
    no columns (`python_api.py` l. 171-190 in this port). It is the branch a reader is most
    likely to drop when trimming, because it looks like an early return rather than output --
    and dropping it does not raise, it produces the generic explanation instead. numpy is a
    hard dev-dependency for exactly this reason (see `pyproject.toml`: a verdict that changes
    with whether numpy happens to be installed is worse than no verdict).
    """
    oracle, ours = _approx_explanation(
        tmp_path,
        "approxshape",
        "import numpy as np\nimport pytest\n\n\ndef test_shape():\n"
        "    assert np.array([1.0, 2.0]) == pytest.approx(np.array([1.0, 2.0, 3.0]))\n",
    )
    for fragment in (
        "Impossible to compare arrays with different shapes.",
        "Shapes: (3,) and (2,)",
    ):
        assert fragment in oracle, f"--- pytest ---\n{oracle}"
        assert fragment in ours, f"--- v2 ---\n{ours}"
    # The discriminator against "it fell through to the table branch".
    assert "Index | Obtained" not in ours, f"--- v2 ---\n{ours}"


def test_a_plain_sequence_failure_did_not_regress_when_approx_was_wired_in(
    tmp_path: Path,
) -> None:
    """The no-regression control for I2: the branch was *added* to ``_compare_eq_any``.

    An ``isinstance(left, ApproxBase) or isinstance(right, ApproxBase)`` arm placed before
    the sequence arm is one indentation slip away from swallowing the ordinary case, and the
    symptom would be a *worse explanation*, not an error -- the kind of regression a suite
    that only asserts exit codes never sees.
    """
    oracle, ours = _approx_explanation(
        tmp_path,
        "approxnone",
        "def test_plain():\n    assert [1, 2, 3] == [1, 9, 3]\n",
    )
    for fragment in ("At index 1 diff: 2 != 9", "Use -v to get more diff"):
        assert fragment in oracle, f"--- pytest ---\n{oracle}"
        assert fragment in ours, f"--- v2 ---\n{ours}"
    assert "comparison failed" not in ours, f"--- v2 ---\n{ours}"
