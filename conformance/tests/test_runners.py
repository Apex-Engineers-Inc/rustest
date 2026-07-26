from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

from conformance.harness.runners import (
    CollectResult,
    FullRunResult,
    RunOutcomes,
    _check_pytest_collect_exit,
    _run,
    _check_pytest_exit,
    _isolate_case,
    parse_pytest_collect,
    parse_pytest_summary,
    run_pytest,
    run_pytest_collect,
    run_pytest_full,
    run_rustest,
    run_rustest_v2_collect,
    run_rustest_v2_run,
)

COLLECT_OUTPUT = textwrap.dedent(
    """\
    test_a.py::test_one
    test_a.py::TestBox::test_two[x]

    2 tests collected in 0.01s
    """
)

MINI_SUITE = textwrap.dedent(
    """\
    def test_one():
        assert True


    def test_two():
        assert False


    class TestBox:
        def test_in_class(self):
            assert True
    """
)

# pytest emits a module's ids in source order, so this doubles as the ordered
# expectation for the collect gate. MINI_IDS stays a set for the v1 runners, which
# grade on membership only.
MINI_IDS_ORDERED = [
    "test_mini.py::test_one",
    "test_mini.py::test_two",
    "test_mini.py::TestBox::test_in_class",
]
MINI_IDS = set(MINI_IDS_ORDERED)


def test_parse_pytest_collect() -> None:
    assert parse_pytest_collect(COLLECT_OUTPUT) == [
        "test_a.py::test_one",
        "test_a.py::TestBox::test_two[x]",
    ]


COLLECT_OUTPUT_WITH_TRACEBACK = textwrap.dedent(
    """\
    test_a.py::test_one
    test_a.py::TestBox::test_two[x]

    =================================== ERRORS ====================================
    ______________________ ERROR collecting test_broken.py ________________________
        assert path == "src::main::foo"
    E   AssertionError: mismatch bar::baz

    2 tests collected, 1 error in 0.01s
    """
)


def test_parse_pytest_collect_ignores_traceback_line_with_double_colon() -> None:
    """A traceback/source line containing ``::`` must never read as a phantom nodeid.

    Both extra lines below contain a literal ``::`` and would have slipped past the
    old heuristic (which only excluded blank lines and a fixed set of prefixes) had
    they not happened to start with one of those prefixes. They must still be
    excluded: real nodeids are always flush at column 0, and an indented source
    line or an ``E   ...`` assertion line is not.
    """
    assert parse_pytest_collect(COLLECT_OUTPUT_WITH_TRACEBACK) == [
        "test_a.py::test_one",
        "test_a.py::TestBox::test_two[x]",
    ]


COLLECT_OUTPUT_WITH_SLICE_SYNTAX_ERROR = textwrap.dedent(
    """\
    test_sibling_ok.py::test_alpha

    =================================== ERRORS ====================================
    ___________ ERROR collecting test_slice_syntax_error.py ____________
    E     File "test_slice_syntax_error.py", line 3
    E       x = data[::2
    E               ^
    E   SyntaxError: '[' was never closed
    =========================== short test summary info ===========================
    ERROR test_slice_syntax_error.py
    !!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 test collected, 1 error in 0.29s
    """
)


def test_parse_pytest_collect_ignores_syntax_error_slice_echo_golden() -> None:
    """Golden-text regression for the reviewer-found escape (fast, no subprocess).

    A collection-time SyntaxError's echoed source line can itself contain a
    slice (``data[::2``), which pytest prints as ``E       x = data[::2`` --
    flush at column 0, matching _NODEID_RE's per-line shape on its own, since a
    slice's "::" has no preceding bare colon to disqualify it the way
    "AssertionError: ...::..." does. This is captured verbatim from a real
    pytest run (see test_parse_pytest_collect_ignores_syntax_error_slice_echo_real_pytest)
    as a fast golden fixture for CI.
    """
    assert parse_pytest_collect(COLLECT_OUTPUT_WITH_SLICE_SYNTAX_ERROR) == [
        "test_sibling_ok.py::test_alpha",
    ]


