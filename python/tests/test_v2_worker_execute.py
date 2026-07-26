"""Tests for the v2 worker's execute half (`rustest._v2_worker`).

Five things are under test, and **real pytest is the oracle for every outcome**: the tables
below do not hold hand-written expectations, they run pytest in a subprocess over the same
tree and compare its reporting category per nodeid (:func:`pytest_statuses`).  A row that
disagrees is either a worker bug or a documented divergence — never a guess that drifted.

1. **Outcome classification by type.**  `_pytest/outcomes.py` makes ``Skipped``/``Failed``/
   ``XFailed`` classes so that the decision is ``isinstance``, never a message match.  The
   switch has one test per branch, each individually killable.
2. **`failed` vs `error` is the phase, not the exception.**  `_pytest/runner.py` l. 214-223
   and `_pytest/terminal.py` l. 325-337: a failure in setup or teardown is ``error``, one in
   the body is ``failed``.  The corpus has no case for it, so the fixture tables below are it.
3. **Marks at execution** — the #131 root fix.  `marks/skip-and-skipif` and `marks/xfail` are
   the acceptance cases; the wider table covers the shapes the corpus does not (strict xpass,
   ``raises=``, ``run=False``, string conditions, skip-beats-xfail).
4. **unittest through an explicit ``TestResult``** — the #129 root fix.  All five callback
   buckets, translated the way `_pytest/unittest.py::TestCaseFunction` translates them.
   `collection/unittest-basic` is the acceptance case.
5. **The wire shape**: the golden ``test_result`` line, omission of the optional fields, and
   the protocol-fatal paths.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import unittest

import pytest

from rustest._v2_worker import (
    PROTOCOL_VERSION,
    STATUSES,
    DEFAULT_NAMING,
    ExecutionPlan,
    FixtureRunner,
    MarkSpec,
    PhaseReport,
    ResultResponse,
    UnknownTestError,
    Xfail,
    build_registry,
    collect_module,
    encode_response,
    evaluate_skip_marks,
    evaluate_xfail_marks,
    execute_test,
    reduce_reports,
    report_for_phase,
)
import rustest._v2_worker as worker


REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "conformance" / "corpus"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@contextmanager
def isolated_worker_state() -> Iterator[None]:
    """The worker's import and execution state, installed then fully undone.

    Same contract as ``test_v2_worker_fixtures.isolated_import_state`` (the compat shim must
    be live or a generated module's ``import pytest`` would bind *real* pytest and leave none
    of the ``__rustest_*`` metadata), plus the two globals the execute half owns: the plan
    index and the process-wide :class:`FixtureRunner`.  Leaking either would let one test's
    module-scoped fixture survive into the next and make a later assertion pass for the wrong
    reason.
    """
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    saved_conftests = dict(worker._conftest_modules)  # pyright: ignore[reportPrivateUsage]
    saved_plans = dict(worker._execution_plans)  # pyright: ignore[reportPrivateUsage]
    saved_runner = worker._runner  # pyright: ignore[reportPrivateUsage]
    worker._runner = None  # pyright: ignore[reportPrivateUsage]
    try:
        worker.install_pytest_shim()
        yield
    finally:
        worker._runner = saved_runner  # pyright: ignore[reportPrivateUsage]
        worker._execution_plans.clear()  # pyright: ignore[reportPrivateUsage]
        worker._execution_plans.update(saved_plans)  # pyright: ignore[reportPrivateUsage]
        sys.path[:] = saved_path
        for name in set(sys.modules) - set(saved_modules):
            del sys.modules[name]
        sys.modules.update(saved_modules)
        worker._conftest_modules.clear()  # pyright: ignore[reportPrivateUsage]
        worker._conftest_modules.update(saved_conftests)  # pyright: ignore[reportPrivateUsage]


def write(path: Path, source: str) -> Path:
    """Write dedented *source* to *path*, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def register(plans: list[ExecutionPlan]) -> None:
    """Put *plans* in the worker's index, which is what :func:`execute_test` resolves against."""
    for plan in plans:
        worker._execution_plans[plan.id] = plan  # pyright: ignore[reportPrivateUsage]


def run_module(path: Path, rootdir: Path) -> list[ResultResponse]:
    """Collect one file and execute every test in it, exactly as the protocol loop would.

    Goes through :func:`execute_test` — the function ``main`` calls — rather than driving the
    runner directly, so the tests exercise the production path including capture, duration
    and the shutdown drain.
    """
    module, registry = build_registry(path, rootdir)
    _entries, plans = collect_module(module, path, rootdir, DEFAULT_NAMING, registry)
    register(plans)
    results = [execute_test(plan.id) for plan in plans]
    _ = shutdown()
    return results


def shutdown() -> BaseException | None:
    """Shut the worker's execution state down the way ``main`` does: drain, then answer.

    A helper rather than a bare ``handle_shutdown()`` call because the two halves are a
    contract — answering ``shutdown`` without draining leaks every module- and session-scoped
    fixture — and a test that only called the second half would not notice.
    """
    failure = worker.drain_at_shutdown()
    assert worker.handle_shutdown() == {"op": "bye"}
    return failure


def statuses(results: list[ResultResponse]) -> dict[str, str]:
    """``{nodeid: status}`` for a run, which is what the pytest oracle is compared against."""
    return {result["id"]: result["status"] for result in results}


_VERBOSE_LINE = re.compile(
    r"^(?P<nodeid>\S+::\S+)\s+(?P<word>PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)\b"
)

#: pytest's verbose words are its reporting categories in upper case, except the two xfail
#: ones, which `_pytest/skipping.py::pytest_report_teststatus` spells `XFAIL`/`XPASS`.
_WORD_TO_STATUS = {
    "PASSED": "passed",
    "FAILED": "failed",
    "SKIPPED": "skipped",
    "XFAIL": "xfailed",
    "XPASS": "xpassed",
    "ERROR": "error",
}


def pytest_statuses(tree: Path) -> dict[str, str]:
    """Real pytest's outcome per nodeid — the execution oracle.

    ``-v`` prints one line per *report*, so a test whose body passed and whose teardown blew
    up appears twice, as ``PASSED`` then ``ERROR``.  The wire carries **one** status, so the
    oracle has to state pytest's own reduction, and the rule is *"a ``.`` is not news"*: drop
    the plain ``PASSED`` lines and take the first that remains.

    That is not a convenience — it is what pytest itself reports.  For that shape it counts
    ``1 passed, 1 error``, exits non-zero, and lists the test under ``ERRORS`` in its short
    summary; collapsing to ``passed`` would report a green test for a run pytest failed.
    ``XPASS`` and ``XFAIL`` are distinct words, so they are never dropped by this rule, which
    is why an xpassed call still beats a later teardown report — exactly what
    :func:`reduce_reports` does with :attr:`PhaseReport.plain_pass`.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
            "--tb=no",
            "-W",
            "ignore",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    reported: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        match = _VERBOSE_LINE.match(line.strip())
        if match is None:
            continue
        nodeid = match.group("nodeid").replace("\\", "/")
        reported.setdefault(nodeid, []).append(_WORD_TO_STATUS[match.group("word")])
    assert reported, f"pytest ran nothing:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return {
        nodeid: next((word for word in words if word != "passed"), "passed")
        for nodeid, words in reported.items()
    }


def differential(tmp_path: Path, source: str, filename: str = "test_diff.py") -> dict[str, str]:
    """Run *source* under both runners and assert they agree; return the shared table.

    The comparison is a dict equality on ``{nodeid: status}``, so a test the worker fails to
    run at all is as loud as one it runs to the wrong answer.
    """
    tree = tmp_path / "tree"
    target = write(tree / filename, source)
    oracle = pytest_statuses(tree)
    with isolated_worker_state():
        ours = statuses(run_module(target, tree))
    assert ours == oracle
    return oracle


def corpus_differential(tmp_path: Path, case: str) -> dict[str, str]:
    """The same comparison over a real ``conformance/corpus`` case, copied out of the tree.

    Copied rather than run in place: both runners write ``__pycache__`` and pytest would
    otherwise collect the corpus's neighbours through the repo's own ini file.
    """
    source_dir = CORPUS / case
    tree = tmp_path / "tree"
    tree.mkdir(parents=True, exist_ok=True)
    targets: list[Path] = []
    for entry in sorted(source_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".py":
            _ = shutil.copy2(entry, tree / entry.name)
            if entry.name.startswith("test_"):
                targets.append(tree / entry.name)
    assert targets, f"corpus case {case} has no test module"

    oracle = pytest_statuses(tree)
    with isolated_worker_state():
        ours: dict[str, str] = {}
        for target in targets:
            module, registry = build_registry(target, tree)
            _entries, plans = collect_module(module, target, tree, DEFAULT_NAMING, registry)
            register(plans)
            ours.update({plan.id: execute_test(plan.id)["status"] for plan in plans})
        _ = shutdown()
    assert ours == oracle
    return oracle


def _run_worker(lines: list[Mapping[str, object]]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rustest._v2_worker"],
        input="".join(json.dumps(line) + "\n" for line in lines),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _init_line(rootdir: Path) -> dict[str, object]:
    return {
        "op": "init",
        "protocol_version": PROTOCOL_VERSION,
        "rootdir": rootdir.as_posix(),
        "invocation_dir": rootdir.as_posix(),
        "python_files": ["test_*.py"],
        "python_classes": ["Test"],
        "python_functions": ["test"],
    }


# ---------------------------------------------------------------------------
# 1. the corpus acceptance cases
# ---------------------------------------------------------------------------


def test_corpus_marks_skip_and_skipif_executes_like_pytest(tmp_path: Path) -> None:
    """`marks/skip-and-skipif` — the #131 acceptance case.

    Both marks have to be *evaluated*, not merely carried: before this task they travelled to
    the manifest as data and every one of these tests ran.  The false condition is the row
    that matters most — a worker that skipped on the presence of a ``skipif`` mark rather
    than on its value would pass the other two.
    """
    assert corpus_differential(tmp_path, "marks/skip-and-skipif") == {
        "test_skip.py::test_skipped": "skipped",
        "test_skip.py::test_skipped_conditionally": "skipped",
        "test_skip.py::test_runs": "passed",
    }


def test_corpus_marks_xfail_executes_like_pytest(tmp_path: Path) -> None:
    """`marks/xfail` — a failing body under an ``xfail`` mark is ``xfailed``, not ``failed``."""
    assert corpus_differential(tmp_path, "marks/xfail") == {
        "test_xfail.py::test_expected_failure": "xfailed",
        "test_xfail.py::test_normal": "passed",
    }


def test_corpus_unittest_basic_executes_like_pytest(tmp_path: Path) -> None:
    """`collection/unittest-basic` — the #129 acceptance case, 1 pass and 1 fail.

    The failing row is the load-bearing one: it is an ``assertEqual``, so it lands in
    ``TestResult.failures``, and translating that bucket to anything but ``failed`` is
    exactly the defect #129 describes.
    """
    assert corpus_differential(tmp_path, "collection/unittest-basic") == {
        "test_unittest.py::TestLegacy::test_addition": "passed",
        "test_unittest.py::TestLegacy::test_failure": "failed",
    }


# ---------------------------------------------------------------------------
# 2. mark semantics — the wider table
# ---------------------------------------------------------------------------

SKIP_TABLE = """
import pytest


