"""``--cov``: PEP 669 line measurement in the workers, coverage.py's data format on disk.

The module has two halves that never run in the same process:

* the **worker half** (:class:`LineMonitor`, :func:`start`, :func:`stop_and_write`) registers a
  ``sys.monitoring`` tool, records executed lines for the measured trees, and writes one
  coverage.py data file per worker process;
* the **orchestrator half** (:func:`combine_and_report`) runs in the CLI process afterwards,
  combines those files with ``Coverage.combine`` and renders the requested reports.

They live together because they are two ends of one contract -- the file names one writes are
the file names the other combines -- and neither is imported at all unless ``--cov`` was
passed.

Packaging: coverage.py's data API, not a hand-rolled writer
-----------------------------------------------------------

The plan asked for a cited comparison of "emit a ``.coverage`` file through coverage.py's data
API" against "emit raw lcov", and for a decision. The decision is the former, and ``coverage``
is an **optional extra** (``rustest[cov]``) rather than a dependency.

============================ ==================================== ==============================
                             ``.coverage`` via coverage.py's API  hand-written lcov
============================ ==================================== ==============================
consumers                    ``coverage report/html/xml/json/     genhtml, Codecov, Coveralls,
                             lcov/annotate``, ``coverage          SonarQube -- and *nothing*
                             combine``, diff-cover, and every     that reads ``.coverage``
                             tool that reads the sqlite schema
``--cov-report=xml``         ``Coverage.xml_report`` -- the       a second hand-written
                             Cobertura writer every CI already    Cobertura emitter, i.e. a
                             consumes                             second thing to get wrong
statement set / exclusions   coverage.py's parser decides which   we would have to re-implement
                             lines are *statements* and applies   ``# pragma: no cover``, the
                             ``exclude_lines``, ``[tool.coverage  ``exclude_also`` regexes and
                             .report]``, ``# pragma: no cover``   the multi-line statement map
merging N workers            ``coverage combine`` -- the same     hand-written union, with our
                             code path ``coverage run -p`` uses   own file-identity rules
new dependency               ``coverage`` (optional extra)        none
============================ ==================================== ==============================

lcov's only advantage is having no dependency, and it buys that by re-implementing the two
things that are genuinely hard about coverage reporting -- deciding what counts as a
*statement*, and applying exclusion rules -- in a second place, where they would be free to
disagree with the tool users check the answer against. The plan's own preference ("prefer the
former for ecosystem compat") is therefore also the correctness-preserving choice, and the
extra keeps the cost off everyone who does not ask for coverage.

The same argument is why this module uses coverage.py's :mod:`coverage.files` for path
canonicalisation and tree matching rather than a local prefix test: a data file whose names
are not byte-identical to coverage.py's would combine into *two* entries for one file, and a
source-tree test that differed on symlinks or Windows path case would measure a different set
of files than the tool the result is compared against. rustest supplies the **measurement**;
coverage.py supplies the file semantics and the format.

What is measured, and when
--------------------------

Measurement starts in :func:`rustest._worker.handle_init`, i.e. **before the first
``collect_file``**, because a module's import-time lines are lines coverage.py counts:
``coverage run -m pytest`` starts before pytest imports anything, and pytest-cov starts in
``pytest_load_initial_conftests``. A worker that started measuring at execution time would miss
every module-level line in the suite.

It stops in :func:`rustest._worker.main`'s ``finally``, which is after
``drain_at_shutdown`` -- so session- and module-scoped fixture *teardowns* are measured, as
they are under pytest.

Branch coverage is **not implemented** (see :func:`branch_refusal`).
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from types import CodeType
    from typing import Any, TextIO


#: The name this worker registers its ``sys.monitoring`` tool under.
#:
#: Visible in ``sys.monitoring.get_tool(id)``, which is what a second tool in the same process
#: sees when it collides with us -- so it says *rustest*, not "coverage.py".
TOOL_NAME: Final = "rustest --cov"

#: The tool ids PEP 669 makes available, tried in order: ``COVERAGE_ID`` (1) first because
#: that is what the id is *for*, then every id up to 5.
#:
#: The search is coverage.py's own, bounds included (`coverage/sysmon.py::SysMonitor.start`,
#: l. 245-253 — ``self.myid = sys_monitoring.COVERAGE_ID`` then ``while self.myid <= 5``, under
#: the comment "There's no guarantee that 'our' tool id will still be available, so we have to
#: search for a usable one in start() anyway").
#:
#: The case it covers is an **in-process squatter**: any tool already holding the id in the
#: interpreter this monitor starts in — a debugger, a profiler, another coverage tool, or an
#: embedded/driver use of :class:`LineMonitor` in a process that already had one. It is *not*
#: ``coverage run -m rustest --cov=...``: that measures the CLI process, and workers are
#: spawned subprocesses which inherit no tracer, so nothing collides there.
#: ``test_a_taken_tool_id_is_stepped_over`` builds the honest case — it takes the id in the
#: test process and asserts the monitor steps past it.
#:
#: Derived from the constant rather than written as a literal, because ``COVERAGE_ID`` is **1**
#: — not 3, which is the number the "reserved ids" table invites you to assume.
_TOOL_IDS: Final = tuple(range(sys.monitoring.COVERAGE_ID, 6))

#: The ``co_name`` of the PEP 649 code objects that hold a module's or class's annotations.
#:
#: Skipped for the reason coverage.py skips them (`coverage/sysmon.py` l. 323-325, "Type
#: annotation code objects don't execute, ignore them"): on 3.14 they exist for every annotated
#: definition and run only if something reads ``__annotations__``, so measuring them would add
#: lines to rustest's data that coverage.py's data does not have -- a differential failure with
#: no user-visible meaning.
_ANNOTATE: Final = "__annotate__"


class CoverageUnavailableError(ValueError):
    """``--cov`` was asked for and ``coverage`` is not installed.

    A **`ValueError`**, which is not decoration: `rustest.core.run` classifies every
    `ValueError` out of the coverage set-up as pytest's usage error (exit 4), and that is
    exactly what a missing optional extra is. A bare `Exception` here would escape that arm
    and reach the user as a traceback out of the CLI.
    """


def require_coverage() -> None:
    """Fail loudly, once, in the CLI process if the optional extra is missing.

    Called **before** any worker is spawned, so a missing extra costs one clear line instead of
    N identical tracebacks from N workers that already imported the suite.
    """
    try:
        import coverage  # noqa: F401  # pyright: ignore[reportUnusedImport]
    except ImportError as exc:  # pragma: no cover - exercised by the packaging gate, not here
        raise CoverageUnavailableError(
            "--cov needs coverage.py, which is an optional extra: install it with"
            + " `pip install 'rustest[cov]'` (or `pip install coverage`)."
        ) from exc


def branch_refusal(asked_by: str = "--cov-branch") -> str:
    """The message a request for branch coverage gets, as a usage error.

    Branch coverage is **deferred, not forgotten**, and the refusal is loud rather than a
    silent downgrade to line coverage: a run that quietly measured lines when the user asked
    for branches would report a *higher* number than the truth, against a threshold the user
    set for branches. That is the one failure mode a coverage tool must not have -- and it is
    not hypothetical: on a fixture where a real branch run reports **78 %**, the silently
    degraded line report reads **86 %**.

    *asked_by* names where the request came from, because there are two doors into it and the
    fix differs: the ``--cov-branch`` flag, and ``branch = True`` in the coverage configuration
    (`.coveragerc`, `setup.cfg`, `tox.ini`, or ``[tool.coverage.run]`` in `pyproject.toml`).
    The second is the dangerous one -- it is invisible on the command line, and a user who set
    it once a year ago would otherwise get a line number under a branch heading.

    What it would take, so the deferral is a scope decision and not a mystery: PEP 669's
    ``BRANCH_LEFT``/``BRANCH_RIGHT`` events (3.14+ only -- on 3.12/3.13 coverage.py refuses
    ``core=sysmon`` for branches outright, `coverage/core.py` l. 75-76), plus
    ``coverage.bytecode.BranchArcResolver`` to turn instruction offsets into ``(from, to)``
    arcs, plus the multi-line statement map that resolver needs, plus arcs rather than lines
    through the data API. None of it is hard; all of it is a second measurement mode with its
    own differential, and rustest's floor is 3.12.
    """
    remedy = f"Drop {asked_by}" if asked_by.startswith("-") else "Remove it"
    return (
        f"{asked_by} asks for branch coverage, which rustest does not implement:"
        + " it measures line coverage only. Branch coverage needs PEP 669"
        + " BRANCH_LEFT/BRANCH_RIGHT events, which exist on 3.14+ only, plus arc resolution --"
        + " it is deferred rather than approximated, because reporting line coverage against a"
        + " branch threshold would overstate it."
        + f" {remedy}, or use `coverage run --branch -m rustest` for branch data."
    )


def config_requests_branch() -> bool:
    """Whether the resolved coverage configuration turns branch measurement on.

    Read from the **same** files and in the same order the report will read them, by asking
    coverage.py rather than parsing anything here: ``Coverage.__init__`` resolves
    `.coveragerc` / `setup.cfg` / `tox.ini` / `pyproject.toml` (and ``COVERAGE_RCFILE``) into
    ``self.config``, so this is the value that would otherwise reach the reporter.

    Constructed **without** a ``branch=`` argument on purpose: a constructor argument overrides
    the file, so passing ``branch=False`` here -- which is exactly what
    :func:`combine_and_report` must do -- would answer `False` for every project and detect
    nothing. The two calls are therefore deliberately different, and this one is the probe.
    """
    import coverage

    return bool(coverage.Coverage(data_file=None).config.branch)


class LineMonitor:
    """Executed lines for the measured trees, collected with ``sys.monitoring``.

    The shape is coverage.py's ``SysMonitor`` (`coverage/sysmon.py`) reduced to line coverage,
    and the two callbacks are the reason the cost is what it is:

    * ``PY_START`` is registered **globally** and answers ``DISABLE`` every time
      (l. 395), so each code object costs exactly one callback for the whole run. When the
      code's file is measured, the handler first arms ``LINE`` on that code object alone with
      ``set_local_events`` (l. 387) -- so unmeasured code never produces a line event at all.
    * ``LINE`` records the line and answers ``DISABLE`` (l. 429), which retires that location
      permanently. A line in a loop costs one callback, not one per iteration.

    The result is bounded by *distinct code objects executed* plus *distinct lines executed in
    the measured trees* -- not by how often either runs.
    """

    def __init__(self, sources: Sequence[str], data_dir: str) -> None:
        super().__init__()
        #: Absolute source trees, in the order they arrived on the wire.
        self._sources: tuple[str, ...] = tuple(sources)
        self._data_dir: str = data_dir
        #: The tool id actually acquired, or `None` while stopped.
        self._tool_id: int | None = None
        #: Canonical file name -> executed line numbers.
        self._lines: dict[str, set[int]] = {}
        #: ``code.co_filename`` -> the canonical name to record under, or `None` for "not
        #: measured".  coverage.py's ``should_trace_cache`` (`inorout.py` l. 337-346): the
        #: decision is per *file*, made once, and every later code object in that file reads it.
        self._verdicts: dict[str, str | None] = {}
        #: Set by :meth:`start`; the matchers are coverage.py's own.
        self._in_source: Any = None
        self._third_party: Any = None
        self._source_in_third: Any = None
        #: The data file :meth:`write` produced, for tests and diagnostics.
        self.data_file: str | None = None

    @property
    def lines(self) -> Mapping[str, set[int]]:
        """Canonical file name -> executed lines, as measured so far.

        Read-only by convention and exposed for tests: the line *set* is the thing the
        differential compares, and going through the written data file to see it would mean a
        sqlite round trip in every unit test that asks a question about the callbacks.
        """
        return self._lines

    @property
    def tool_id(self) -> int | None:
        """The ``sys.monitoring`` tool id in use, or `None` when stopped.

        Exposed because "which id did it take" is the one observable that distinguishes
        "stepped over a tool that was already there" from "failed to start"
        (:meth:`start` raises for the latter).
        """
        return self._tool_id

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Acquire a tool id, build the matchers, and turn ``PY_START`` on.

        Raises :class:`RuntimeError` when every tool id is taken -- coverage.py's own wording
        and its own failure mode (`sysmon.py` l. 253). A worker that silently ran unmeasured
        would report a green 0 %.
        """
        from coverage.files import TreeMatcher, canonical_filename
        from coverage.inorout import _add_third_party_paths  # pyright: ignore[reportPrivateUsage]

        # coverage.py's `InOrOut.__init__` (l. 219-226): source dirs are canonicalised, and a
        # `source` entry that is not a directory becomes a source *package* instead.  rustest
        # resolves `--cov=NAME` to a path before the wire, so only the directory arm exists
        # here; a non-directory is refused by the CLI with a message that says so.
        source_dirs = [canonical_filename(source) for source in self._sources]
        self._in_source = TreeMatcher(source_dirs, "source")

        # `inorout.py::set_matchers_depending_on_syspath` (l. 296-341), minus the re-run.
        # coverage.py rebuilds these whenever `sys.path` changes, because a test runner can add
        # an entry mid-run; rustest computes them once at worker start.  The difference is
        # reachable only when a *test root added during the run is itself a virtualenv*, and
        # the alternative is re-walking every `sys.path` entry each time a new file is seen.
        third_paths: set[str] = set()
        _add_third_party_paths(third_paths)
        self._third_party = TreeMatcher(third_paths, "third")
        self._source_in_third = TreeMatcher(
            [src for src in source_dirs if self._third_party.match(src)],
            "source_in_third",
        )

        monitoring = sys.monitoring
        for candidate in _TOOL_IDS:
            try:
                monitoring.use_tool_id(candidate, TOOL_NAME)
            except ValueError:
                continue
            self._tool_id = candidate
            break
        else:
            raise RuntimeError(
                "no sys.monitoring tool id is available for --cov"
                + f" (tried {', '.join(str(i) for i in _TOOL_IDS)}):"
                + " another coverage or profiling tool is already running in this process"
            )

        events = monitoring.events
        monitoring.register_callback(self._tool_id, events.PY_START, self._on_start)
        monitoring.register_callback(self._tool_id, events.LINE, self._on_line)
        monitoring.set_events(self._tool_id, events.PY_START)
        # `restart_events` re-enables locations a previous tool run left disabled
        # (`sysmon.py` l. 267).  Harmless on a fresh interpreter and load-bearing under any
        # embedding that measured something earlier in the same process.
        monitoring.restart_events()

    def stop(self) -> None:
        """Turn every event off and give the tool id back.  Idempotent."""
        if self._tool_id is None:
            return
        monitoring = sys.monitoring
        tool_id, self._tool_id = self._tool_id, None
        monitoring.set_events(tool_id, 0)
        monitoring.free_tool_id(tool_id)

    def write(self) -> str | None:
        """Write this worker's lines as a coverage.py parallel-mode data file.

        The name is ``<data_dir>/.coverage.<host>.pid<pid>.<random>``: ``suffix=True`` is
        coverage.py's own parallel naming (`coverage/data.py::filename_suffix`), which is what
        lets N workers write into one directory without a lock and what
        ``Coverage.combine`` looks for. Returns the file written.

        A worker that measured **nothing** still writes a file. That is deliberate: an absent
        file and an empty one are the same to ``combine``, but only the empty one distinguishes
        "this worker ran and touched no measured code" from "this worker died before it could
        write", which is the difference between a correct 0 % and a silently short report.
        """
        from coverage import CoverageData

        data = CoverageData(basename=os.path.join(self._data_dir, ".coverage"), suffix=True)
        data.add_lines({name: sorted(lines) for name, lines in self._lines.items()})
        data.write()
        self.data_file = data.data_filename()
        return self.data_file

    # -- the callbacks -----------------------------------------------------

    def _on_start(self, code: CodeType, instruction_offset: int) -> Any:
        """``PY_START``: decide the file once, arm ``LINE`` locally, and never fire again."""
        monitoring = sys.monitoring
        if code.co_name == _ANNOTATE:
            return monitoring.DISABLE
        filename = code.co_filename
        try:
            canonical = self._verdicts[filename]
        except KeyError:
            canonical = self._verdicts[filename] = self._verdict(filename)
        if canonical is not None:
            self._lines.setdefault(canonical, set())
            # Recording the canonical name on the code object's own decision would need a
            # second dict keyed by `id(code)`; keying the line callback off `co_filename`
            # through the same verdict cache costs one dict lookup and cannot go stale.
            if self._tool_id is not None:
                monitoring.set_local_events(self._tool_id, code, monitoring.events.LINE)
        return monitoring.DISABLE

    def _on_line(self, code: CodeType, line_number: int) -> Any:
        """``LINE``: record it, then retire this location for the rest of the run."""
        canonical = self._verdicts.get(code.co_filename)
        if canonical is not None:
            self._lines[canonical].add(line_number)
        return sys.monitoring.DISABLE

    def _verdict(self, filename: str) -> str | None:
        """The canonical name to record *filename* under, or `None` to ignore it.

        coverage.py's ``should_trace`` + ``check_include_omit_etc`` (`inorout.py` l. 343-500)
        reduced to the ``source``-is-set branch, which is the only branch rustest can be in:

        * an **empty** ``co_filename`` "isn't a file name" (l. 386-388);
        * ``memory:`` "isn't traceable" (l. 390-391);
        * ``<string>``/``<frozen ...>`` and friends are never real files (l. 393-397, "Lots of
          non-file execution is represented with artificial file names");
        * a file outside every source tree is "falls outside the --source spec" (l. 471);
        * a file *inside* a source tree that is also a third-party install location is
          "inside --source, but is third-party" (l. 472-473) -- which is what keeps a bare
          ``--cov`` at a repo root from measuring the ``.venv/`` inside it.

        **Not ported:** coverage.py's ``should_trace`` prefers the running frame's ``__file__``
        to ``co_filename`` and maps ``.pyc`` to ``.py`` through ``source_for_file``
        (`inorout.py` l. 368-384; its own sysmon tracer reaches the frame with
        ``inspect.currentframe().f_back``, `sysmon.py` l. 339-345). rustest decides on
        ``co_filename`` alone. The two differ only for a ``.pyc`` whose *compile-time* path is
        not where it now lives -- an installed or relocated bytecode file, or a sourceless one
        -- where rustest would judge the stale name and, typically, decide it falls outside the
        source tree: an **under**-report, never an over-report. Recorded in the Task 3 report's
        accepted-difference table rather than fixed, because the frame walk is a per-code-object
        cost paid to serve a case that source-present imports never reach.

        **The empty-string guard is not defensive tidying**, and it is why the whole ladder is
        ported rather than just its first rung. ``exec(compile(src, "", "exec"))`` -- the shape
        ``pytest.importorskip`` uses (`_pytest/outcomes.py` l. 256), and rustest's own compat
        shim with it (`rustest/compat/pytest.py` l. 979) -- produces code objects whose
        ``co_filename`` is ``""``. ``canonical_filename("")`` is ``abspath("")``, i.e. **the
        current directory**, so under a bare ``--cov`` the *directory* matches the source tree
        and is recorded as a measured file. The consequence is not a stray row: the report dies
        with ``No source for code: '<dir>': [Errno 13] Permission denied`` and the written
        ``.coverage`` stays poisoned for every later ``coverage report``/``html``. Reproduced
        before the fix and pinned by
        ``test_code_compiled_with_an_empty_filename_is_not_measured``.
        """
        if not filename or filename.startswith(("<", "memory:")):
            return None
        from coverage.files import canonical_filename

        canonical = canonical_filename(filename)
        if not self._in_source.match(canonical):
            return None
        if self._third_party.match(canonical) and not self._source_in_third.match(canonical):
            return None
        return canonical