def test_parse_pytest_collect_ignores_syntax_error_slice_echo_real_pytest(
    tmp_path: Path,
) -> None:
    """Reproduce the reviewer's escape against real pytest, not a hand-written golden.

    ``test_aaa_broken.py`` sorts alphabetically before the valid sibling, which
    matters: it proves pytest front-loads every successfully collected nodeid
    before any error block regardless of traversal order, so stopping at the
    first section boundary in parse_pytest_collect never loses a real id.
    """
    (tmp_path / "test_aaa_broken.py").write_text(
        "def test_placeholder():\n    data = [1, 2, 3]\n    x = data[::2\n",
        encoding="utf-8",
    )
    (tmp_path / "test_zzz_good.py").write_text(
        "def test_after_alpha():\n    assert True\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2  # collection error

    ids = parse_pytest_collect(proc.stdout)

    assert ids == ["test_zzz_good.py::test_after_alpha"]
    assert not any("data[" in i for i in ids)


def test_parse_pytest_summary() -> None:
    out = parse_pytest_summary("1 failed, 2 passed, 1 skipped in 0.05s\n", exit_code=1)
    assert (out.passed, out.failed, out.skipped, out.errors) == (2, 1, 1, 0)
    assert out.exit_code == 1
    assert out.collection_error is False
    err = parse_pytest_summary("no tests ran in 0.01s\n", exit_code=2)
    assert err.collection_error is True


def test_parse_pytest_summary_counts_errors() -> None:
    singular = parse_pytest_summary("2 passed, 1 error in 0.10s\n", exit_code=1)
    assert (singular.passed, singular.failed, singular.skipped, singular.errors) == (2, 0, 0, 1)
    plural = parse_pytest_summary("1 failed, 3 errors in 0.10s\n", exit_code=1)
    assert (plural.passed, plural.failed, plural.skipped, plural.errors) == (0, 1, 0, 3)


def test_parse_pytest_summary_exit_5_is_not_collection_error() -> None:
    """Exit 5 (no tests collected) is a real outcome, distinct from the exit-2 bucket.

    ``collection_error`` is keyed off exit code 2 specifically; exit 5 must not be
    conflated with it even though both leave zero tests in the passed/failed/skipped
    buckets.
    """
    out = parse_pytest_summary("no tests ran in 0.01s\n", exit_code=5)
    assert out.exit_code == 5
    assert out.collection_error is False
    assert (out.passed, out.failed, out.skipped, out.errors) == (0, 0, 0, 0)


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr="boom")


def test_check_pytest_exit_passes_through_exit_5() -> None:
    """Exit 5 (no tests collected) is a comparable outcome, not a harness fault.

    ``pytest -m nosuchmark`` genuinely exits 5 while rustest exits 0 for the same
    invocation -- a real product divergence the grader must see, not one the harness
    should mask by raising.
    """
    _check_pytest_exit(_completed(5), "run")  # must not raise


@pytest.mark.parametrize("returncode", [3, 4])
def test_check_pytest_exit_raises_on_internal_and_usage_errors(returncode: int) -> None:
    with pytest.raises(RuntimeError, match=r"pytest run failed \(exit \d+\)"):
        _check_pytest_exit(_completed(returncode), "run")


def _write_mini_suite(root: Path) -> None:
    (root / "test_mini.py").write_text(MINI_SUITE, encoding="utf-8")


def test_run_pytest_integration(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)
    result = run_pytest(tmp_path, [])
    assert result.ids == MINI_IDS
    assert (result.outcomes.passed, result.outcomes.failed) == (2, 1)
    assert result.outcomes.errors == 0


def test_run_rustest_integration(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)
    result = run_rustest(tmp_path, [])
    assert result.ids == MINI_IDS
    assert (result.outcomes.passed, result.outcomes.failed) == (2, 1)
    assert result.outcomes.errors == 0


def test_run_pytest_ignores_surrounding_project_config(tmp_path: Path) -> None:
    """A pytest config above the case dir must not influence the run."""
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n", encoding="utf-8")
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    result = run_pytest(case_dir, [])
    assert result.ids == MINI_IDS
    assert (result.outcomes.passed, result.outcomes.failed) == (2, 1)


def test_run_pytest_accepts_relative_case_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative case dir is resolved, so ``--rootdir`` stays valid.

    ``--rootdir`` is resolved against pytest's own cwd, not the harness's, so an
    unresolved relative path used to hand pytest a nonexistent rootdir and abort
    with a usage error (exit 4) that the summary parser read as 0/0/0/0.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    monkeypatch.chdir(tmp_path)

    result = run_pytest(Path("case"), [])

    assert result.ids == MINI_IDS
    assert (result.outcomes.passed, result.outcomes.failed) == (2, 1)


def test_check_pytest_collect_exit_passes_through_collectable_outcomes() -> None:
    """0 (collected), 2 (collection error) and 5 (nothing collected) are gradeable.

    All three are outcomes the v2 ``--v2-collect-only`` surface also produces, so the
    grader must see them rather than have the harness raise over them.
    """
    for returncode in (0, 2, 5):
        _check_pytest_collect_exit(_completed(returncode))  # must not raise