@pytest.mark.skip(reason="always skipped")
def test_skip_mark():
    raise AssertionError("must not run")


@pytest.mark.skipif(1 + 1 == 2, reason="condition true")
def test_skipif_true():
    raise AssertionError("must not run")


@pytest.mark.skipif(1 + 1 == 3, reason="condition false")
def test_skipif_false():
    assert True


@pytest.mark.skipif(False, reason="first")
@pytest.mark.skipif(True, reason="second")
def test_two_skipifs_any_true():
    raise AssertionError("must not run")


@pytest.mark.skipif(True)
def test_skipif_without_a_reason():
    assert True


def test_runtime_skip():
    pytest.skip("skipped from the body")


def test_runtime_fail():
    pytest.fail("failed on purpose")


def test_unittest_skiptest():
    import unittest

    raise unittest.SkipTest("raised unittest.SkipTest")


def test_plain_assert():
    assert 1 == 2


def test_plain_error():
    raise ValueError("boom")
"""


def test_skip_semantics_match_pytest(tmp_path: Path) -> None:
    """Port of `_pytest/skipping.py::evaluate_skip_marks` (l. 168-193), row by row.

    Three rows exist only because the obvious implementation gets them wrong:

    * ``test_skipif_without_a_reason`` is an **error**, not a skip — pytest's
      ``evaluate_condition`` calls ``fail()`` for a boolean condition with no ``reason=``
      (l. 150-156), and it does so whether the condition is true or false;
    * ``test_unittest_skiptest`` is a **skip**, because pytest converts ``unittest.SkipTest``
      explicitly (`_pytest/unittest.py` l. 377-387) — a classifier that only knew rustest's
      own ``Skipped`` would report it as a failure;
    * ``test_plain_error`` is ``failed``, not ``error``: the phase is what makes an ``E``.
    """
    assert differential(tmp_path, SKIP_TABLE) == {
        "test_diff.py::test_skip_mark": "skipped",
        "test_diff.py::test_skipif_true": "skipped",
        "test_diff.py::test_skipif_false": "passed",
        "test_diff.py::test_two_skipifs_any_true": "skipped",
        "test_diff.py::test_skipif_without_a_reason": "error",
        "test_diff.py::test_runtime_skip": "skipped",
        "test_diff.py::test_runtime_fail": "failed",
        "test_diff.py::test_unittest_skiptest": "skipped",
        "test_diff.py::test_plain_assert": "failed",
        "test_diff.py::test_plain_error": "failed",
    }


XFAIL_TABLE = """
import pytest


@pytest.mark.xfail(reason="known broken")
def test_xfail_body_fails():
    assert False


@pytest.mark.xfail(reason="but it works")
def test_xfail_body_passes():
    assert True


@pytest.mark.xfail(reason="strict", strict=True)
def test_strict_xpass():
    assert True


@pytest.mark.xfail(reason="strict", strict=True)
def test_strict_xfail():
    assert False


@pytest.mark.xfail(False, reason="condition false")
def test_xfail_condition_false():
    assert False


@pytest.mark.xfail(True, reason="condition true")
def test_xfail_condition_true():
    assert False


@pytest.mark.xfail("1 == 1", reason="string condition true")
def test_xfail_string_condition_true():
    assert False


@pytest.mark.xfail("1 == 2", reason="string condition false")
def test_xfail_string_condition_false():
    assert False


@pytest.mark.xfail(reason="never run", run=False)
def test_xfail_norun():
    raise AssertionError("must not run")


@pytest.mark.xfail(raises=ValueError, reason="only ValueError")
def test_xfail_raises_matches():
    raise ValueError("expected")


@pytest.mark.xfail(raises=ValueError, reason="only ValueError")
def test_xfail_raises_does_not_match():
    raise TypeError("unexpected")


def test_imperative_xfail():
    pytest.xfail("xfailed from the body")


@pytest.mark.skip(reason="skip wins")
@pytest.mark.xfail(reason="xfail loses")
def test_skip_beats_xfail():
    assert False
"""


def test_xfail_semantics_match_pytest(tmp_path: Path) -> None:
    """Port of `_pytest/skipping.py::evaluate_xfail_marks` + the makereport promotion.

    The rows that pin the parts most easily got wrong:

    * ``test_strict_xpass`` is **``failed``**, not ``xpassed`` — l. 300-303 rewrites
      ``rep.outcome`` before the ``wasxfail`` hook can promote it, which is why the wire's
      six-status set has no "strict xpass" member;
    * ``test_xfail_string_condition_false`` is ``failed``: a string condition has to be
      ``eval``'d, and judging it by truthiness (a non-empty string is always true) would make
      *every* string condition xfail;
    * ``test_xfail_raises_does_not_match`` is ``failed`` — the ``raises=`` filter needs the
      real exception **class**, which is exactly why marks reach execution as
      :class:`MarkSpec` rather than as their JSON-safe wire form;
    * ``test_skip_beats_xfail`` is ``skipped``: pytest's promotion is guarded by
      ``not rep.skipped`` (l. 283).
    """
    assert differential(tmp_path, XFAIL_TABLE, "test_x.py") == {
        "test_x.py::test_xfail_body_fails": "xfailed",
        "test_x.py::test_xfail_body_passes": "xpassed",
        "test_x.py::test_strict_xpass": "failed",
        "test_x.py::test_strict_xfail": "xfailed",
        "test_x.py::test_xfail_condition_false": "failed",
        "test_x.py::test_xfail_condition_true": "xfailed",
        "test_x.py::test_xfail_string_condition_true": "xfailed",
        "test_x.py::test_xfail_string_condition_false": "failed",
        "test_x.py::test_xfail_norun": "xfailed",
        "test_x.py::test_xfail_raises_matches": "xfailed",
        "test_x.py::test_xfail_raises_does_not_match": "failed",
        "test_x.py::test_imperative_xfail": "xfailed",
        "test_x.py::test_skip_beats_xfail": "skipped",
    }


MARK_INHERITANCE = """
import pytest

pytestmark = pytest.mark.skipif(True, reason="module level")


def test_module_mark_applies():
    raise AssertionError("must not run")


@pytest.mark.skipif(False, reason="own mark does not un-skip")
def test_module_mark_still_applies():
    raise AssertionError("must not run")
"""

CLASS_MARK_INHERITANCE = """
import pytest


@pytest.mark.skipif(True, reason="base class")
class TestBase:
    def test_inherited(self):
        raise AssertionError("must not run")


class TestDerived(TestBase):
    def test_own(self):
        raise AssertionError("must not run")


class TestPlain:
    def test_runs(self):
        assert True