# ---------------------------------------------------------------------------
# the worker half's module-level handle
# ---------------------------------------------------------------------------

#: This worker's monitor, or `None`.  Module-level so :func:`stop_and_write` is safe to call
#: from an exception handler that has no reference to anything.
_monitor: LineMonitor | None = None


def start(wire: Mapping[str, object]) -> LineMonitor:
    """Start measuring, from the ``coverage`` object on the ``init`` line.

    The wire shape is ``src/engine/protocol.rs::CoverageWire`` and both fields are required there,
    so a missing one is protocol drift rather than a mode: it raises.
    """
    global _monitor
    sources = wire["sources"]
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"coverage.sources must be a non-empty list, got {sources!r}")
    data_dir = wire["data_dir"]
    if not isinstance(data_dir, str) or not data_dir:
        raise ValueError(f"coverage.data_dir must be a non-empty string, got {data_dir!r}")
    entries = cast("list[object]", sources)
    monitor = LineMonitor([str(source) for source in entries], data_dir)
    monitor.start()
    _monitor = monitor
    return monitor


def stop_and_write() -> str | None:
    """Stop measuring and write this worker's data file.  A no-op when never started.

    Idempotent, and called from :func:`rustest._worker.main`'s ``finally`` so that **every**
    exit path writes -- including ``pytest.exit()`` and a ``KeyboardInterrupt`` that propagates
    out of the loop. The orchestrator waits for each worker to exit before it combines
    (`src/engine/collect.rs::shutdown_and_reap` calls ``child.wait()``), so a file written here is
    always on disk in time.
    """
    global _monitor
    monitor, _monitor = _monitor, None
    if monitor is None:
        return None
    monitor.stop()
    return monitor.write()