@pytest.mark.parametrize("returncode", [3, 4])
def test_check_pytest_collect_exit_raises_on_internal_and_usage_errors(returncode: int) -> None:
    """A pytest collect run that never happened must not grade as an empty id set.

    Exit 3 (internal error) and 4 (usage error) mean pytest could not do its job at
    all; parsing zero ids out of that would fabricate a divergence with no
    explanation. Raising routes it to the harness-error channel instead.
    """
    with pytest.raises(RuntimeError, match=r"pytest collect failed \(exit \d+\)"):
        _check_pytest_collect_exit(_completed(returncode))


def test_isolate_case_copies_and_adds_a_bare_ini(tmp_path: Path) -> None:
    """The isolated copy gets a bare ``pytest.ini`` and the original is untouched.

    The bare ini is what pins rootdir to the copy for *both* runners: pytest treats a
    ``pytest.ini`` as authoritative even when empty, and so does v2's config search
    (``src/v2/config.rs``: ``pytest.ini files are always the source of configuration,
    even if empty``). That is the whole comparison protocol in one file.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    dest_parent = tmp_path / "work"
    dest_parent.mkdir()

    work = _isolate_case(case_dir, dest_parent)

    assert work == dest_parent / "case"
    assert (work / "test_mini.py").read_text(encoding="utf-8") == MINI_SUITE
    assert (work / "pytest.ini").read_text(encoding="utf-8") == "[pytest]\n"
    assert not (case_dir / "pytest.ini").exists()


def test_isolate_case_keeps_a_case_owned_config_file(tmp_path: Path) -> None:
    """A case that ships its own config keeps it -- the harness must not clobber intent.

    Overwriting it would silently rewrite what the case is testing. The bare ini is a
    *fallback* for the (currently universal) config-less case, not a mandate.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n", encoding="utf-8")
    dest_parent = tmp_path / "work"
    dest_parent.mkdir()

    work = _isolate_case(case_dir, dest_parent)

    assert "check_*.py" in (work / "pytest.ini").read_text(encoding="utf-8")


@pytest.mark.parametrize("cache_dir", ["__pycache__", ".pytest_cache", ".rustest_cache"])
def test_isolate_case_drops_caches(cache_dir: str, tmp_path: Path) -> None:
    """Caches are litter, not case content, and never get copied.

    ``__pycache__`` is checked into the corpus from earlier runs and stale bytecode
    beside freshly copied source is the shape of pytest's ``import file mismatch``;
    the two runner caches would carry one run's state into the next.
    """
    case_dir = tmp_path / "case"
    (case_dir / cache_dir).mkdir(parents=True)
    (case_dir / cache_dir / "stale.bin").write_bytes(b"\x00")
    _write_mini_suite(case_dir)
    dest_parent = tmp_path / "work"
    dest_parent.mkdir()

    work = _isolate_case(case_dir, dest_parent)

    assert not (work / cache_dir).exists()


# (filename, contents, qualifies) -- the content rules both runners apply, per
# `_pytest/config/findpaths.py::load_config_dict_from_file` and its port in
# `src/v2/config.rs`. A file that does NOT qualify must still get the bare ini.
CONFIG_QUALIFICATION_CASES = [
    ("pyproject.toml", '[project]\nname = "x"\n', False),
    ("pyproject.toml", '[tool.pytest.ini_options]\nminversion = "7"\n', True),
    ("tox.ini", "[tox]\nenvlist = py312\n", False),
    ("tox.ini", "[pytest]\nminversion = 7\n", True),
    ("setup.cfg", "[metadata]\nname = x\n", False),
    ("setup.cfg", "[tool:pytest]\nminversion = 7\n", True),
    (".pytest.ini", "", False),
    (".pytest.ini", "[pytest]\n", True),
    ("pytest.ini", "", True),
]


