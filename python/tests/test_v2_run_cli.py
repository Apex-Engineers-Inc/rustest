"""The ``rustest --v2`` CLI surface, diffed against REAL pytest.

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

**Why every mark is called.** ``@pytest.mark.xfail`` (uncalled) is destroyed by rustest's v1
compat shim -- ``MarkGenerator.xfail`` is a plain method, so the bare form passes the test
function in as ``reason`` and the module attribute becomes the inner closure. Recorded as
defect #137 in the 1b.2 Task 3 report; corpora here call their marks so these tests are
about execution rather than about that defect.
"""

from __future__ import annotations

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
    return _run([sys.executable, "-m", "rustest", "--v2", *args], tree)


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
    ) -> str:
        del invocation_dir, python
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
    ) -> str:
        del invocation_dir, python, workers, keyword, mark_expr
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
    ) -> str:
        del invocation_dir, args, python, workers, keyword, mark_expr
        raise RuntimeError("could not spawn the collection worker `nope -m rustest._v2_worker`")

    with stub_rust_module(v2_run=boom):
        assert core.v2_run(paths=[]) == 3

    captured = capsys.readouterr()
    assert captured.out == "", captured
    assert captured.err.startswith("INTERNALERROR: could not spawn"), captured


def test_the_summary_line_omits_empty_buckets(capsys: pytest.CaptureFixture[str]) -> None:
    """pytest never writes ``0 xfailed``; neither does this. An all-zero tally becomes
    ``no tests ran``, which is pytest's own wording for the exit-5 shape."""
    summary = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "error": 0,
        "deselected": 0,
        "duration": 0.0,
    }
    assert core._run_summary(summary) == "no tests ran"  # pyright: ignore[reportPrivateUsage]

    filled = {**summary, "passed": 2, "failed": 1, "xpassed": 1, "error": 1}
    assert (
        core._run_summary(filled)  # pyright: ignore[reportPrivateUsage]
        == "2 passed, 1 failed, 1 xpassed, 1 error"
    )

    # `error` is the one bucket pytest spells by count.
    plural = {**summary, "error": 2}
    assert core._run_summary(plural) == "2 errors"  # pyright: ignore[reportPrivateUsage]

    # Collection errors land in the same bucket, because pytest puts them there. Without
    # this an exit-2 run prints "no tests ran" -- the wording for an empty tree.
    assert core._run_summary(summary, 1) == "1 error"  # pyright: ignore[reportPrivateUsage]
    assert core._run_summary(plural, 1) == "3 errors"  # pyright: ignore[reportPrivateUsage]
    _ = capsys.readouterr()


def test_a_collection_error_run_says_error_not_no_tests_ran(tmp_path: Path) -> None:
    """The terminal line for an interrupted run must not read like an empty tree.

    pytest prints ``1 error`` and exits 2; before this was folded in, v2 printed ``no tests
    ran`` -- literally its exit-5 wording -- for a run stopped by a broken import.
    """
    tree = _tree(tmp_path, "cerr", {"test_bad.py": "import nope_does_not_exist\n"})

    oracle = _run_pytest(tree, [])
    ours = _run_v2(tree, [])

    assert oracle.returncode == 2 and ours.returncode == 2, _context("v2", ours)
    assert _pytest_counts(oracle.stdout)["error"] == 1, _context("pytest", oracle)
    summary_line = ours.stderr.strip().splitlines()[-1]
    assert summary_line == "1 error", _context("v2", ours)