def is_measuring() -> bool:
    """Whether this process has a monitoring tool registered -- the overhead claim, testable.

    A run without ``--cov`` must answer `False`, and that is a stronger statement than "the
    callbacks are cheap": nothing is registered, so CPython's monitoring machinery is not
    involved in the interpreter loop at all.
    """
    return _monitor is not None


# ---------------------------------------------------------------------------
# the orchestrator half
# ---------------------------------------------------------------------------


def combine_and_report(
    *,
    data_dir: str,
    sources: Sequence[str],
    data_file: str,
    reports: Sequence[tuple[str, str | None]],
    stream: TextIO,
) -> float:
    """Combine the workers' data files, then render *reports*.  Returns the total percentage.

    ``combine`` is coverage.py's own multi-process merge -- the code path ``coverage combine``
    runs after ``coverage run -p`` -- so the merge rules (file identity, line-bit union,
    contexts) are coverage.py's rather than a second implementation here.

    ``data_file`` is where the merged result lands, and it is
    ``<invocation dir>/.coverage`` by default: the same name and place coverage.py itself
    uses, so ``coverage html`` or ``coverage report`` **after** a ``rustest --cov`` run works
    with no arguments. That is the whole ecosystem-compatibility claim, made concrete.

    ``branch=False`` is passed **explicitly**, and it is not a restatement of the default. The
    default is whatever the project's coverage configuration says, and a project with
    ``branch = True`` in it would otherwise get a reporter configured for branches over data
    that has none -- a line percentage printed under a branch setting, higher than the truth.
    :func:`config_requests_branch` refuses that run outright before any worker is spawned; this
    argument is the structural half of the same guarantee, so no path can reach the reporter
    with branches on.
    """
    import coverage

    cov = coverage.Coverage(data_file=data_file, source=list(sources), branch=False)
    # `strict=False`: a pool whose every worker measured nothing still wrote a file, but an
    # aborted run may have written none at all, and "no data" must report 0 % rather than
    # raise on top of whatever already went wrong.
    cov.combine([data_dir], strict=False, keep=False)
    _touch_unexecuted(cov, sources)
    cov.save()

    total = 0.0
    for kind, destination in reports:
        if kind == "term":
            total = cov.report(file=stream)
        elif kind == "xml":
            total = cov.xml_report(outfile=destination or "coverage.xml")
        else:  # pragma: no cover - the CLI rejects anything else before we get here
            raise ValueError(f"unsupported --cov-report kind: {kind!r}")
    return total