"""


def test_module_level_pytestmark_is_evaluated(tmp_path: Path) -> None:
    """``pytestmark`` at module level reaches every test — and is *evaluated*, not carried.

    The second row matters: a test carrying its own **false** ``skipif`` must still be
    skipped by the module's true one.  pytest returns on the first *true* condition while
    iterating every ``skipif`` mark closest-first, so an implementation that stopped at the
    first ``skipif`` it found would run this test.
    """
    assert differential(tmp_path, MARK_INHERITANCE, "test_mod.py") == {
        "test_mod.py::test_module_mark_applies": "skipped",
        "test_mod.py::test_module_mark_still_applies": "skipped",
    }


def test_class_marks_reach_inherited_methods(tmp_path: Path) -> None:
    """The class-mark MRO read (#135) has to hold at *execution*, not only in the manifest.

    ``TestDerived`` carries no mark of its own; it inherits one through ``__mro__``, and both
    its inherited method and its own method must be skipped.  Collection already walks the
    reversed-``__mro__`` ``__dict__`` chain (`_pytest/mark/structures.py::get_unpacked_marks`)
    — this asserts the marks it produced are the ones the evaluator acts on.
    """
    assert differential(tmp_path, CLASS_MARK_INHERITANCE, "test_cls.py") == {
        "test_cls.py::TestBase::test_inherited": "skipped",
        "test_cls.py::TestDerived::test_inherited": "skipped",
        "test_cls.py::TestDerived::test_own": "skipped",
        "test_cls.py::TestPlain::test_runs": "passed",
    }


# ---------------------------------------------------------------------------
# 3. phases — failed vs error
# ---------------------------------------------------------------------------

PHASE_TABLE = """
import pytest


@pytest.fixture
def broken_setup():
    raise RuntimeError("setup boom")


def test_setup_error(broken_setup):
    assert True


@pytest.fixture
def broken_teardown():
    yield 1
    raise RuntimeError("teardown boom")


def test_passing_body_with_broken_teardown(broken_teardown):
    assert True


@pytest.fixture
def broken_teardown_two():
    yield 1
    raise RuntimeError("teardown boom")


def test_failing_body_with_broken_teardown(broken_teardown_two):
    assert False


@pytest.fixture
def skipping_fixture():
    pytest.skip("skipped from a fixture")


def test_skip_from_fixture(skipping_fixture):
    assert True


def test_missing_fixture(no_such_fixture):
    assert True


@pytest.fixture
def broken_setup_three():
    raise RuntimeError("setup boom")


@pytest.mark.xfail(reason="xfail swallows a setup error too")
def test_xfail_with_setup_error(broken_setup_three):
    assert False


@pytest.fixture
def broken_setup_four():
    raise RuntimeError("setup boom")


@pytest.mark.skip(reason="skip runs before any fixture")
def test_skip_beats_a_broken_fixture(broken_setup_four):
    assert True
"""


def test_phase_classification_matches_pytest(tmp_path: Path) -> None:
    """``error`` is the *phase*, not the exception — and the corpus has no case for it.

    Every row is a probe result, not a guess:

    * a broken fixture is ``error`` and an unresolvable one is too (pytest reports a missing
      fixture as a setup error, never as a collection error);
    * a **passing** body with a broken teardown is ``error`` — pytest prints ``PASSED`` then
      ``ERROR``, and the collapse keeps the news;
    * a **failing** body with a broken teardown is ``failed`` — pytest prints ``FAILED`` then
      ``ERROR``, and ``failed`` is the headline in its own short summary;
    * ``test_xfail_with_setup_error`` is ``xfailed``: `skipping.py`'s promotion fires for any
      phase, so an ``xfail`` mark swallows a broken fixture as well as a broken body;
    * ``test_skip_beats_a_broken_fixture`` is ``skipped``: marks are evaluated in
      ``pytest_runtest_setup`` *before* fixtures are built, so the fixture never runs.
    """
    assert differential(tmp_path, PHASE_TABLE, "test_ph.py") == {
        "test_ph.py::test_setup_error": "error",
        "test_ph.py::test_passing_body_with_broken_teardown": "error",
        "test_ph.py::test_failing_body_with_broken_teardown": "failed",
        "test_ph.py::test_skip_from_fixture": "skipped",
        "test_ph.py::test_missing_fixture": "error",
        "test_ph.py::test_xfail_with_setup_error": "xfailed",
        "test_ph.py::test_skip_beats_a_broken_fixture": "skipped",
    }


SIDE_EFFECTS = """
import pytest

ran = []


@pytest.fixture
def broken():
    raise RuntimeError("setup boom")


@pytest.mark.skip(reason="skipped")
def test_skipped_body_must_not_run():
    ran.append("skipped body ran")


def test_body_must_not_run_after_a_broken_fixture(broken):
    ran.append("body ran after a broken setup")


@pytest.mark.xfail(reason="not run", run=False)
def test_norun_body_must_not_run():
    ran.append("norun body ran")
"""


def test_the_body_does_not_run_when_it_should_not(tmp_path: Path) -> None:
    """The three shapes where the body must be **skipped**, checked by side effect.

    Every one of them is invisible in the *status*: pytest's reduction reports the setup
    phase, so a worker that ran the body anyway would still answer ``skipped``/``error``/
    ``xfailed``.  A corpus body of ``raise AssertionError("must not run")`` does not catch it
    either, for the same reason.  Only an observable side effect does — which is why this
    test exists and why the mutation pass demanded it.

    * a ``skip`` mark short-circuits before any fixture is built
      (`skipping.py::pytest_runtest_setup`);
    * a failed setup skips the call phase (``if rep.passed:``, `runner.py` l. 132-136);
    * ``xfail(run=False)`` raises ``xfail("[NOTRUN] " + reason)`` at setup (l. 249-250) —
      pinned by pytest's own ``[NOTRUN]`` prefix as well as by the side effect, because the
      prefix is the only thing that distinguishes it from an ordinary xfail.
    """
    target = write(tmp_path / "test_side.py", SIDE_EFFECTS)
    with isolated_worker_state():
        results = run_module(target, tmp_path)
        ran = getattr(sys.modules["test_side"], "ran")

    by_id = {result["id"]: result for result in results}
    assert by_id["test_side.py::test_skipped_body_must_not_run"]["status"] == "skipped"
    assert by_id["test_side.py::test_body_must_not_run_after_a_broken_fixture"]["status"] == "error"
    norun = by_id["test_side.py::test_norun_body_must_not_run"]
    assert norun["status"] == "xfailed"
    assert norun["message"] == "[NOTRUN] not run"

    assert ran == [], f"a body that must not run did: {ran}"


def test_a_failing_body_still_tears_its_fixtures_down(tmp_path: Path) -> None:
    """``runtestprotocol`` appends the teardown report **unconditionally** (l. 141).

    Invisible in the status — the reduction reports the call failure either way — so a worker
    that skipped teardown after a failure would leak every fixture a failing test touched,
    silently, and only under failure.

    The assertion has to be about **when**, not whether: ``handle_shutdown`` drains every
    open scope, so a teardown that was wrongly deferred still runs before the process ends
    and a check made after shutdown passes either way.  So this executes the test and asserts
    the teardown has *already* happened, with shutdown still ahead of it.
    """
    target = write(
        tmp_path / "test_late.py",
        """
        import pytest

        ran = []


        @pytest.fixture
        def records_teardown():
            yield 1
            ran.append("teardown ran")


        def test_fails(records_teardown):
            assert False
        """,
    )
    with isolated_worker_state():
        module, registry = build_registry(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        register(plans)
        result = execute_test(plans[0].id)
        ran = list(getattr(module, "ran"))
        _ = shutdown()

    assert result["status"] == "failed"
    assert ran == ["teardown ran"], "teardown was deferred past the test that owned it"


# ---------------------------------------------------------------------------
# 4. unittest — the translation matrix
# ---------------------------------------------------------------------------

UNITTEST_MATRIX = """
import unittest


class TestMatrix(unittest.TestCase):
    def test_success(self):
        self.assertEqual(1, 1)

    def test_failure_bucket(self):
        self.assertEqual(1, 2)

    def test_error_bucket(self):
        raise ValueError("boom")

    @unittest.skip("decorated")
    def test_skipped_bucket(self):
        raise AssertionError("must not run")

    def test_runtime_skip(self):
        self.skipTest("at runtime")

    @unittest.expectedFailure
    def test_expected_failure_bucket(self):
        self.assertEqual(1, 2)

    @unittest.expectedFailure
    def test_unexpected_success_bucket(self):
        self.assertEqual(1, 1)


class TestSetUpRaises(unittest.TestCase):
    def setUp(self):
        raise RuntimeError("setUp boom")

    def test_body(self):
        assert True


class TestTearDownRaises(unittest.TestCase):
    def tearDown(self):
        raise RuntimeError("tearDown boom")

    def test_body(self):
        assert True


class TestBodyFailsAndTearDownRaises(unittest.TestCase):
    def tearDown(self):
        raise RuntimeError("tearDown boom")

    def test_body(self):
        self.assertEqual(1, 2)


class TestSetUpClassRaises(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise RuntimeError("setUpClass boom")

    def test_body(self):
        assert True


@unittest.skip("whole class")
class TestWholeClassSkipped(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise RuntimeError("must not run")

    def test_body(self):
        raise AssertionError("must not run")
"""


def test_unittest_translation_matrix_matches_pytest(tmp_path: Path) -> None:
    """All five ``TestResult`` buckets plus the phase boundaries, against the oracle.

    Two rows contradict the obvious mapping and both were **probed** rather than assumed:

    * ``test_error_bucket`` — ``TestResult.errors`` — is ``failed``, **not** ``error``.
      pytest drives the whole ``TestCase`` inside the *call* phase
      (`_pytest/unittest.py::TestCaseFunction.runtest` l. 322-353), so any exception the body
      or ``setUp`` raises is a call-phase failure.  ``TestSetUpRaises`` and
      ``TestTearDownRaises`` pin the same thing from the other side: both are ``failed``,
      because ``unittest`` — not the fixture engine — owns those two hooks.
    * ``test_unexpected_success_bucket`` is ``failed``, **not** ``xpassed``.
      `_pytest/unittest.py::addUnexpectedSuccess` (l. 299-311) calls ``fail()`` and says why
      in a comment: *"Preserve unittest behaviour - fail the test. Explicitly not an XPASS."*

    ``TestSetUpClassRaises`` is the one unittest row that really is ``error``: ``setUpClass``
    is a class-scoped autouse **fixture**, so it fails in the setup phase.
    ``TestWholeClassSkipped`` proves the fixture is not even registered for a skipped class —
    its ``setUpClass`` would explode if it were.
    """
    assert differential(tmp_path, UNITTEST_MATRIX, "test_ut.py") == {
        "test_ut.py::TestMatrix::test_success": "passed",
        "test_ut.py::TestMatrix::test_failure_bucket": "failed",
        "test_ut.py::TestMatrix::test_error_bucket": "failed",
        "test_ut.py::TestMatrix::test_skipped_bucket": "skipped",
        "test_ut.py::TestMatrix::test_runtime_skip": "skipped",
        "test_ut.py::TestMatrix::test_expected_failure_bucket": "xfailed",
        "test_ut.py::TestMatrix::test_unexpected_success_bucket": "failed",
        "test_ut.py::TestSetUpRaises::test_body": "failed",
        "test_ut.py::TestTearDownRaises::test_body": "failed",
        "test_ut.py::TestBodyFailsAndTearDownRaises::test_body": "failed",
        "test_ut.py::TestSetUpClassRaises::test_body": "error",
        "test_ut.py::TestWholeClassSkipped::test_body": "skipped",
    }


UNITTEST_CLASS_LIFECYCLE = """
import unittest

events = []


class TestFirst(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        events.append("setUpClass:TestFirst")

    @classmethod
    def tearDownClass(cls):
        events.append("tearDownClass:TestFirst")

    def test_a(self):
        events.append("TestFirst.test_a")

    def test_b(self):
        events.append("TestFirst.test_b")


class TestSecond(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        events.append("setUpClass:TestSecond")

    @classmethod
    def tearDownClass(cls):
        events.append("tearDownClass:TestSecond")

    def test_c(self):
        events.append("TestSecond.test_c")
"""


def test_setupclass_runs_once_per_class_and_tears_down_at_the_boundary(tmp_path: Path) -> None:
    """``setUpClass``/``tearDownClass`` are a class-scoped autouse fixture, so scoping is free.

    ``unittest.TestCase.run`` does **not** call ``setUpClass`` — ``TestSuite`` does — so a
    worker that only calls ``TestCase(name)(result)`` would run every method of a class whose
    class-level setup never happened.  Port of
    `_pytest/unittest.py::_register_unittest_setup_class_fixture` (l. 116-170); the boundary
    comes from ``FixtureRunner.note_test_boundary``, which the class-scope work already
    provides.

    The event sequence is the assertion, not just a pass count: a fixture that ran per *test*
    would still give three passes.
    """
    target = write(tmp_path / "test_life.py", UNITTEST_CLASS_LIFECYCLE)
    with isolated_worker_state():
        results = run_module(target, tmp_path)
        module = sys.modules["test_life"]
        events = getattr(module, "events")
    assert [result["status"] for result in results] == ["passed", "passed", "passed"]
    assert events == [
        "setUpClass:TestFirst",
        "TestFirst.test_a",
        "TestFirst.test_b",
        "tearDownClass:TestFirst",
        "setUpClass:TestSecond",
        "TestSecond.test_c",
        "tearDownClass:TestSecond",
    ]


def test_the_recorder_keeps_unittest_callbacks_in_order(tmp_path: Path) -> None:
    """A body failure **and** a ``tearDown`` error: the body's failure is what is reported.

    pytest appends each callback to ``_excinfo`` and pops ``[0]``
    (`_pytest/unittest.py::_addexcinfo` l. 265 and l. 370-371), so the *order* unittest
    reported them in decides — not a bucket priority.  Probed: the same shape prints
    ``FAILED`` then a separate teardown ``ERROR`` under pytest, i.e. the assertion wins.

    Asserted at the recorder itself as well as through the status, because the status alone
    would also be produced by a rule that simply preferred ``failures`` over ``errors`` — and
    that rule gives the wrong answer the moment ``setUp`` errors and ``tearDown`` asserts.
    """
    recorder = worker._UnittestOutcomeRecorder()  # pyright: ignore[reportPrivateUsage]

    class Case(unittest.TestCase):
        def tearDown(self) -> None:
            raise RuntimeError("tearDown boom")

        def test_body(self) -> None:
            self.assertEqual(1, 2)

    Case("test_body")(result=recorder)

    assert [type(exc).__name__ for exc in recorder.outcomes] == ["AssertionError", "RuntimeError"]
    first = recorder.first_outcome()
    assert isinstance(first, AssertionError)
    assert report_for_phase("call", first, None).status == "failed"


# ---------------------------------------------------------------------------
# 5. the classification switch — one test per branch
# ---------------------------------------------------------------------------


def test_no_exception_is_a_pass() -> None:
    report = report_for_phase("call", None, None)
    assert (report.outcome, report.wasxfail, report.status) == ("passed", None, "passed")
    assert report.message is None
    assert report.plain_pass


@pytest.mark.parametrize(
    "exc",
    [
        worker._Skipped("reason"),  # pyright: ignore[reportPrivateUsage]
        worker._StubSkipped("reason"),  # pyright: ignore[reportPrivateUsage]
        unittest.SkipTest("reason"),
    ],
)
def test_every_skip_type_is_classified_as_skipped(exc: BaseException) -> None:
    """Three *different classes* mean "skip", and all three must be recognised by type.

    ``rustest.decorators.Skipped`` is what ``pytest.skip()`` raises through the shim;
    ``rustest._pytest_stub.outcomes.Skipped`` is what a suite importing pytest's internals
    raises; ``unittest.SkipTest`` is what ``self.skipTest()`` and ``@unittest.skip`` raise and
    what pytest converts explicitly.  Testing only one of them would leave the other two
    silently reported as failures.
    """
    report = report_for_phase("call", exc, None)
    assert (report.outcome, report.status) == ("skipped", "skipped")
    assert report.message == "reason"


def test_the_xfail_exception_is_classified_by_type_not_by_the_mark() -> None:
    """``pytest.xfail()`` from the body xfails a test carrying **no mark at all**.

    `_pytest/skipping.py` l. 279-282 checks the exception before it ever looks at the stash,
    which is why this branch cannot be folded into the mark promotion below it.
    """
    report = report_for_phase(
        "call",
        worker._XFailed("from the body"),  # pyright: ignore[reportPrivateUsage]
        None,
    )
    assert (report.outcome, report.wasxfail, report.status) == (
        "skipped",
        "from the body",
        "xfailed",
    )


def test_an_empty_xfail_reason_is_still_an_xfail() -> None:
    """``wasxfail`` is detected by **presence**, never by truthiness.

    ``@unittest.expectedFailure`` carries no reason at all, so ``wasxfail`` is ``""``; a
    ``if report.wasxfail:`` test would report it as an ordinary skip.
    """
    report = report_for_phase(
        "call",
        worker._XFailed(""),  # pyright: ignore[reportPrivateUsage]
        None,
    )
    assert report.wasxfail == ""
    assert report.status == "xfailed"


@pytest.mark.parametrize(
    "exc",
    [
        AssertionError("assert 1 == 2"),
        ValueError("boom"),
        worker._Failed("explicit"),  # pyright: ignore[reportPrivateUsage]
        worker._StubFailed("explicit"),  # pyright: ignore[reportPrivateUsage]
    ],
)
def test_everything_else_from_the_body_is_a_failure(exc: BaseException) -> None:
    """The default branch: no type list, no message match, just "not skip and not xfail".

    ``pytest.fail()``'s ``Failed`` is deliberately *not* special-cased — pytest reports it
    exactly like an ``AssertionError``, and a branch that treated it differently would be a
    divergence with nothing to gain.
    """
    report = report_for_phase("call", exc, None)
    assert (report.outcome, report.status) == ("failed", "failed")
    assert report.message is not None
    assert type(exc).__name__ in report.message


@pytest.mark.parametrize(
    ("phase", "expected"),
    [("setup", "error"), ("call", "failed"), ("teardown", "error")],
)
def test_failed_becomes_error_outside_the_call_phase(phase: str, expected: str) -> None:
    """The whole ``failed``/``error`` distinction, in one table.

    Same exception, three phases: `_pytest/runner.py` l. 214-223 returns ``("error", "E",
    "ERROR")`` for a failed setup or teardown, and `_pytest/terminal.py` l. 333-335 restates
    it.  Reporting a broken fixture as ``failed`` would send someone reading the ``E`` column
    to debug a body that never executed.
    """
    assert report_for_phase(phase, RuntimeError("boom"), None).status == expected


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_a_skip_is_skipped_in_every_phase(phase: str) -> None:
    """A skip is never promoted to ``error``: `runner.py` l. 219-220 returns ``skipped``."""
    assert (
        report_for_phase(
            phase,
            worker._Skipped("nope"),  # pyright: ignore[reportPrivateUsage]
            None,
        ).status
        == "skipped"
    )


def test_an_xfail_mark_promotes_a_failure_to_xfailed() -> None:
    xfailed = Xfail(reason="known broken", run=True, strict=False, raises=None)
    report = report_for_phase("call", AssertionError("boom"), xfailed)
    assert (report.outcome, report.wasxfail, report.status) == (
        "skipped",
        "known broken",
        "xfailed",
    )


def test_an_xfail_mark_promotes_a_pass_to_xpassed_only_in_the_call_phase() -> None:
    """``elif call.when == "call"`` (l. 300) — a *passing* setup is not an xpass.

    Without the phase guard the setup report of every xfail-marked test would come back
    ``xpassed`` and win the reduction, hiding whatever the body actually did.
    """
    xfailed = Xfail(reason="but it works", run=True, strict=False, raises=None)
    assert report_for_phase("call", None, xfailed).status == "xpassed"
    assert report_for_phase("setup", None, xfailed).status == "passed"
    assert report_for_phase("teardown", None, xfailed).status == "passed"


def test_a_strict_xpass_is_a_failure_with_pytests_own_wording() -> None:
    """`skipping.py` l. 300-303 — the one xfail shape that is *not* an xfail category."""
    xfailed = Xfail(reason="should fail", run=True, strict=True, raises=None)
    report = report_for_phase("call", None, xfailed)
    assert (report.outcome, report.wasxfail, report.status) == ("failed", None, "failed")
    assert report.message == "[XPASS(strict)] should fail"


def test_a_strict_xfail_is_still_an_xfail() -> None:
    """``strict`` only changes what an unexpected *pass* means (l. 296-303)."""
    xfailed = Xfail(reason="known broken", run=True, strict=True, raises=None)
    assert report_for_phase("call", AssertionError("boom"), xfailed).status == "xfailed"


def test_the_raises_filter_narrows_which_exceptions_count() -> None:
    """`skipping.py` l. 285-299: a non-matching exception is a real failure."""
    xfailed = Xfail(reason="only ValueError", run=True, strict=False, raises=(ValueError,))
    assert report_for_phase("call", ValueError("yes"), xfailed).status == "xfailed"
    assert report_for_phase("call", TypeError("no"), xfailed).status == "failed"


def test_a_skip_is_never_promoted_by_an_xfail_mark() -> None:
    """``elif not rep.skipped and xfailed`` (l. 283) — the guard that makes skip win."""
    xfailed = Xfail(reason="xfail loses", run=True, strict=False, raises=None)
    report = report_for_phase(
        "call",
        worker._Skipped("skip wins"),  # pyright: ignore[reportPrivateUsage]
        xfailed,
    )
    assert (report.status, report.message) == ("skipped", "skip wins")


def test_every_status_the_switch_can_produce_is_on_the_wire_contract() -> None:
    """No branch may invent a seventh status — the decoder accepts one, the report does not.

    ``src/v2/protocol.rs`` leaves ``status`` an unvalidated ``String`` on purpose, so an
    undocumented value would travel all the way to the orchestrator before anything noticed.
    This closes that loop on the producing side.
    """
    xfailed = Xfail(reason="r", run=True, strict=False, raises=None)
    strict = Xfail(reason="r", run=True, strict=True, raises=None)
    produced = {
        report_for_phase(phase, exc, mark).status
        for phase in ("setup", "call", "teardown")
        for exc in (
            None,
            AssertionError("x"),
            worker._Skipped("x"),  # pyright: ignore[reportPrivateUsage]
            worker._XFailed("x"),  # pyright: ignore[reportPrivateUsage]
        )
        for mark in (None, xfailed, strict)
    }
    assert produced <= set(STATUSES)
    assert produced == {"passed", "failed", "skipped", "xfailed", "xpassed", "error"}


# ---------------------------------------------------------------------------
# the reduction
# ---------------------------------------------------------------------------


def test_the_reduction_takes_the_earliest_phase_that_is_not_a_plain_pass() -> None:
    """One line on the wire out of pytest's up-to-three reports.

    Each row is a shape pytest was probed for, and the pairs are what make the rule
    load-bearing: a passing body with a broken teardown must not report ``passed``, and a
    *failing* body with a broken teardown must not report ``error``.
    """
    passed = PhaseReport("setup", "passed")
    call_passed = PhaseReport("call", "passed")
    call_failed = PhaseReport("call", "failed", "boom")
    teardown_failed = PhaseReport("teardown", "failed", "boom")
    setup_failed = PhaseReport("setup", "failed", "boom")
    teardown_passed = PhaseReport("teardown", "passed")

    assert reduce_reports([passed, call_passed, teardown_passed]).status == "passed"
    assert reduce_reports([setup_failed, teardown_passed]).status == "error"
    assert reduce_reports([passed, call_passed, teardown_failed]).status == "error"
    assert reduce_reports([passed, call_failed, teardown_failed]).status == "failed"


def test_an_xpass_is_not_a_plain_pass_and_wins_the_reduction() -> None:
    """``plain_pass`` is ``passed`` *and* no ``wasxfail`` — an xpass is news, so it wins.

    A reduction that only looked at ``outcome`` would skip straight past an ``xpassed`` call
    report and report whatever the teardown said.
    """
    xpassed = PhaseReport("call", "passed", None, "but it works")
    teardown_failed = PhaseReport("teardown", "failed", "boom")
    assert not xpassed.plain_pass
    assert reduce_reports([PhaseReport("setup", "passed"), xpassed, teardown_failed]).status == (
        "xpassed"
    )


# ---------------------------------------------------------------------------
# mark evaluation, at the unit level
# ---------------------------------------------------------------------------


def test_every_skipif_is_considered_before_any_skip() -> None:
    """pytest's two separate loops (l. 170-191), not one pass over the mark list.

    With ``skip`` listed first — the closest-first order a decorator stack produces — a
    single-loop implementation reports the ``skip`` mark's reason.  pytest reports the
    condition's.
    """
    marks = [
        MarkSpec("skip", kwargs={"reason": "the skip mark"}),
        MarkSpec("skipif", args=(True,), kwargs={"reason": "the skipif mark"}),
    ]
    skipped = evaluate_skip_marks(marks, {})
    assert skipped is not None
    assert skipped.reason == "the skipif mark"


def test_a_false_skipif_does_not_skip() -> None:
    assert evaluate_skip_marks([MarkSpec("skipif", args=(False,), kwargs={"reason": "r"})], {}) is (
        None
    )


def test_an_unconditional_skipif_skips() -> None:
    """l. 176-179: a ``skipif`` with no condition at all is an unconditional skip."""
    skipped = evaluate_skip_marks([MarkSpec("skipif", kwargs={"reason": "no condition"})], {})
    assert skipped is not None
    assert skipped.reason == "no condition"


def test_the_condition_keyword_takes_precedence_over_positional_args() -> None:
    """l. 171-174 — ``condition=`` collapses to one condition and the args are ignored."""
    mark = MarkSpec("skipif", args=(True,), kwargs={"condition": False, "reason": "r"})
    assert evaluate_skip_marks([mark], {}) is None


def test_a_string_condition_is_evaluated_against_the_modules_globals() -> None:
    """`skipping.py` l. 98-118 — compiled and ``eval``'d, never tested for truthiness.

    The false row is the one that matters: every non-empty string is truthy, so an
    implementation that skipped the ``eval`` would skip this test too.
    """
    namespace: Mapping[str, object] = {"FLAG": True}
    true_mark = MarkSpec("skipif", args=("FLAG",), kwargs={"reason": "flag set"})
    false_mark = MarkSpec("skipif", args=("not FLAG",), kwargs={"reason": "flag clear"})
    assert evaluate_skip_marks([true_mark], namespace) is not None
    assert evaluate_skip_marks([false_mark], namespace) is None


def test_a_string_condition_can_use_sys_and_platform() -> None:
    """The three modules pytest injects into ``globals_`` (l. 99-104)."""
    mark = MarkSpec("skipif", args=("sys.platform == sys.platform",), kwargs={"reason": "r"})
    assert evaluate_skip_marks([mark], {}) is not None


def test_a_string_condition_with_no_reason_reports_the_condition() -> None:
    """l. 146-150 — ``reason`` defaults to ``"condition: " + condition`` for a string."""
    skipped = evaluate_skip_marks([MarkSpec("skipif", args=("True",))], {})
    assert skipped is not None
    assert skipped.reason == "condition: True"


def test_a_boolean_condition_without_a_reason_is_an_error() -> None:
    """l. 150-156 — pytest ``fail()``s rather than skipping a test whose author never said why."""
    with pytest.raises(worker._Failed) as excinfo:  # pyright: ignore[reportPrivateUsage]
        _ = evaluate_skip_marks([MarkSpec("skipif", args=(True,))], {})
    assert "you need to specify reason=STRING" in str(excinfo.value)


def test_a_broken_string_condition_is_an_error_naming_the_mark() -> None:
    """l. 127-133 — the condition's own exception is reported, not propagated."""
    with pytest.raises(worker._Failed) as excinfo:  # pyright: ignore[reportPrivateUsage]
        _ = evaluate_skip_marks(
            [MarkSpec("skipif", args=("undefined_name",), kwargs={"reason": "r"})], {}
        )
    message = str(excinfo.value)
    assert "Error evaluating 'skipif' condition" in message
    assert "NameError" in message


def test_a_syntactically_invalid_string_condition_is_an_error() -> None:
    """l. 119-126 — its own branch, with pytest's caret line."""
    with pytest.raises(worker._Failed) as excinfo:  # pyright: ignore[reportPrivateUsage]
        _ = evaluate_skip_marks([MarkSpec("skipif", args=("1 ==",), kwargs={"reason": "r"})], {})
    assert "SyntaxError: invalid syntax" in str(excinfo.value)


def test_xfail_defaults_match_pytests_ini_defaults() -> None:
    """``run=True``, ``strict=False`` (the ``xfail_strict`` ini default), ``raises=None``."""
    xfailed = evaluate_xfail_marks([MarkSpec("xfail", kwargs={"reason": "r"})], {})
    assert xfailed == Xfail(reason="r", run=True, strict=False, raises=None)


def test_an_explicit_none_reason_is_not_the_string_none() -> None:
    """rustest's ``MarkGenerator`` normalises absent keywords to ``None``, pytest omits them.

    Without the coercion the literal word ``None`` would appear in the report where pytest
    prints nothing at all.
    """
    xfailed = evaluate_xfail_marks([MarkSpec("xfail", kwargs={"reason": None})], {})
    assert xfailed is not None
    assert xfailed.reason == ""


def test_raises_accepts_a_class_or_a_tuple_and_refuses_anything_else() -> None:
    """``isinstance`` needs real classes; a repr string would ``TypeError`` deep in reporting.

    The refusal is why marks reach execution as :class:`MarkSpec`: the JSON-safe wire form
    turns ``ValueError`` into the string ``"<class 'ValueError'>"``.
    """
    single = evaluate_xfail_marks([MarkSpec("xfail", kwargs={"raises": ValueError})], {})
    assert single is not None and single.raises == (ValueError,)

    pair = evaluate_xfail_marks([MarkSpec("xfail", kwargs={"raises": (ValueError, TypeError)})], {})
    assert pair is not None and pair.raises == (ValueError, TypeError)

    with pytest.raises(worker._Failed) as excinfo:  # pyright: ignore[reportPrivateUsage]
        _ = evaluate_xfail_marks([MarkSpec("xfail", kwargs={"raises": "<class 'ValueError'>"})], {})
    assert "must be an exception class" in str(excinfo.value)


def test_the_json_safe_wire_form_would_have_destroyed_the_raises_filter() -> None:
    """The reason :class:`MarkSpec` exists, asserted rather than described.

    ``to_wire`` is lossy by design — the manifest only displays marks.  This pins that the
    loss is real, so nobody "simplifies" the execute half back onto the wire form.
    """
    mark = MarkSpec("xfail", kwargs={"raises": ValueError, "reason": "r"})
    assert mark.kwargs["raises"] is ValueError
    assert mark.to_wire()["kwargs"]["raises"] == "<class 'ValueError'>"


def test_a_falsy_non_builtin_condition_survives_as_an_object() -> None:
    """The second half of the same argument: ``repr()`` of a falsy object is truthy.

    A condition object with ``__bool__`` returning ``False`` must not skip.  Through the wire
    form it would arrive as a non-empty repr string and skip every time.
    """

    class Falsy:
        def __bool__(self) -> bool:
            return False

    condition = Falsy()
    assert evaluate_skip_marks(
        [MarkSpec("skipif", args=(condition,), kwargs={"reason": "r"})], {}
    ) is (None)
    wire = MarkSpec("skipif", args=(condition,), kwargs={"reason": "r"}).to_wire()
    assert bool(wire["args"][0]) is True


# ---------------------------------------------------------------------------
# the wire shape
# ---------------------------------------------------------------------------


def test_a_passing_result_matches_the_golden_line(tmp_path: Path) -> None:
    """`src/v2/protocol.rs::test_result_omits_the_optional_fields_it_does_not_carry`.

    ``message``/``stdout``/``stderr`` are absent **keys**, not nulls and not empty strings, so
    the common case stays a short line.
    """
    target = write(tmp_path / "test_ok.py", "def test_ok():\n    assert True\n")
    with isolated_worker_state():
        (result,) = run_module(target, tmp_path)

    assert list(result) == ["op", "id", "status", "duration_s"]
    assert result["op"] == "test_result"
    assert result["id"] == "test_ok.py::test_ok"
    assert result["status"] == "passed"
    assert isinstance(result["duration_s"], float)

    encoded = encode_response(result)
    for key in ('"message":', '"stdout":', '"stderr":'):
        assert key not in encoded
    assert encoded.startswith('{"op":"test_result","id":"test_ok.py::test_ok","status":"passed"')


def test_a_populated_result_keeps_the_serde_field_order(tmp_path: Path) -> None:
    """Key order mirrors the Rust struct, so the encoded line matches the golden shape."""
    target = write(
        tmp_path / "test_noisy.py",
        """
        import sys


        def test_noisy():
            print("out")
            print("err", file=sys.stderr)
            assert False
        """,
    )
    with isolated_worker_state():
        (result,) = run_module(target, tmp_path)

    assert list(result) == ["op", "id", "status", "duration_s", "message", "stdout", "stderr"]
    assert result["status"] == "failed"
    assert "\n" not in encode_response(result), "the transport is JSON-lines"


def test_the_duration_covers_the_whole_protocol(tmp_path: Path) -> None:
    """``perf_counter`` around setup, call and teardown — a monotonic clock, so never negative.

    The sleeping fixture is what makes it more than a smoke test: a duration measured around
    the *body* alone would come back under the threshold.
    """
    target = write(
        tmp_path / "test_slow.py",
        """
        import time

        import pytest


        @pytest.fixture
        def slow_setup():
            time.sleep(0.05)
            yield 1
            time.sleep(0.05)


        def test_slow(slow_setup):
            assert True
        """,
    )
    with isolated_worker_state():
        (result,) = run_module(target, tmp_path)
    assert result["status"] == "passed"
    assert result["duration_s"] >= 0.1


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_stdout_and_stderr_are_captured_per_test(tmp_path: Path) -> None:
    """Captured separately, and **not** leaked into the next test's buffers."""
    target = write(
        tmp_path / "test_cap.py",
        """
        import sys


        def test_first():
            print("first out")
            print("first err", file=sys.stderr)


        def test_second():
            print("second out")
        """,
    )
    with isolated_worker_state():
        first, second = run_module(target, tmp_path)

    assert first["stdout"] == "first out\n"
    assert first["stderr"] == "first err\n"
    assert second["stdout"] == "second out\n"
    assert "stderr" not in second


def test_capture_spans_setup_and_teardown_too(tmp_path: Path) -> None:
    """Fixtures print as well, and pytest attaches their output to the same test.

    Teardown output is the load-bearing half: a capture that stopped at the end of the body
    would drop it, and a teardown that reports *why* it failed is exactly when it matters.
    """
    target = write(
        tmp_path / "test_cap2.py",
        """
        import pytest


        @pytest.fixture
        def talkative():
            print("setup speaks")
            yield 1
            print("teardown speaks")


        def test_body(talkative):
            print("body speaks")
        """,
    )
    with isolated_worker_state():
        (result,) = run_module(target, tmp_path)
    assert result["stdout"] == "setup speaks\nbody speaks\nteardown speaks\n"


def test_nothing_captured_means_no_keys_at_all(tmp_path: Path) -> None:
    """An empty stream is an omitted key, never ``""`` — the protocol's omission rule."""
    target = write(tmp_path / "test_quiet.py", "def test_quiet():\n    assert True\n")
    with isolated_worker_state():
        (result,) = run_module(target, tmp_path)
    assert "stdout" not in result
    assert "stderr" not in result


CLOSES_STDOUT = """
import sys


def test_before():
    print("before")
    assert True


def test_closes_stdout():
    sys.stdout.close()


def test_after():
    print("after")
    assert True
"""


def test_a_test_that_closes_stdout_does_not_kill_the_worker(tmp_path: Path) -> None:
    """The reviewer's crash: ``sys.stdout.close()`` closes **the capture buffer itself**.

    Every later ``getvalue()`` then raises ``ValueError: I/O operation on closed file`` — out
    of ``execute_test``, past the protocol loop, killing the worker with **exit 1 mid-stream**.
    Every queued test goes unanswered, and exit 1 is indistinguishable from an uncaught
    traceback, i.e. from protocol drift.

    Run through the **real subprocess** on purpose: an in-process test would exercise the
    guard but not the thing that was actually broken, which is that the worker stays alive and
    keeps answering. All three tests must come back, ``shutdown`` must be honoured, and the
    exit code must be 0.

    Status of the closer is pytest's, probed: ``FAILED`` for the call phase and a separate
    ``ERROR`` at teardown, because reading the capture is part of pytest's per-phase protocol
    (`_pytest/capture.py::snap`). :meth:`_Capture.broken` reproduces the pair, so the reduction
    reports ``failed``.

    **Documented divergence on the third test.** pytest's capture is session-wide, so once a
    test closes it every *subsequent* test errors too (probed: ``test_after`` is ERROR at both
    setup and teardown). This worker builds a fresh buffer per test, so ``test_after`` recovers
    and passes. Deliberately not matched: one test's vandalism poisoning the rest of the file
    is pytest's bug to keep, not ours to copy.
    """
    target = write(tmp_path / "test_close.py", CLOSES_STDOUT)
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_close.py::test_before"},
            {"op": "execute_test", "id": "test_close.py::test_closes_stdout"},
            {"op": "execute_test", "id": "test_close.py::test_after"},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, f"the worker died: {proc.stderr}"
    messages = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [message["op"] for message in messages] == [
        "ready",
        "collected",
        "test_result",
        "test_result",
        "test_result",
        "bye",
    ], "every queued test must still be answered"

    before, closer, after = messages[2:5]
    assert (before["id"], before["status"], before["stdout"]) == (
        "test_close.py::test_before",
        "passed",
        "before\n",
    )
    assert closer["status"] == "failed", "pytest reports the closer FAILED; so must we"
    assert closer["message"] == worker.CAPTURE_CLOSED_MESSAGE
    assert closer["stdout"] == worker.CAPTURE_CLOSED_MESSAGE
    assert (after["status"], after["stdout"]) == ("passed", "after\n")


def test_a_test_that_closes_stderr_is_caught_too(tmp_path: Path) -> None:
    """Both streams, not just ``stdout`` — the buffers are independent objects.

    ``sys.stderr.close()`` breaks the capture exactly as ``sys.stdout.close()`` does, and a
    ``broken`` check that only consulted ``stdout`` would report this test ``passed`` and then
    hand the wire a ``stderr`` value read from a closed buffer. The mutation pass caught this:
    the stdout-only test above is green against a stdout-only check.
    """
    target = write(
        tmp_path / "test_close3.py",
        """
        import sys


        def test_closes_stderr():
            sys.stderr.close()
        """,
    )
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_close3.py::test_closes_stderr"},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.splitlines()[2])
    assert result["status"] == "failed"
    assert result["stderr"] == worker.CAPTURE_CLOSED_MESSAGE


def test_a_test_that_closes_stdout_keeps_its_own_failure(tmp_path: Path) -> None:
    """A test that *both* fails and closes the stream reports the real failure.

    Only a phase that would otherwise be a **plain pass** is rewritten to the capture message
    — the assertion is the more useful diagnosis, and it is also what pytest reports, since its
    own ``snap`` runs after the body's exception has been recorded.
    """
    target = write(
        tmp_path / "test_close2.py",
        """
        import sys


        def test_fails_and_closes():
            sys.stdout.close()
            assert 1 == 2
        """,
    )
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_close2.py::test_fails_and_closes"},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.splitlines()[2])
    assert result["status"] == "failed"
    assert "AssertionError" in result["message"]
    assert result["message"] != worker.CAPTURE_CLOSED_MESSAGE


BOUNDARY_OUTPUT = """
import pytest


@pytest.fixture(scope="module")
def per_module():
    yield 1
    print("TEARDOWN-MODULE")


@pytest.fixture(scope="class")
def per_class():
    yield 1
    print("TEARDOWN-CLASS")


class TestFirst:
    def test_one(self, per_module, per_class):
        print("ONE")


def test_two(per_module):
    print("TWO")
"""


def test_boundary_teardown_output_is_not_charged_to_the_next_test(tmp_path: Path) -> None:
    """The previous tests' teardown must not appear in the **next** test's ``stdout``.

    Class- and module-scoped teardown is drained at a boundary, and the boundary is detected
    when the *incoming* test arrives. Running that drain inside the incoming test's capture
    window prefixed ``TEARDOWN-CLASS`` onto ``test_two``'s output — attributing one test's
    output to another on the wire, which is worse than losing it.

    :func:`drain_boundaries` now runs **before** the capture opens, so boundary output goes to
    the worker's stderr. Divergence from pytest, deliberate and documented: pytest prints it
    under the *previous* test's teardown section, which needs the ``nextitem`` lookahead the
    execute wire does not have.

    The assertion is exact equality, not "does not contain": a ``TEARDOWN-`` substring check
    would still pass if some *other* teardown leaked in.
    """
    target = write(tmp_path / "test_bound.py", BOUNDARY_OUTPUT)
    with isolated_worker_state():
        results = run_module(target, tmp_path)

    by_id = {result["id"]: result for result in results}
    assert by_id["test_bound.py::TestFirst::test_one"]["stdout"] == "ONE\n"
    second = by_id["test_bound.py::test_two"]["stdout"]
    assert second == "TWO\n", "the previous class's teardown was charged to this test"


def test_a_boundary_teardown_failure_is_reported_against_the_incoming_test(
    tmp_path: Path,
) -> None:
    """A class teardown that fails at a **boundary** is an ``error``, never dropped.

    ``drain_boundaries`` returns its exception instead of raising, so it needs somewhere to
    go; the only phase left is the incoming test's setup. This pins that it gets there —
    ``drain_boundaries`` swallowing it would lose a real teardown failure entirely, and the
    shutdown-drain tests do not cover this path because a *boundary* drain happens mid-run,
    not at shutdown.

    **Documented divergence.** pytest attributes this to ``test_one``'s teardown (``PASSED``
    then ``ERROR at teardown of test_one``) and leaves ``test_two`` passing, because
    ``runtestprotocol`` is handed ``nextitem``. The execute wire has no lookahead, so the
    failure lands on ``test_two`` — the wrong test, but loudly. Asserted here so the
    divergence is visible rather than implicit.
    """
    target = write(
        tmp_path / "test_bteardown.py",
        """
        import pytest


        @pytest.fixture(scope="class")
        def per_class():
            yield 1
            raise RuntimeError("class teardown boom")


        class TestFirst:
            def test_one(self, per_class):
                assert True


        class TestSecond:
            def test_two(self):
                assert True
        """,
    )
    with isolated_worker_state():
        results = run_module(target, tmp_path)

    assert statuses(results) == {
        "test_bteardown.py::TestFirst::test_one": "passed",
        "test_bteardown.py::TestSecond::test_two": "error",
    }
    assert "class teardown boom" in results[1]["message"]


def test_a_keyboard_interrupt_ends_the_run_instead_of_being_classified(tmp_path: Path) -> None:
    """Ctrl-C aborts — port of `_pytest/runner.py::call_runtest_hook` l. 242-244.

    Classifying it would turn an interrupt into a ``failed`` test and then calmly run the
    next one. Driven through the subprocess because that is the only place "the run ends" is
    observable: the test gets **no** ``test_result`` and the test queued after it never runs.
    """
    target = write(
        tmp_path / "test_abort.py",
        """
        def test_aborts():
            raise KeyboardInterrupt


        def test_never_reached():
            assert True
        """,
    )
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_abort.py::test_aborts"},
            {"op": "execute_test", "id": "test_abort.py::test_never_reached"},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode != 0
    assert [json.loads(line)["op"] for line in proc.stdout.splitlines()] == ["ready", "collected"]


def test_a_system_exit_is_an_ordinary_failure_and_the_run_continues(tmp_path: Path) -> None:
    """``SystemExit`` is **not** an abort — probed, and the obvious guess is wrong.

    Both exceptions end a process, so the natural assumption is that both abort a run.
    pytest disagrees: ``CallInfo.from_call`` catches ``BaseException`` and only the
    ``reraise`` set — ``(Exit,)`` plus ``KeyboardInterrupt`` — escapes, so ``raise SystemExit``
    in a body is reported ``FAILED`` and the next test runs. (``SystemExit`` reraises during
    *collection* only, `runner.py` l. 392.)

    Matching matters: treating it as an abort would let one ``sys.exit()`` deep inside a
    library silently truncate a run, which is the same silent-truncation shape as the
    shutdown-drain false green.
    """
    target = write(
        tmp_path / "test_sysexit.py",
        """
        def test_exits():
            raise SystemExit


        def test_still_runs():
            assert True
        """,
    )
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_sysexit.py::test_exits"},
            {"op": "execute_test", "id": "test_sysexit.py::test_still_runs"},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    messages = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [(m["id"], m["status"]) for m in messages if m["op"] == "test_result"] == [
        ("test_sysexit.py::test_exits", "failed"),
        ("test_sysexit.py::test_still_runs", "passed"),
    ]


def test_a_failure_message_names_the_test_not_the_runner(tmp_path: Path) -> None:
    """Port of pytest's traceback filters — the runner's own frames are not the user's problem.

    Without the filter the message opens with ``_run_phases``/``_run_call`` and, for a
    ``TestCase``, four frames of ``unittest.case`` plumbing before reaching the assertion.
    """
    target = write(
        tmp_path / "test_msg.py",
        """
        import unittest


        def test_plain():
            assert 1 == 2


        class TestCaseStyle(unittest.TestCase):
            def test_method(self):
                self.assertEqual(1, 2)
        """,
    )
    with isolated_worker_state():
        plain, case = run_module(target, tmp_path)

    assert "_v2_worker.py" not in plain["message"]
    assert "test_msg.py" in plain["message"]

    assert "_v2_worker.py" not in case["message"]
    assert "unittest" not in case["message"].split("AssertionError")[0]
    assert "test_msg.py" in case["message"]
    assert case["message"].endswith("AssertionError: 1 != 2")


# ---------------------------------------------------------------------------
# scope boundaries the execute half owns
# ---------------------------------------------------------------------------


def test_leaving_a_module_tears_its_module_scoped_fixtures_down(tmp_path: Path) -> None:
    """``note_module_boundary`` — without it a module fixture lives until Shutdown.

    Driven through :func:`execute_test` for both files against one runner, which is what the
    worker does; the assertion is that the first module's teardown has already happened by
    the time the second module's test runs, not merely that it happens eventually.
    """
    shared = write(
        tmp_path / "conftest.py",
        """
        import pytest

        events = []


        @pytest.fixture(scope="module")
        def per_module():
            events.append("setup")
            yield object()
            events.append("teardown")
        """,
    )
    assert shared.exists()
    first = write(
        tmp_path / "test_one.py",
        "def test_one(per_module):\n    from conftest import events\n\n    assert events == ['setup']\n",
    )
    second = write(
        tmp_path / "test_two.py",
        """
        def test_two(per_module):
            from conftest import events

            assert events == ["setup", "teardown", "setup"]
        """,
    )

    with isolated_worker_state():
        results: list[ResultResponse] = []
        for target in (first, second):
            module, registry = build_registry(target, tmp_path)
            _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
            register(plans)
            results += [execute_test(plan.id) for plan in plans]
        _ = shutdown()
        events = getattr(sys.modules["conftest"], "events")

    assert [result["status"] for result in results] == ["passed", "passed"], results
    assert events == ["setup", "teardown", "setup", "teardown"]


def test_shutdown_drains_every_open_scope(tmp_path: Path) -> None:
    """A session fixture's teardown must run before the process ends, not be abandoned."""
    target = write(
        tmp_path / "test_sess.py",
        """
        import pytest

        events = []


        @pytest.fixture(scope="session")
        def per_session():
            events.append("setup")
            yield 1
            events.append("teardown")


        def test_uses(per_session):
            assert per_session == 1
        """,
    )
    with isolated_worker_state():
        module, registry = build_registry(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        register(plans)
        _ = execute_test(plans[0].id)
        assert getattr(module, "events") == ["setup"]
        _ = shutdown()
        assert getattr(module, "events") == ["setup", "teardown"]


def test_a_shutdown_drain_failure_is_returned_not_swallowed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A teardown failure with no test left to own it must still end the run.

    The in-process half: ``drain_at_shutdown`` **returns** the exception (and reports it on
    stderr) rather than swallowing it, so ``main`` can turn it into
    :data:`SHUTDOWN_TEARDOWN_EXIT`.  ``bye`` is still answered, because the response stream
    really is complete — what is not complete is the run.

    This test used to be named ``..._is_reported_but_not_fatal`` and asserted the swallow. It
    was wrong: a class- or module-scoped teardown that fails on the **last** test routed to a
    worker was reported ``passed`` with the traceback buried in stderr, i.e. a green run over
    a real failure. The subprocess half is
    ``test_a_shutdown_drain_failure_exits_nonzero_after_bye``.
    """
    target = write(
        tmp_path / "test_boom.py",
        """
        import pytest


        @pytest.fixture(scope="session")
        def explodes():
            yield 1
            raise RuntimeError("session teardown boom")


        def test_uses(explodes):
            assert True
        """,
    )
    with isolated_worker_state():
        module, registry = build_registry(target, tmp_path)
        _entries, plans = collect_module(module, target, tmp_path, DEFAULT_NAMING, registry)
        register(plans)
        assert execute_test(plans[0].id)["status"] == "passed"
        failure = shutdown()

    assert isinstance(failure, RuntimeError)
    assert "session teardown boom" in capsys.readouterr().err


def test_a_shutdown_drain_failure_exits_nonzero_after_bye(tmp_path: Path) -> None:
    """The reviewer's shape: ``tearDownClass`` raises on the worker's **last** test.

    That test is already answered ``passed`` — correctly, its body passed — so there is no
    test left for the failure to be attributed to. Before this fix the worker exited 0 and
    the run came back **green** with a traceback in stderr.

    The fix uses the loud channel that already exists: ``bye`` is still written (the stream
    is well-formed) and the process exits :data:`SHUTDOWN_TEARDOWN_EXIT`, which the
    orchestrator already treats as a failed run —
    ``src/v2/collect.rs::a_nonzero_exit_after_bye_is_still_a_failure``. Distinct from 2 so
    "your teardown is broken" is never confused with "the protocol drifted".
    """
    target = write(
        tmp_path / "test_last.py",
        """
        import unittest


        class TestLast(unittest.TestCase):
            @classmethod
            def tearDownClass(cls):
                raise RuntimeError("tearDownClass boom")

            def test_only(self):
                assert True
        """,
    )
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_last.py::TestLast::test_only"},
            {"op": "shutdown"},
        ]
    )

    messages = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [message["op"] for message in messages] == ["ready", "collected", "test_result", "bye"]
    assert messages[2]["status"] == "passed", "the body did pass; the class teardown did not"

    why = f"a broken class teardown on the last test must not exit 0: {proc.stderr}"
    assert proc.returncode == worker.SHUTDOWN_TEARDOWN_EXIT, why
    assert proc.returncode != 2, "not protocol drift"
    assert "tearDownClass boom" in proc.stderr


def test_the_runner_is_shared_across_execute_calls(tmp_path: Path) -> None:
    """One runner per worker: a module-scoped fixture must not be rebuilt per test.

    A fresh runner per ``execute_test`` would still make both tests pass — it is the
    *identity* of the value that catches it.
    """
    target = write(
        tmp_path / "test_shared.py",
        """
        import pytest

        seen = []


        @pytest.fixture(scope="module")
        def once():
            seen.append(object())
            return seen[-1]


        def test_a(once):
            assert once is seen[0]


        def test_b(once):
            assert once is seen[0]
        """,
    )
    with isolated_worker_state():
        results = run_module(target, tmp_path)
        seen = getattr(sys.modules["test_shared"], "seen")
    assert [result["status"] for result in results] == ["passed", "passed"]
    assert len(seen) == 1


def test_a_stale_finalizer_does_not_evict_the_live_cache_entry() -> None:
    """`FixtureDef.finish` clears *its own* ``cached_result``; the dict needs an identity guard.

    A parametrization change finishes the old instance and immediately caches a new one,
    while the *old* finalizer is still on its scope bucket waiting to be drained.  Draining it
    must be a no-op — an unconditional ``pop`` would evict the live value and rebuild a
    fixture that was supposed to be cached.
    """
    runner = FixtureRunner()
    fixturedef = worker.FixtureDef(
        name="anything",
        func=lambda: 1,
        scope="module",
        params=None,
        autouse=False,
        baseid="",
        argnames=(),
    )
    stale = worker._Finalizer(fixturedef)  # pyright: ignore[reportPrivateUsage]
    live = worker._Finalizer(fixturedef)  # pyright: ignore[reportPrivateUsage]
    runner._cache[fixturedef] = worker._Cached("live value", None, live)  # pyright: ignore[reportPrivateUsage]

    runner._finish(stale)  # pyright: ignore[reportPrivateUsage]
    assert runner._cache[fixturedef].value == "live value"  # pyright: ignore[reportPrivateUsage]

    runner._finish(live)  # pyright: ignore[reportPrivateUsage]
    assert fixturedef not in runner._cache  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# the protocol op
# ---------------------------------------------------------------------------


def test_execute_test_over_the_real_subprocess_protocol(tmp_path: Path) -> None:
    """End to end: init, collect, execute, shutdown — the path the orchestrator drives."""
    target = write(
        tmp_path / "test_wire.py",
        """
        import pytest


        def test_pass():
            assert True


        @pytest.mark.skip(reason="nope")
        def test_skip():
            raise AssertionError("must not run")


        def test_fail():
            assert 1 == 2
        """,
    )
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_wire.py::test_pass"},
            {"op": "execute_test", "id": "test_wire.py::test_skip"},
            {"op": "execute_test", "id": "test_wire.py::test_fail"},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    messages = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [message["op"] for message in messages] == [
        "ready",
        "collected",
        "test_result",
        "test_result",
        "test_result",
        "bye",
    ]
    assert [(m["id"], m["status"]) for m in messages if m["op"] == "test_result"] == [
        ("test_wire.py::test_pass", "passed"),
        ("test_wire.py::test_skip", "skipped"),
        ("test_wire.py::test_fail", "failed"),
    ]


def test_executing_an_unknown_id_is_protocol_fatal(tmp_path: Path) -> None:
    """``WorkerRequest::ExecuteTest``: *"a protocol error response, not a silent skip"*.

    The orchestrator routes an execute back to the worker that collected the file, so an
    unknown id is **routing drift**.  ``WorkerResponse`` has no error variant for the execute
    op and the two candidate answers are not equivalent: replying ``status:"error"`` would
    put a test that was never run into the report and let a whole worker's worth of
    mis-routed ids look like ordinary failures.  So it takes the unknown-op path — stderr,
    exit 2, and **nothing on stdout beyond what was already answered**.
    """
    target = write(tmp_path / "test_known.py", "def test_known():\n    assert True\n")
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_known.py::test_ghost"},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 2
    assert "test_known.py::test_ghost" in proc.stderr
    assert [json.loads(line)["op"] for line in proc.stdout.splitlines()] == ["ready", "collected"]


def test_execute_test_without_an_id_is_protocol_fatal(tmp_path: Path) -> None:
    """The same rule as ``collect_file`` without a path: a malformed request is drift."""
    proc = _run_worker([_init_line(tmp_path), {"op": "execute_test"}])

    assert proc.returncode == 2
    assert "without an id" in proc.stderr


def test_execute_test_raises_unknown_test_error_in_process(tmp_path: Path) -> None:
    """The in-process half of the same rule, so the type is pinned and not just the exit code."""
    del tmp_path
    with isolated_worker_state():
        with pytest.raises(UnknownTestError) as excinfo:
            _ = execute_test("nowhere.py::test_ghost")
    assert "nowhere.py::test_ghost" in str(excinfo.value)


def test_a_print_at_execution_never_reaches_the_protocol_stream(tmp_path: Path) -> None:
    """stdout is reserved for JSON lines; a chatty test must not corrupt the framing.

    Two layers do this and both are asserted at once: ``main`` rebinds ``sys.stdout`` to
    stderr before any test module is imported, and the per-test capture redirects it again.
    """
    target = write(
        tmp_path / "test_chatty.py",
        """
        print("at import time")


        def test_chatty():
            print("during the test")
            assert True
        """,
    )
    proc = _run_worker(
        [
            _init_line(tmp_path),
            {"op": "collect_file", "path": target.as_posix()},
            {"op": "execute_test", "id": "test_chatty.py::test_chatty"},
            {"op": "shutdown"},
        ]
    )

    assert proc.returncode == 0, proc.stderr
    for line in proc.stdout.splitlines():
        _ = json.loads(line)  # every line is a message, not a stray print
    result = json.loads(proc.stdout.splitlines()[2])
    assert result["stdout"] == "during the test\n"
    assert "at import time" in proc.stderr
