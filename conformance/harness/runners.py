"""Run pytest and rustest as subprocesses over a corpus case directory."""

from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

# The summary-line tokens the harness counts. `xfailed`/`xpassed` are listed even
# though they end in `failed`/`passed`: the alternation is only ever tried at the
# character right after `<digits> `, so "1 xfailed" cannot match `failed` (that would
# need the token to start mid-word) and the two Xs get buckets of their own. Before
# they were here the tokens matched *nothing at all* and an expected failure was
# tallied as zero of everything -- see the `marks/xfail` waiver, which recorded pytest
# as 1/0/0/0 for a `1 passed, 1 xfailed` run.
#
# `deselected` is not an outcome -- no test ran -- but it is graded, and it is the ONLY
# thing that can be. An id missing from the graded list because `-m` deselected it is
# otherwise indistinguishable from one missing because it was never collected at all:
# same ids, same buckets, same exit code. See `grade_run_case`.
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed|deselected)")

# pytest's exit code for "collection raised, so the run loop never started"
# (`_pytest/main.py::pytest_runtestloop` raises `Interrupted` before the first item).
# Named because `run_pytest_full` keys the one rule it owns on this value -- read off the
# COLLECT pass, never the run pass; see that function.
_PYTEST_EXIT_INTERRUPTED = 2

# A pytest nodeid: a path segment, one or more "::"-separated name segments, and
# an optional trailing "[...]" parametrize suffix (which may itself contain
# colons or nested brackets, e.g. "test_x.py::test_f[a:b]" or "[a[b]]"). No
# segment before the trailing suffix may contain a bare colon, and the whole
# thing must start at column 0 -- both are true of every real nodeid pytest
# prints and false of most non-nodeid lines (traceback frames, source excerpts,
# "E   ..." assertion text are indented, and most contain a single ":" ahead of
# any incidental "::", e.g. "AssertionError: ...::..."). This is a per-line
# filter, not a complete guard on its own: an "E   ..." line that verbatim
# echoes offending source containing a slice (e.g. "E       x = data[::2") has
# no such preceding colon and DOES match this shape despite sitting at column
# 0. See parse_pytest_collect, which stops scanning before any such line is
# ever reached.
_NODEID_RE = re.compile(r"^[^\s:][^:\n]*(::[^\s:][^:\n]*)+(\[[^\n]*\])?$")

# A pytest -q --collect-only report is structurally two parts: every collected
# nodeid, printed contiguously first, followed -- only if collection hit
# trouble -- by an "=== TITLE ===" section header (ERRORS, short test summary
# info, warnings summary) and its body. Verified against real pytest (see the
# probe in the Task 7 report): even when the broken file sorts alphabetically
# *before* a valid sibling, pytest still front-loads every successfully
# collected nodeid from every file before any error block -- collection
# errors never interleave with the nodeid list. So stopping at the first
# boundary line loses no real ids and is a structural guard against any
# "E   ..." echoed-source line, not just the ones _NODEID_RE's shape happens
# to reject. Two boundary shapes are recognized: the "=== TITLE ==="
# section-header line itself (what actually fires in every real case probed),
# and a bare "E " prefix as a defensive backstop in case an "E   ..." line
# were ever reached without a preceding recognized header.
_SECTION_BOUNDARY_RE = re.compile(r"^=+ .+ =+$")


@dataclass(frozen=True)
class Outcomes:
    """Everything :func:`parse_pytest_summary` can read off a pytest summary line.

    A *parse* result, not a graded contract: the run gate reshapes it into
    :class:`RunOutcomes` (the seven counts it actually diffs) and drops ``exit_code`` and
    ``collection_error``, which belong to :class:`FullRunResult`.

    The three defaulted fields are defaulted for a history reason worth keeping: they were
    appended rather than slotted in beside ``skipped`` so the v1 gate's positional
    construction kept working. That gate is gone (Phase 4 Task 2), the defaults are now
    merely harmless, and every caller passes all nine.
    """

    passed: int
    failed: int
    skipped: int
    errors: int
    exit_code: int
    collection_error: bool
    xfailed: int = 0
    xpassed: int = 0
    deselected: int = 0