@pytest.mark.parametrize(("filename", "contents", "qualifies"), CONFIG_QUALIFICATION_CASES)
def test_isolate_case_qualifies_config_by_content(
    filename: str, contents: str, qualifies: bool, tmp_path: Path
) -> None:
    """A shipped config file is honored only if it would really anchor the search.

    ``pytest.ini`` qualifies on its *name* even when empty; every other candidate
    qualifies only on content, and a section-less ``.pytest.ini`` does not qualify at
    all. When a case's file does not qualify, the bare ini must still be written --
    otherwise nothing anchors the isolated copy.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    (case_dir / filename).write_text(contents, encoding="utf-8")
    dest_parent = tmp_path / "work"
    dest_parent.mkdir()

    work = _isolate_case(case_dir, dest_parent)

    if qualifies:
        assert (work / filename).read_text(encoding="utf-8") == contents
        if filename != "pytest.ini":
            assert not (work / "pytest.ini").exists()
    else:
        assert (work / "pytest.ini").read_text(encoding="utf-8") == "[pytest]\n"


# The two collect invocations, as the runners build them -- used where a test must run
# in a directory it chose itself rather than in the runners' own temp isolation.
COLLECT_COMMANDS = {
    "pytest": [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--collect-only",
        "-q",
    ],
    "v2": [sys.executable, "-m", "rustest", "--v2-collect-only"],
}


@pytest.mark.parametrize("runner_name", list(COLLECT_COMMANDS))
def test_non_qualifying_case_config_still_isolates_from_a_poisoned_parent(
    runner_name: str, tmp_path: Path
) -> None:
    """The vacuous-MATCH trap: a ``[project]``-only pyproject.toml anchors nothing.

    Treating the file's mere *existence* as "this case brings its own config" skips
    the bare ini; both runners then walk up out of the isolated copy, find a parent
    ``pytest.ini`` whose ``python_files`` matches nothing here, and both collect zero
    tests. They would AGREE -- on the wrong rootdir -- and the case would record a
    MATCH that proves nothing.

    **Why this drives ``_isolate_case`` and raw subprocesses rather than
    ``run_pytest_collect`` / ``run_rustest_v2_collect``.** Those functions isolate into
    a fresh ``tempfile.TemporaryDirectory()`` under the system temp root, which is not
    beneath ``tmp_path`` -- so a poisoned ini written in ``tmp_path`` is never an
    ancestor of the tree they actually run in, and the test passes no matter how
    qualification is decided. That is exactly how the first version of this test was
    inert (it passed under an existence-only mutation). Placing ``dest_parent``
    *under* the poisoned directory is what puts the poison back in the ancestor chain
    and makes the assertion load-bearing.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n", encoding="utf-8")
    case_dir = tmp_path / "case_src"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    (case_dir / "pyproject.toml").write_text('[project]\nname = "case"\n', encoding="utf-8")
    dest_parent = tmp_path / "work"
    dest_parent.mkdir()

    work = _isolate_case(case_dir, dest_parent)
    proc = _run(COLLECT_COMMANDS[runner_name], work)

    ids = parse_pytest_collect(proc.stdout) if runner_name == "pytest" else proc.stdout.splitlines()
    assert ids == MINI_IDS_ORDERED
    assert proc.returncode == 0


def test_run_pytest_collect_integration(tmp_path: Path) -> None:
    result = run_pytest_collect(_case_with_mini_suite(tmp_path), [])
    assert result.ids == MINI_IDS_ORDERED
    assert result.exit_code == 0


def test_run_rustest_v2_collect_integration(tmp_path: Path) -> None:
    result = run_rustest_v2_collect(_case_with_mini_suite(tmp_path), [])
    assert result.ids == MINI_IDS_ORDERED
    assert result.exit_code == 0


def _case_with_mini_suite(tmp_path: Path) -> Path:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    return case_dir


@pytest.mark.parametrize("runner", [run_pytest_collect, run_rustest_v2_collect])
def test_collect_runners_ignore_a_surrounding_project_config(
    runner: Callable[[Path, list[str]], CollectResult], tmp_path: Path
) -> None:
    """Both collect runners must be blind to config *above* the case directory.

    This is the load-bearing protocol test. Run in place, the two runners disagree on
    rootdir by construction: v2 resolves config by walking up from its cwd and would
    adopt this ``pytest.ini`` (collecting nothing, since ``python_files`` no longer
    matches ``test_mini.py``), while ``run_pytest``'s v1-mode isolation flags
    (``-c`` + ``--rootdir``) pin pytest to the case dir. Copying the case out of the
    tree removes the disagreement for both sides at once, without either runner
    needing flags the ``--v2-collect-only`` surface does not have in 1b.1.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n", encoding="utf-8")

    result = runner(_case_with_mini_suite(tmp_path), [])

    assert result.ids == MINI_IDS_ORDERED
    assert result.exit_code == 0


@pytest.mark.parametrize("runner", [run_pytest_collect, run_rustest_v2_collect])
def test_collect_runners_agree_on_an_empty_tree(
    runner: Callable[[Path, list[str]], CollectResult], tmp_path: Path
) -> None:
    """Zero collectible tests is exit 5 on both sides -- the v1 ledger's waiver is dead here."""
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "test_nothing.py").write_text("def helper():\n    pass\n", encoding="utf-8")

    result = runner(case_dir, [])

    assert result.ids == []
    assert result.exit_code == 5


