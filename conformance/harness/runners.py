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

from .ids import normalize_pytest_nodeid, normalize_rustest_id

_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors)")

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
    passed: int
    failed: int
    skipped: int
    errors: int
    exit_code: int
    collection_error: bool


@dataclass(frozen=True)
class RunResult:
    ids: set[str]
    outcomes: Outcomes


@dataclass(frozen=True)
class CollectResult:
    """What a *collection-only* run produces, and nothing more.

    Deliberately not a ``RunResult``: a collect-only surface has no pass/fail/skip
    counts and no collection-error flag distinct from its exit code. Reusing
    ``RunResult`` would force zeros into those fields, and every case would then
    silently "agree" on execution neither side performed. The v2-collect gate grades
    exactly these two things -- the ids on stdout and the process exit code.

    ``ids`` is an ordered ``list``, not a ``set``. Collection **order** is part of what
    v2 reproduces -- Task 3's name-sorted interleaved walk descends a directory at the
    position its own name sorts to, which a set comparison cannot see at all -- and so
    is **cardinality**: a duplicated id collapses into a set silently, while an ordered
    list surfaces it as a plain length/position mismatch.
    """

    ids: list[str]
    exit_code: int


# The config filenames pytest's rootdir search recognizes, in its own precedence order
# (`_pytest/config/findpaths.py::locate_config`), mirrored by `src/v2/config.rs`. Used
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
    mirrored by ``src/v2/config.rs::load_config_dict_from_file`` (lines 673-730):

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
    in collection order, which the v2-collect gate compares directly; callers that
    only care about membership (``run_pytest``, the v1 gate) wrap this in a ``set``.
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
    """Extract pass/fail/skip/error counts from a pytest terminal summary line."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
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


def run_pytest(case_dir: Path, args: list[str]) -> RunResult:
    """Collect and run *case_dir* with real pytest, returning normalized results.

    The case runs under pure pytest defaults: an empty ini file is forced with
    ``-c`` and the rootdir is pinned to *case_dir*, so pytest never walks up and
    adopts a surrounding project's ``[tool.pytest.ini_options]`` (this repo's own
    ``pyproject.toml`` would otherwise apply to every corpus case).

    *case_dir* is resolved first: ``--rootdir`` is interpreted relative to
    pytest's own cwd, so a relative case directory would point pytest at a
    nonexistent rootdir and abort with a usage error (exit 4).
    """
    case_dir = case_dir.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        empty_ini = Path(tmp) / "pytest.ini"
        empty_ini.write_text("[pytest]\n", encoding="utf-8")
        base = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(empty_ini),
            f"--rootdir={case_dir}",
        ]
        collect = _run([*base, "--collect-only", "-q", *args], case_dir)
        _check_pytest_exit(collect, "collect")
        raw_ids = parse_pytest_collect(collect.stdout)
        run = _run([*base, "-q", "--tb=no", *args], case_dir)
        _check_pytest_exit(run, "run")
    outcomes = parse_pytest_summary(run.stdout, run.returncode)
    return RunResult(
        ids={normalize_pytest_nodeid(i) for i in raw_ids},
        outcomes=outcomes,
    )


def _check_pytest_collect_exit(proc: subprocess.CompletedProcess[str]) -> None:
    """Raise on a pytest *collect* fault, leaving real collection outcomes alone.

    Under ``--collect-only`` the gradeable codes are 0 (collected), 2 (a file failed
    to import) and 5 (nothing collected -- an empty tree, or everything deselected by
    ``-m``). All three are codes the ``--v2-collect-only`` surface also produces, so
    the grader must see them. Codes 3 (internal error) and 4 (usage error) mean pytest
    never collected at all; parsing zero ids out of that would fabricate a divergence
    with no explanation, so they route to ``_grade_one_collect``'s harness-error
    channel. Exit 1 cannot occur here -- no test is ever run.
    """
    if proc.returncode >= 3 and proc.returncode != 5:
        raise RuntimeError(f"pytest collect failed (exit {proc.returncode}): {proc.stderr[-500:]}")


def _isolate_case(case_dir: Path, dest_parent: Path) -> Path:
    """Copy *case_dir* under *dest_parent* as a self-contained, config-pinned tree.

    This is the whole v2-collect comparison protocol. ``run_pytest`` (v1 mode) pins
    rootdir with ``-c <empty ini>`` and ``--rootdir=<case dir>``; the
    ``--v2-collect-only`` surface has neither flag in 1b.1 and resolves config by
    walking *up* from its cwd. Run in place, the two therefore disagree about rootdir
    for every in-repo case -- this repo's own ``pyproject.toml`` carries
    ``[tool.pytest.ini_options]``, so v2's ids would read ``conformance/corpus/...``
    while pytest's read ``test_x.py``, and every case would "diverge" on a prefix
    neither runner is actually getting wrong.

    Copying the case out of the repo removes the disagreement at its source instead of
    papering over it: with a bare ``pytest.ini`` at the copy's root, *both* runners
    resolve the same rootdir by their own unmodified rules, and both emit case-relative
    ids. pytest treats a ``pytest.ini`` as authoritative even when empty
    (``findpaths.py::load_config_dict_from_file``) and so does v2
    (``src/v2/config.rs:684``), so no flags are needed on either side -- which matters,
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
    authority, exactly as it is for ``run_rustest_v2_collect``. That symmetry is the
    point -- any flag used here and unavailable there would make the comparison a
    comparison of harness invocations rather than of runners.

    Ids are taken verbatim and **in order**, without ``normalize_pytest_nodeid``: the
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


def run_rustest_v2_collect(case_dir: Path, args: list[str]) -> CollectResult:
    """Collect *case_dir* with ``rustest --v2-collect-only`` in an isolated copy.

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
        proc = _run([sys.executable, "-m", "rustest", "--v2-collect-only", *args], work)
    return CollectResult(ids=proc.stdout.splitlines(), exit_code=proc.returncode)


def run_rustest(case_dir: Path, args: list[str]) -> RunResult:
    """Run *case_dir* with real rustest, returning normalized results.

    *case_dir* is resolved so the subprocess cwd and the ID normalization base
    are absolute, matching ``run_pytest``.

    A missing report file means rustest died before it could write one (bad
    argv, crash, import-time abort). That is a harness fault, not a case
    outcome, so it raises rather than returning a fabricated all-zeros result
    that would silently grade as a divergence with no explanation.
    """
    case_dir = case_dir.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        # TODO(phase1): --pytest-compat is deleted in v2 (compat-by-default); update this invocation.
        cmd = [
            sys.executable,
            "-m",
            "rustest",
            ".",
            "--pytest-compat",
            "--color",
            "never",
            "--report-json",
            str(report_path),
            *args,
        ]
        proc = _run(cmd, case_dir)
        if not report_path.exists():
            raise RuntimeError(
                f"rustest wrote no report (exit {proc.returncode}): {proc.stderr[-500:]}"
            )
        data: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    summary: dict[str, int] = data["summary"]
    tests: list[dict[str, Any]] = data["tests"]
    return RunResult(
        ids={normalize_rustest_id(str(test["id"]), case_dir) for test in tests},
        outcomes=Outcomes(
            passed=summary["passed"],
            failed=summary["failed"],
            skipped=summary["skipped"],
            errors=sum(1 for test in tests if test.get("status") == "error"),
            exit_code=proc.returncode,
            collection_error=bool(data["collection_errors"]),
        ),
    )