@dataclass(frozen=True)
class RunOutcomes:
    """The seven graded counts schema v2 carries -- the tally half of the run gate.

    Field order mirrors the schema-v2 ``summary`` object's own field order, so the
    legend printed on a divergence reads like the report it came from.

    Deliberately its own type rather than a reuse of :class:`Outcomes`. Six *outcome*
    buckets is the point: schema v1's three statuses had nowhere to put an X, which is
    exactly how ``marks/xfail`` graded as an ordinary failure and ``xpassed`` was
    invisible (Task 4 report §5.1). A tally that silently dropped either would make the
    two cases this gate exists to prove match for the wrong reason.

    ``deselected`` is the seventh and is **not an outcome** -- no test ran -- but it is
    graded, because it is the only thing that can distinguish "this id is absent because
    ``-m`` deselected it" from "this id is absent because it was never collected". Those
    two produce identical ids, identical buckets and an identical exit code. Leaving it
    out was a **false green**: a v2 that lost a deselected sibling entirely graded MATCH.

    No ``exit_code`` and no ``collection_error`` field: the exit code belongs to the
    *run*, not to its tally, and lives on :class:`FullRunResult`; a collection error is
    exit 2 on both sides by construction, so carrying it here would grade the same fact
    twice under two names.
    """

    passed: int
    failed: int
    skipped: int
    xfailed: int
    xpassed: int
    errors: int
    deselected: int


@dataclass(frozen=True)
class FullRunResult:
    """What an *executed* run produces: ordered ids, the graded counts, the exit code.

    ``ids`` is an ordered ``list`` for the same two reasons as
    :attr:`CollectResult.ids` -- execution **order** is observable behaviour (a
    module-scoped fixture tears down when the runner leaves the file) and a duplicated
    id collapses into a set silently.
    """

    ids: list[str]
    outcomes: RunOutcomes
    exit_code: int


@dataclass(frozen=True)
class CollectResult:
    """What a *collection-only* run produces, and nothing more.

    Deliberately not a :class:`FullRunResult`: a collect-only surface has no
    pass/fail/skip counts at all. Reusing the run type would force zeros into those
    fields, and every case would then silently "agree" on execution neither side
    performed. The v2-collect gate grades exactly two things -- the ids on stdout and
    the process exit code.

    ``ids`` is an ordered ``list``, not a ``set``. Collection **order** is part of what
    v2 reproduces -- Task 3's name-sorted interleaved walk descends a directory at the
    position its own name sorts to, which a set comparison cannot see at all -- and so
    is **cardinality**: a duplicated id collapses into a set silently, while an ordered
    list surfaces it as a plain length/position mismatch.
    """

    ids: list[str]
    exit_code: int


# The config filenames pytest's rootdir search recognizes, in its own precedence order
# (`_pytest/config/findpaths.py::locate_config`), mirrored by `src/engine/config.rs`. Used
# only to decide whether an isolated case already carries config of its own -- see
# `_qualifies_as_config`, which decides that on *content*, the way both runners do.
_CASE_CONFIG_NAMES = ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")