@pytest.mark.parametrize("runner", [run_pytest_collect, run_rustest_v2_collect])
def test_collect_runners_agree_on_a_collection_error(
    runner: Callable[[Path, list[str]], CollectResult], tmp_path: Path
) -> None:
    """An unimportable file is data, not a harness fault: healthy ids plus exit 2.

    The broken file sorts first, which is the interesting order: pytest still emits
    every healthy nodeid before its error block, and v2 keeps ids on stdout with the
    error on stderr. Neither side's error *prose* is compared -- only ids and code.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "test_aaa_broken.py").write_text("def test_x(:\n", encoding="utf-8")
    (case_dir / "test_zzz_good.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    result = runner(case_dir, [])

    assert result.ids == ["test_zzz_good.py::test_ok"]
    assert result.exit_code == 2


def _write_interleaved_tree(case_dir: Path) -> None:
    """A tree whose walk order is only correct if directories sort in with files."""
    (case_dir / "sub").mkdir(parents=True)
    (case_dir / "sub" / "test_b.py").write_text(
        "def test_in_subdir():\n    assert True\n", encoding="utf-8"
    )
    (case_dir / "test_a.py").write_text(
        "def test_alpha():\n    assert True\n\n\ndef test_beta():\n    assert True\n",
        encoding="utf-8",
    )
    (case_dir / "zz_test.py").write_text("def test_gamma():\n    assert True\n", encoding="utf-8")


INTERLEAVED_IDS_ORDERED = [
    "sub/test_b.py::test_in_subdir",
    "test_a.py::test_alpha",
    "test_a.py::test_beta",
    "zz_test.py::test_gamma",
]


@pytest.mark.parametrize("runner", [run_pytest_collect, run_rustest_v2_collect])
def test_collect_runners_agree_on_interleaved_walk_order(
    runner: Callable[[Path, list[str]], CollectResult], tmp_path: Path
) -> None:
    """Collection ORDER is graded, and this is the tree that makes order observable.

    ``sub/`` is descended at the position its own *name* sorts to -- before
    ``test_a.py``, not after every root file -- so a runner that walked files first
    and directories second would produce the same id *set* in a different order. A
    set comparison cannot see that at all; an ordered one fails on index 0.
    ``zz_test.py`` also pins the second default ``python_files`` pattern
    (``*_test.py``) and anchors the tail of the order.
    """
    case_dir = tmp_path / "case"
    _write_interleaved_tree(case_dir)

    result = runner(case_dir, [])

    assert result.ids == INTERLEAVED_IDS_ORDERED
    assert result.exit_code == 0


def test_parse_pytest_collect_preserves_duplicate_ids() -> None:
    """pytest really does print a node id twice when a path is passed twice.

    Verified against live pytest: ``pytest --collect-only -q test_a.py test_a.py``
    prints the id twice and reports ``2 tests collected``. The parser must not
    quietly fold that back into one.
    """
    assert parse_pytest_collect("test_a.py::test_x\ntest_a.py::test_x\n") == [
        "test_a.py::test_x",
        "test_a.py::test_x",
    ]


def test_run_rustest_v2_collect_never_deduplicates_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner reports exactly the lines v2 printed, duplicates included.

    A v2 collector that emitted the same id twice would be a real defect, and
    ``set(...)`` or ``dict.fromkeys(...)`` in the runner would erase the evidence
    before the grader ever saw it -- the runner would be *hiding* the bug it exists
    to expose. No live v2 invocation produces a duplicate today (a repeated path
    argument collapses; see the Task 5 report's findings), so the process boundary is
    stubbed rather than waiting for the defect to appear in the wild.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)

    def _fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="test_mini.py::test_one\ntest_mini.py::test_one\n",
            stderr="",
        )

    monkeypatch.setattr("conformance.harness.runners._run", _fake_run)

    result = run_rustest_v2_collect(case_dir, [])

    assert result.ids == ["test_mini.py::test_one", "test_mini.py::test_one"]


def test_run_rustest_v2_collect_reads_only_stdout_ids(tmp_path: Path) -> None:
    """v2's stderr (summary, ``ERROR collecting ...``) must never leak into the id set.

    v2 deliberately puts its summary and error prose on stderr where pytest puts them
    on stdout; a runner that merged the streams would read ``1 test collected`` as a
    phantom id and every collection-error case would diverge for the wrong reason.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "test_broken.py").write_text("def test_x(:\n", encoding="utf-8")
    (case_dir / "test_good.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    result = run_rustest_v2_collect(case_dir, [])

    assert result.ids == ["test_good.py::test_ok"]
    assert not any("collected" in nodeid for nodeid in result.ids)


# --------------------------------------------------------------------------------------
# The full-run (`--v2-run`) gate: real pytest execution vs `rustest --v2 --report-json`.
# --------------------------------------------------------------------------------------


def test_parse_pytest_summary_reads_all_six_buckets() -> None:
    """The six-value mapping, from the one line pytest prints when all six occur.

    Captured verbatim from real pytest 8.4.2 (see
    ``test_parse_pytest_summary_matches_real_pytest_on_every_bucket``); kept as a fast
    golden so the parser has a subprocess-free regression too.
    """
    out = parse_pytest_summary(
        "1 failed, 1 passed, 1 skipped, 1 xfailed, 1 xpassed, 1 error in 0.09s\n",
        exit_code=1,
    )

    assert (out.passed, out.failed, out.skipped, out.errors) == (1, 1, 1, 1)
    assert (out.xfailed, out.xpassed) == (1, 1)


def test_parse_pytest_summary_does_not_read_xfailed_as_failed() -> None:
    """``xfailed`` ends in ``failed`` and must never be counted as one.

    This is not hypothetical: before ``xfailed`` was in the alternation the token
    simply did not match at all, and ``marks/xfail`` recorded pytest as ``1/0/0/0``
    -- an *expected failure silently tallied as nothing*. The dangerous direction is
    the other one, though: a pattern that matched ``failed`` inside ``xfailed`` would
    turn an entirely green pytest run into ``1 failed`` and make v2 look broken.
    """
    out = parse_pytest_summary("1 passed, 2 xfailed in 0.01s\n", exit_code=0)

    assert out.failed == 0
    assert out.xfailed == 2
    assert out.passed == 1


def test_parse_pytest_summary_does_not_read_xpassed_as_passed() -> None:
    """``xpassed`` ends in ``passed``; the same trap, in the bucket that hides it best.

    An xpass folded into ``passed`` is invisible -- both are green -- which is exactly
    why schema v2 gave it its own bucket (Task 4 §5.1).
    """
    out = parse_pytest_summary("2 xpassed in 0.01s\n", exit_code=0)

    assert out.passed == 0
    assert out.xpassed == 2


def test_parse_pytest_summary_counts_deselected() -> None:
    """``deselected`` is read off the summary line, and it is not an outcome bucket.

    pytest prints it beside the outcomes (``1 passed, 1 deselected``) but no test ran,
    so it must not be conflated with ``skipped`` -- the bucket it superficially
    resembles. The gate compares it because it is the only published field that
    distinguishes a deselected id from one that was never collected.
    """
    out = parse_pytest_summary("1 passed, 2 deselected in 0.01s\n", exit_code=0)

    assert out.deselected == 2
    assert out.skipped == 0
    assert out.passed == 1


SIX_OUTCOME_SUITE = textwrap.dedent(
    """\
    import pytest


    def test_pass():
        assert True


    def test_fail():
        assert False


    @pytest.mark.skip(reason="s")
    def test_skip():
        pass


    @pytest.mark.xfail(reason="broken")
    def test_xfail():
        assert False


    @pytest.mark.xfail(reason="already fixed")
    def test_xpass():
        assert True


    @pytest.fixture
    def broken():
        raise RuntimeError("boom")


    def test_error(broken):
        pass
    """
)

SIX_OUTCOME_IDS_ORDERED = [
    "test_six.py::test_pass",
    "test_six.py::test_fail",
    "test_six.py::test_skip",
    "test_six.py::test_xfail",
    "test_six.py::test_xpass",
    "test_six.py::test_error",
]


def _case_with_six_outcomes(tmp_path: Path) -> Path:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "test_six.py").write_text(SIX_OUTCOME_SUITE, encoding="utf-8")
    return case_dir