def _touch_unexecuted(cov: Any, sources: Sequence[str]) -> None:
    """Add the source trees' never-imported files, so they report 0 % rather than vanish.

    This is ``Coverage._post_save_work`` (`coverage/control.py` l. 938-964, "Touch all the
    files that could have executed, so that we can mark completely un-executed files as 0 %
    covered"), reproduced explicitly because it does **not** run on this path: that method is
    reached from ``get_data`` only ``if self._collector is not None``, i.e. only after an
    in-process measured run. rustest's measurement happened in other processes, so a plain
    ``combine`` + ``report`` would silently omit every module the suite never imported -- and
    a coverage number that ignores the code you forgot to test is the one number a coverage
    tool must not report.

    ``find_python_files`` is coverage.py's own walker, including its rule that a subdirectory
    without ``__init__.py`` is not importable and is pruned (`coverage/files.py` l. 569-581).
    That rule is also what keeps a ``.venv`` inside a bare-``--cov`` source tree out of the
    report, since a virtualenv has no ``__init__.py`` at its root.
    """
    from coverage.files import canonical_filename, find_python_files

    data = cov.get_data()
    # `touch_files` refuses an empty database ("Can't touch files in an empty CoverageData"),
    # which is exactly the run where touching matters most: a suite that imported none of its
    # source. An empty `add_lines` sets the lines-or-arcs flag and adds nothing.
    data.add_lines({})
    paths = [
        canonical_filename(path)
        for source in sources
        for path in find_python_files(source, include_namespace_packages=False)
    ]
    data.touch_files(paths, None)