def _has_ini_section(path: Path, section: str) -> bool:
    """Does *path* parse as an ini file carrying ``[section]``?

    An unreadable or malformed file answers False rather than raising: the only
    question being asked is "would this file stop the config search", and a file
    neither runner can parse into a pytest section does not.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, configparser.Error):
        return False
    return parser.has_section(section)


def _qualifies_as_config(path: Path) -> bool:
    """Would *path* actually stop pytest's (and v2's) upward config search?

    Existence is not the question -- **content** is, and both runners already agree on
    the rules. Port of ``_pytest/config/findpaths.py::load_config_dict_from_file``,
    mirrored by ``src/engine/config.rs::load_config_dict_from_file`` (lines 673-730):

    * ``.ini`` qualifies with a ``[pytest]`` section; ``pytest.ini`` qualifies by
      *name* even when empty ("pytest.ini files are always the source of
      configuration, even if empty"), while a section-less ``.pytest.ini`` does NOT --
      the name check is keyed on the exact filename.
    * ``.cfg`` (``setup.cfg``) qualifies only with ``[tool:pytest]``.
    * ``.toml`` (``pyproject.toml``) qualifies only with ``[tool.pytest.ini_options]``.

    Getting this wrong is not cosmetic. A case shipping a ``pyproject.toml`` that has
    only ``[project]`` in it does **not** anchor either runner; if the harness took its
    mere existence as "this case brings its own config" and skipped the bare ini, both
    runners would walk up out of the isolated copy and into whatever sits above the
    temp directory. Both would then agree -- on the wrong thing -- and the case would
    record a **vacuous MATCH**.
    """
    if not path.is_file():
        # Checked first because the `pytest.ini` rule below qualifies on *name* alone;
        # without this, a case that ships no config at all would "qualify" on the very
        # file the caller is about to write.
        return False
    suffix = path.suffix
    if suffix == ".ini":
        return path.name == "pytest.ini" or _has_ini_section(path, "pytest")
    if suffix == ".cfg":
        return _has_ini_section(path, "tool:pytest")
    if suffix == ".toml":
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return False
        table: object = data
        for key in ("tool", "pytest", "ini_options"):
            if not isinstance(table, dict):
                return False
            table = cast(dict[str, object], table).get(key)
        return table is not None
    return False


def parse_pytest_collect(text: str) -> list[str]:
    """Extract nodeids from ``pytest --collect-only -q`` output.

    Stops at the first section-boundary line (an "=== TITLE ===" header, or a
    defensive fallback on a bare "E " prefix) -- see _SECTION_BOUNDARY_RE --
    since real nodeids are only ever printed before any such boundary.
    Matched against the *raw* line (only trailing whitespace stripped): real
    nodeids are always flush at column 0, while every other pre-boundary line
    is indented by pytest. Stripping leading whitespace before matching (the
    previous heuristic did) throws that signal away and lets an indented line
    that happens to contain a literal "::" -- e.g. a quoted path or
    Rust-style module reference inside an assertion message -- read as a
    phantom test id.

    The boundary check is the load-bearing guard: an "E   ..." line that
    echoes a SyntaxError's offending source verbatim can contain a slice
    (e.g. "E       x = data[::2") and would otherwise match _NODEID_RE's
    per-line shape despite sitting at column 0, since a slice's "::" has no
    preceding bare colon to disqualify it.

    Ids are returned **in emission order**, with duplicates kept. pytest prints them
    in collection order, which both gates compare directly -- neither sorts, de-duplicates
    nor sets them, because order and cardinality are part of what is being graded.
    """
    ids: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _SECTION_BOUNDARY_RE.match(line) or line.startswith("E "):
            break
        if _NODEID_RE.match(line):
            ids.append(line)
    return ids


def parse_pytest_summary(text: str, exit_code: int) -> Outcomes:
    """Extract the outcome tally from a pytest terminal summary line.

    Six buckets, because pytest reports six: ``xfailed`` and ``xpassed`` are their own
    categories and folding either into ``skipped``/``passed`` is precisely what made an
    X invisible under schema v1. ``deselected`` rides along as a seventh count -- not an
    outcome, but the only evidence that an absent id was *chosen* against rather than
    never seen. The v1 gate still grades only the four buckets it can represent (see
    :class:`Outcomes`); the full-run gate grades all seven.
    """
    counts = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "xfailed": 0,
        "xpassed": 0,
        "deselected": 0,
    }
    for line in reversed(text.splitlines()):
        found: dict[str, int] = {}
        for match in _SUMMARY_RE.finditer(line):
            number: str = match.group(1)
            kind: str = match.group(2)
            found[kind] = int(number)
        if found:
            counts["passed"] = found.get("passed", 0)
            counts["failed"] = found.get("failed", 0)
            counts["skipped"] = found.get("skipped", 0)
            counts["xfailed"] = found.get("xfailed", 0)
            counts["xpassed"] = found.get("xpassed", 0)
            counts["deselected"] = found.get("deselected", 0)
            # pytest writes "1 error" but "2 errors"; both mean the same bucket.
            counts["errors"] = found.get("error", 0) + found.get("errors", 0)
            break
    return Outcomes(
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        errors=counts["errors"],
        exit_code=exit_code,
        collection_error=exit_code == 2,
        xfailed=counts["xfailed"],
        xpassed=counts["xpassed"],
        deselected=counts["deselected"],
    )


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)


def _check_pytest_exit(proc: subprocess.CompletedProcess[str], phase: str) -> None:
    """Raise on a pytest *harness* fault, leaving real test outcomes alone.

    pytest exit codes 0 (all passed), 1 (tests failed) and 2 (collection error /
    interrupted) are legitimate case outcomes the corpus grades on. So is 5 (no
    tests collected, e.g. ``-m nosuchmark`` deselecting everything): it is a real,
    comparable outcome under the v2 exit-code contract (spec: "Contracts are
    pytest's: exit codes 0-5"), not a harness fault -- rustest exiting 0 for the
    same invocation is exactly the kind of divergence the corpus exists to catch.
    Codes 3 (internal error) and 4 (usage error, e.g. a bad rootdir) mean pytest
    itself could not do its job, and silently parsing an empty summary out of
    those would fabricate a 0/0/0/0 result. Raising routes them to
    ``_grade_one``'s harness-error channel instead.
    """
    if proc.returncode >= 3 and proc.returncode != 5:
        raise RuntimeError(f"pytest {phase} failed (exit {proc.returncode}): {proc.stderr[-500:]}")


def _check_pytest_collect_exit(proc: subprocess.CompletedProcess[str]) -> None:
    """Raise on a pytest *collect* fault, leaving real collection outcomes alone.

    Under ``--collect-only`` the gradeable codes are 0 (collected), 2 (a file failed
    to import) and 5 (nothing collected -- an empty tree, or everything deselected by
    ``-m``). All three are codes the ``--collect-only`` surface also produces, so
    the grader must see them. Codes 3 (internal error) and 4 (usage error) mean pytest
    never collected at all; parsing zero ids out of that would fabricate a divergence
    with no explanation, so they route to ``_grade_one_collect``'s harness-error
    channel.

    **Exit 1 is reachable here and is deliberately passed through.** An earlier version of
    this docstring claimed it "cannot occur -- no test is ever run", which is false: a
    failed collect report increments ``Session.testsfailed``, and with
    ``--continue-on-collection-errors`` no ``Interrupted`` is raised, so ``wrap_session``
    falls through to ``ExitCode.TESTS_FAILED``. Probed on pytest 8.4.2 with one broken
    import beside one healthy file::

        pytest --collect-only -q                                    -> exit 2
        pytest --collect-only -q --continue-on-collection-errors     -> exit 1

    The guard already lets 1 through, so the behaviour was right and only the reasoning was
    wrong -- which is the more dangerous of the two, because the next person to widen this
    condition would have widened it on a false premise. No corpus case passes that flag
    today; one that did would be graded, and would diverge on the exit code, since v2 has
    no such option.
    """
    if proc.returncode >= 3 and proc.returncode != 5:
        raise RuntimeError(f"pytest collect failed (exit {proc.returncode}): {proc.stderr[-500:]}")


def _isolate_case(case_dir: Path, dest_parent: Path) -> Path:
    """Copy *case_dir* under *dest_parent* as a self-contained, config-pinned tree.

    This is the whole comparison protocol for both gates. pytest can be told where its
    rootdir is (``-c <empty ini>``, ``--rootdir=<case dir>``); the ``rustest`` surfaces have
    neither flag and resolve config by walking *up* from their cwd. Run in place, the two
    therefore disagree about rootdir
    for every in-repo case -- this repo's own ``pyproject.toml`` carries
    ``[tool.pytest.ini_options]``, so v2's ids would read ``conformance/corpus/...``
    while pytest's read ``test_x.py``, and every case would "diverge" on a prefix
    neither runner is actually getting wrong.

    Copying the case out of the repo removes the disagreement at its source instead of
    papering over it: with a bare ``pytest.ini`` at the copy's root, *both* runners
    resolve the same rootdir by their own unmodified rules, and both emit case-relative
    ids. pytest treats a ``pytest.ini`` as authoritative even when empty
    (``findpaths.py::load_config_dict_from_file``) and so does v2
    (``src/engine/config.rs:684``), so no flags are needed on either side -- which matters,
    because the v2 side has none to offer.

    Two details:

    * a case that ships a **qualifying** config file keeps it, and gets no bare ini.
      Qualifying is decided on content by ``_qualifies_as_config``, exactly as both
      runners decide it -- a ``pyproject.toml`` with only ``[project]`` in it anchors
      nothing, so such a case still needs the bare ini or both runners would walk up
      out of the temp tree and agree on the wrong rootdir (a vacuous MATCH). The bare
      ini is a fallback for the unanchored case, not a rewrite of what a case tests.
    * caches are not copied. ``__pycache__`` is corpus litter (checked-in ``.pyc``
      files from earlier runs), and stale bytecode next to freshly copied source is
      exactly the shape of pytest's ``import file mismatch`` error;
      ``.pytest_cache``/``.rustest_cache`` would carry one run's state into the next.
    """
    dest = dest_parent / case_dir.name
    shutil.copytree(
        case_dir,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".rustest_cache"),
    )
    if not any(_qualifies_as_config(dest / name) for name in _CASE_CONFIG_NAMES):
        (dest / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return dest


def run_pytest_collect(case_dir: Path, args: list[str]) -> CollectResult:
    """Collect *case_dir* with real pytest in an isolated copy -- the gate's oracle.

    Invoked with no config flags at all: the isolated copy's own ``pytest.ini`` is the
    authority, exactly as it is for ``run_rustest_collect``. That symmetry is the
    point -- any flag used here and unavailable there would make the comparison a
    comparison of harness invocations rather than of runners.

    Ids are taken verbatim and **in order**, with no normalisation at all: the
    v2 surface's contract is byte-parity with pytest's nodeids in pytest's own
    collection order, so normalizing or sorting either side would hide the precise
    defect this gate exists to catch.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        proc = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "--collect-only",
                "-q",
                *args,
            ],
            work,
        )
        _check_pytest_collect_exit(proc)
    return CollectResult(ids=parse_pytest_collect(proc.stdout), exit_code=proc.returncode)