def test_parse_pytest_summary_matches_real_pytest_on_every_bucket(tmp_path: Path) -> None:
    """The golden above, re-derived from live pytest rather than trusted.

    A hand-written summary literal can only pin whatever the author believed pytest
    prints -- the failure mode that put the stderr summary line in the wrong bucket
    order in Task 4 (report §A). Driving real pytest is what makes the parser's
    contract a fact about pytest instead of a fact about this file.
    """
    result = run_pytest_full(_case_with_six_outcomes(tmp_path), [])

    assert result.outcomes == RunOutcomes(
        passed=1, failed=1, skipped=1, xfailed=1, xpassed=1, errors=1, deselected=0
    )


@pytest.mark.parametrize("runner", [run_pytest_full, run_rustest_v2_run])
def test_full_run_runners_agree_on_all_six_outcomes(
    runner: Callable[[Path, list[str]], FullRunResult], tmp_path: Path
) -> None:
    """One tree, all six statuses, both runners -- the whole graded contract at once.

    Parametrizing over the two runners is what makes this a *differential* assertion
    rather than two independent ones: the same literal expectation has to hold for
    pytest and for ``rustest --v2``, so a change to either side that this file did not
    anticipate fails here rather than being absorbed into a matching expectation.
    """
    result = runner(_case_with_six_outcomes(tmp_path), [])

    assert result.ids == SIX_OUTCOME_IDS_ORDERED
    assert result.outcomes == RunOutcomes(
        passed=1, failed=1, skipped=1, xfailed=1, xpassed=1, errors=1, deselected=0
    )
    assert result.exit_code == 1