def parse_report_spec(spec: str) -> tuple[str, str | None]:
    """``--cov-report`` -> ``(kind, destination)``, or raise `ValueError` naming the subset.

    pytest-cov's grammar is ``TYPE`` or ``TYPE:DESTINATION`` (``--cov-report=xml:cov.xml``),
    and this accepts exactly that for the two types rustest implements.

    The refusal names the unsupported type rather than saying "invalid", because every one of
    ``html``/``json``/``lcov``/``annotate``/``term-missing`` is a *real* pytest-cov value that a
    user has in a Makefile, and being told "term-missing is not supported yet" is actionable
    where "invalid --cov-report" is not.
    """
    kind, _, destination = spec.partition(":")
    kind = kind.strip()
    if kind not in ("term", "xml"):
        raise ValueError(
            f"--cov-report={spec!r}: rustest supports `term` and `xml[:PATH]` only"
            + " (html, json, lcov, annotate, term-missing and the skip-covered modifiers are"
            + " not implemented yet). The combined data file is written to .coverage in"
            + " coverage.py's own format, so `coverage html` after the run produces the rest."
        )
    return kind, destination or None


def resolve_sources(raw: Iterable[str], rootdir: str) -> list[str]:
    """``--cov`` values -> absolute source directories, or raise `ValueError`.

    A bare ``--cov`` (no value) arrives as the empty string and resolves to **rootdir**. That is
    a deliberate narrowing of pytest-cov, which resolves a bare ``--cov`` to "everything except
    the stdlib, third-party installs and coverage.py itself"
    (`coverage/inorout.py::check_include_omit_etc`, the ``else`` branch at l. 483-500). The
    narrowing is stated rather than hidden: rustest measures your project, not every editable
    install that happens to be on ``sys.path``. Pass ``--cov=PATH`` for anything outside the
    rootdir.

    A value that is not an existing directory is refused. coverage.py would treat it as a
    source *package* name and import it to find its file (`inorout.py` l. 216-220,
    ``file_and_path_for_module``); rustest does not, because the worker that would have to do
    the importing is a different process from the one that renders the report, and a name that
    resolves differently in the two would measure one tree and report another.
    """
    resolved: list[str] = []
    for value in raw:
        candidate = rootdir if not value else os.path.abspath(value)
        if not os.path.isdir(candidate):
            raise ValueError(
                f"--cov={value!r}: not a directory. rustest's --cov takes a path to a source"
                + " tree (`--cov=src`, `--cov=src/mypkg`) or no value at all, which measures"
                + f" the rootdir ({rootdir}). Package names such as `--cov=mypkg` are not"
                + " resolved."
            )
        if candidate not in resolved:
            resolved.append(candidate)
    return resolved