def run_rustest_collect(case_dir: Path, args: list[str]) -> CollectResult:
    """Collect *case_dir* with ``rustest --collect-only`` in an isolated copy.

    stdout carries node ids and *only* node ids, one per line, in manifest order, so
    it is read with a bare ``splitlines()`` -- no parsing, no filtering, no sorting
    and no de-duplication, all four of which would discard signal. Everything else (the
    ``N tests collected`` summary, ``ERROR collecting <path>`` blocks) goes to stderr
    by design, and stderr is never read: its wording deliberately differs from
    pytest's, so grading anything but ids and the exit code would manufacture
    divergences out of prose.

    No exit-code guard on this side. Every code v2 produces is a graded outcome,
    including 3 (``INTERNALERROR``) and 4 (usage error) -- if v2 crashes where pytest
    collects, that is the divergence, not a harness fault to be raised away.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        proc = _run([sys.executable, "-m", "rustest", "--collect-only", *args], work)
    return CollectResult(ids=proc.stdout.splitlines(), exit_code=proc.returncode)


def run_pytest_full(case_dir: Path, args: list[str]) -> FullRunResult:
    """Collect **and run** *case_dir* with real pytest in an isolated copy -- the run oracle.

    Isolation protocol (a), identical to ``run_pytest_collect``: ``copytree`` minus the
    caches, a content-qualified check for a config file the case ships itself, and a bare
    ``[pytest]`` ini otherwise. Like the collect oracle it is invoked with **no config
    flags at all**, because ``rustest`` has none to offer and a flag used on one side
    only would make the gate a comparison of harness invocations rather than of runners.

    Two invocations in the one isolated tree, because pytest publishes the two halves of
    the graded contract on two different surfaces:

    * ``--collect-only -q`` -> the **ordered node ids of the selected tests**, parsed by
      the same hardened ``parse_pytest_collect`` the collect gate uses. Deselection
      applies here exactly as it does to a run, so ``-m smoke`` shrinks this list.
    * ``-q --tb=no`` -> the summary line (the seven graded counts) and the exit code.

    **The double invocation is an asymmetry with the v2 side, and it is not free.**
    ``run_rustest_run`` runs the case **once**; this runs it twice, and the second
    invocation sees a tree the first one has already touched (``.pyc`` files written
    during collection). Both are pytest, so neither pass is influenced by the other's
    *results*, and ``-p no:cacheprovider`` keeps pytest's own cache out of it -- but a
    case whose behaviour depended on being collected twice, or on filesystem state at
    import time, would be asked a different question here than v2 is asked. No corpus
    case is of that kind. The alternative -- reading ids out of a single ``-v`` run --
    was rejected for the stronger reason below.

    **Why the ids do not come from a ``-v`` run.** ``-v`` prints one line per *report*,
    and pytest emits up to three reports per test: a body that passes with a teardown
    that raises prints the id **twice**, ``PASSED`` then ``ERROR``. The schema-v2 report
    carries one **reduced** status per test (Task 4 report §7.1), so grading pytest's
    reports against v2's tests would compare different units and manufacture an id
    divergence out of a difference that is real only in the *counts*, where this gate
    already reports it.

    **The one rule keyed on an exit code**, and it is pytest's own: a collection error
    means *nothing runs at all* -- ``pytest_runtestloop`` raises ``Interrupted`` before
    the first item -- so the executed-id list is empty however many ids the collect pass
    listed. v2 encodes the identical rule (``src/engine/execute.rs::stage`` returns empty
    dispatch lists when ``errors`` is non-empty; Task 4 report §2.1, correction 2), so
    applying it here compares like with like instead of pitting pytest's *collected* set
    against v2's *executed* one. It is pinned against real pytest from a side effect --
    the healthy test would have written a marker file if it had run -- rather than by
    restating the branch.

    **The rule reads the COLLECT pass's exit code, not the run pass's**, and the
    difference is not cosmetic. Exit 2 is `Interrupted` *or* `Exit`, and
    ``pytest.exit()`` called from inside a test body raises the latter: the run pass
    exits 2 with tests already reported. Probed::

        def test_first():  assert True
        def test_bails():  pytest.exit("stopping here")

        COLLECT pass -> exit 0, 3 ids
        RUN pass     -> exit 2, "1 passed"

    Keying on the run pass would answer "did collection fail?" with a number that also
    means "someone called ``pytest.exit()``", producing an **internally inconsistent
    oracle**: an empty id list beside a tally that says one test passed. Whoever read
    that divergence would be sent looking for a collection bug that does not exist. The
    collect pass is the authority on whether collection failed, and keying on it is
    strictly tighter -- it fires on exactly the cases the rule is about, and the
    ``pytest.exit()`` run then diverges through the tally and the exit code, where it
    belongs.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]
        collect = _run([*base, "--collect-only", "-q", *args], work)
        _check_pytest_collect_exit(collect)
        run = _run([*base, "-q", "--tb=no", *args], work)
        _check_pytest_exit(run, "run")
    outcomes = parse_pytest_summary(run.stdout, run.returncode)
    interrupted = collect.returncode == _PYTEST_EXIT_INTERRUPTED
    ids = [] if interrupted else parse_pytest_collect(collect.stdout)
    return FullRunResult(
        ids=ids,
        outcomes=RunOutcomes(
            passed=outcomes.passed,
            failed=outcomes.failed,
            skipped=outcomes.skipped,
            xfailed=outcomes.xfailed,
            xpassed=outcomes.xpassed,
            errors=outcomes.errors,
            deselected=outcomes.deselected,
        ),
        exit_code=run.returncode,
    )