@pytest.mark.parametrize("runner", [run_pytest_full, run_rustest_v2_run])
def test_full_run_runners_agree_on_the_mini_suite(
    runner: Callable[[Path, list[str]], FullRunResult], tmp_path: Path
) -> None:
    """Ids are ORDERED and in execution order; two passes and one failure exit 1."""
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)

    result = runner(case_dir, [])

    assert result.ids == MINI_IDS_ORDERED
    assert result.outcomes == RunOutcomes(
        passed=2, failed=1, skipped=0, xfailed=0, xpassed=0, errors=0, deselected=0
    )
    assert result.exit_code == 1


@pytest.mark.parametrize("runner", [run_pytest_full, run_rustest_v2_run])
def test_full_run_runners_ignore_a_surrounding_project_config(
    runner: Callable[[Path, list[str]], FullRunResult], tmp_path: Path
) -> None:
    """The isolation protocol, on the run surface: config above the case is invisible.

    Identical in kind to the collect gate's protocol test, and load-bearing for the
    same reason: ``rustest --v2`` resolves config by walking up from its cwd and has no
    ``-c``/``--rootdir`` to pin it, so without the copy-out both runners would answer
    different questions about rootdir and every case would "diverge" on an id prefix
    neither is getting wrong.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = check_*.py\n", encoding="utf-8")
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)

    result = runner(case_dir, [])

    assert result.ids == MINI_IDS_ORDERED
    assert result.exit_code == 1


@pytest.mark.parametrize("runner", [run_pytest_full, run_rustest_v2_run])
def test_full_run_runners_pass_case_args_through(
    runner: Callable[[Path, list[str]], FullRunResult], tmp_path: Path
) -> None:
    """``case.toml`` args reach both runners, and deselection shrinks the graded ids.

    ``marks/mark-filter`` is exactly this shape. If a runner dropped the args, the id
    list would silently grow back to the whole file and the case would compare two
    different questions.

    **``deselected=1`` is the load-bearing half.** It is the differential wiring test for
    the seventh graded count: pytest publishes it on the summary line (``1 passed,
    1 deselected``) and v2 publishes it as ``summary.deselected``, and this asserts both
    reach ``RunOutcomes`` through their own separate code paths. Without it the count
    could be hard-zero on either side and every case would still agree -- which is how
    the gate shipped with a false green (see
    ``test_grade_run_diverge_on_a_lost_deselected_sibling``).
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "test_marks.py").write_text(
        textwrap.dedent(
            """\
            import pytest


            @pytest.mark.smoke
            def test_selected():
                assert True


            def test_unselected():
                assert False
            """
        ),
        encoding="utf-8",
    )

    result = runner(case_dir, ["-m", "smoke"])

    assert result.ids == ["test_marks.py::test_selected"]
    assert result.outcomes == RunOutcomes(
        passed=1, failed=0, skipped=0, xfailed=0, xpassed=0, errors=0, deselected=1
    )
    assert result.exit_code == 0