def run_rustest_run(case_dir: Path, args: list[str]) -> FullRunResult:
    """Run *case_dir* with ``rustest --report-json`` in an isolated copy.

    The report is the whole read surface. stdout (``FAILED <id>`` plus the message) and
    stderr (``ERROR collecting`` blocks, **worker stderr**, the summary line) are never
    parsed: the wording deliberately differs from pytest's, and worker stderr can carry
    legitimate user output on a completely green run because class- and module-scoped
    teardown output is drained at a boundary outside the per-test capture window (Task 3's
    documented divergence, Task 4 report §5). Grading either stream would manufacture
    divergences out of prose.

    Ids are taken **verbatim and in order** from ``tests[]`` -- no normalization, no
    sorting, no de-duplication. Byte-parity with pytest's node ids is the contract, and
    the isolated copy makes both sides case-relative, so any transformation here would
    hide the defect the gate exists to catch.

    The graded exit code is the **process** exit code, not ``report["exit_code"]``. The
    two are pinned to agree by ``python/tests/test_run_cli.py``; if they ever stop
    agreeing, the gate must side with the number CI and users observe rather than with
    v2's claim about it.

    **Collection errors fold into the ``errors`` bucket**, because pytest puts them there:
    a file that fails to import is reported as ``1 error``, the same words a broken fixture
    gets, and that is the number the summary line this gate grades against carries. The
    schema-v2 report deliberately keeps the two apart (``summary.error`` is a bucket over
    ``tests``; ``collection_errors`` is its own list) since a machine reader can afford the
    distinction -- so the harness applies the *same* mapping v2's own terminal line already
    applies (``python/rustest/core.py::_run_summary``, Task 4 report §9.2). Without the
    fold every collection-error case would diverge on a tally difference that is a
    difference of report *schema*, not of behaviour. Nothing is double-counted: a run
    interrupted by a broken import dispatches no tests, so ``summary.error`` is 0.

    A missing report means ``rustest`` died before writing one, and that raises. The
    alternative -- an all-zeros ``FullRunResult`` -- would grade as a **MATCH** against a
    pytest side that is also empty (an empty tree, or everything deselected), certifying a
    run that never happened.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = _isolate_case(case_dir.resolve(), Path(tmp))
        # Beside the copy, never inside it: a file dropped into the case tree is one more
        # thing the runner under test can see, and this one changes size mid-run.
        report_path = Path(tmp) / "report.json"
        proc = _run(
            [sys.executable, "-m", "rustest", "--report-json", str(report_path), *args],
            work,
        )
        if not report_path.exists():
            raise RuntimeError(
                f"rustest wrote no report (exit {proc.returncode}): {proc.stderr[-500:]}"
            )
        data: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    summary: dict[str, int] = data["summary"]
    tests: list[dict[str, Any]] = data["tests"]
    collection_errors: list[dict[str, Any]] = data["collection_errors"]
    return FullRunResult(
        ids=[str(test["id"]) for test in tests],
        outcomes=RunOutcomes(
            passed=summary["passed"],
            failed=summary["failed"],
            skipped=summary["skipped"],
            xfailed=summary["xfailed"],
            xpassed=summary["xpassed"],
            errors=summary["error"] + len(collection_errors),
            deselected=summary["deselected"],
        ),
        exit_code=proc.returncode,
    )