def _case_with_a_broken_import(tmp_path: Path) -> Path:
    """A tree where one file cannot be imported and the other would leave a footprint.

    The sentinel is the point: it turns "nothing ran" from an assertion about exit
    codes into an observation about the filesystem.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "test_aaa_broken.py").write_text("def test_x(:\n", encoding="utf-8")
    (case_dir / "test_zzz_good.py").write_text(
        "from pathlib import Path\n\n\ndef test_ok():\n    Path('ran.marker').write_text('y')\n",
        encoding="utf-8",
    )
    return case_dir


@pytest.mark.parametrize("runner", [run_pytest_full, run_rustest_v2_run])
def test_full_run_runners_report_no_executed_ids_when_collection_is_interrupted(
    runner: Callable[[Path, list[str]], FullRunResult], tmp_path: Path
) -> None:
    """A collection error means *nothing runs*, so the executed-id list is empty.

    This is the one rule ``run_pytest_full`` keys on an exit code, and it is pytest's
    own: ``pytest_runtestloop`` raises ``Interrupted`` before the first item. The
    ``--collect-only`` pass still lists ``test_zzz_good.py::test_ok``, so taking those
    ids verbatim would pit pytest's *collected* set against v2's *executed* one and
    manufacture a divergence out of a rule both engines implement identically
    (``src/v2/execute.rs::stage`` returns empty dispatch lists when ``errors`` is
    non-empty -- Task 4 report §2.1, correction 2).

    Asserted from a **side effect**, not from the exit code: if the healthy test had
    run, it would have left ``ran.marker`` behind. That is what makes the empty list an
    observation rather than a restatement of the branch under test.

    ``errors == 1`` is the *broken file*, not a test: pytest reports a failed import as
    ``1 error``, and ``run_rustest_v2_run`` folds ``collection_errors`` into the same
    bucket so the two tallies are comparable. This expectation is the reason the fold
    exists -- the first version of this test asserted ``errors=0`` for both runners and
    the differential parametrization proved it wrong on the pytest side.
    """
    case_dir = _case_with_a_broken_import(tmp_path)

    result = runner(case_dir, [])

    assert result.exit_code == 2
    assert result.ids == []
    assert result.outcomes == RunOutcomes(
        passed=0, failed=0, skipped=0, xfailed=0, xpassed=0, errors=1, deselected=0
    )
    # The oracle half, and the reason the empty list above is an observation rather
    # than a restatement of the branch: the healthy test would have written this file
    # had it run. No harness mutation can flip this assertion -- it is a claim about
    # *pytest*, not about the harness -- so it is carried here rather than as a
    # separate test that would look mutation-covered and not be.
    assert not (case_dir / "ran.marker").exists()


def test_run_pytest_full_keeps_its_ids_when_a_test_calls_pytest_exit(tmp_path: Path) -> None:
    """Exit 2 from ``pytest.exit()`` must NOT be read as "collection failed".

    ``_pytest.outcomes.Exit`` and ``Interrupted`` share exit code 2, so a rule keyed on
    the **run** pass's code fires for both -- and for ``pytest.exit()`` that produces an
    oracle contradicting itself: an empty id list beside a tally that says a test passed.
    Anyone reading that divergence would go hunting for a collection bug that is not
    there.

    Probed shapes for this tree: collect pass exit 0 with three ids, run pass exit 2 with
    ``1 passed``. Keying on the **collect** pass -- the authority on whether collection
    failed -- keeps the ids, so the real disagreement surfaces through the tally and the
    exit code instead.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "test_bail.py").write_text(
        textwrap.dedent(
            """\
            import pytest


            def test_first():
                assert True


            def test_bails():
                pytest.exit("stopping here")


            def test_never():
                assert True
            """
        ),
        encoding="utf-8",
    )

    result = run_pytest_full(case_dir, [])

    assert result.exit_code == 2
    # Collection succeeded, so the ids stand: the oracle stays internally consistent.
    assert result.ids == [
        "test_bail.py::test_first",
        "test_bail.py::test_bails",
        "test_bail.py::test_never",
    ]
    assert result.outcomes.passed == 1


def test_run_rustest_v2_run_raises_when_no_report_is_written(tmp_path: Path) -> None:
    """``rustest --v2`` dying before it writes a report is a loud failure, never zeros.

    Fabricating an all-zeros ``FullRunResult`` here would grade as a divergence with no
    explanation attached -- and on a case whose pytest side is also empty (an empty
    tree), it would grade as a **MATCH** for a run that never happened.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)

    with pytest.raises(RuntimeError, match="rustest --v2 wrote no report"):
        run_rustest_v2_run(case_dir, ["--definitely-not-a-real-flag"])


def test_run_rustest_v2_run_grades_the_process_exit_code_not_the_reports_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The graded exit code is what the process returned, not what the report says.

    They are pinned to agree by ``python/tests/test_v2_run_cli.py``; if they ever stop
    agreeing, the gate must side with the number CI and users actually observe.
    Stubbing the process boundary is the only way to drive them apart on purpose.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_mini_suite(case_dir)
    real_run = _run

    def _fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        proc = real_run(cmd, cwd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=99, stdout=proc.stdout, stderr=proc.stderr
        )

    monkeypatch.setattr("conformance.harness.runners._run", _fake_run)

    result = run_rustest_v2_run(case_dir, [])

    assert result.exit_code == 99


def test_run_rustest_raises_when_no_report_is_written(tmp_path: Path) -> None:
    """A rustest invocation that dies before writing the report is a harness fault.

    An unrecognized flag makes the rustest CLI bail out in argparse, so no report
    file exists. Returning a fabricated all-zeros result here would grade as a
    silent divergence; the harness must surface the real failure instead. This
    drives the real CLI rather than a monkeypatched stand-in, so it stays honest
    if the failure mode changes.
    """
    _write_mini_suite(tmp_path)

    with pytest.raises(RuntimeError, match="rustest wrote no report"):
        run_rustest(tmp_path, ["--definitely-not-a-real-flag"])
