"""The v2 collection worker: one file in, collected-test data out.

The Rust orchestrator spawns ``python -m rustest._v2_worker`` and talks to it over
stdin/stdout in JSON lines (one object per line, newline-terminated, flushed).  The
wire contract is frozen in ``src/v2/protocol.rs`` and ``src/v2/manifest.rs``; the
golden strings there are authority and are mirrored byte-for-byte by this module's
tests.  Two rules are easy to violate and impossible to detect downstream:

* **Omission, not emptiness.**  A ``collected`` response carries ``tests`` *or*
  ``error`` — never both keys, never ``"tests":[]``, never ``"error":null``.  The
  Rust decoder tolerates the noisy forms, so only the goldens catch a regression.
* **``ready.protocol_version`` declares what this worker speaks** — it is the
  constant below, never an echo of what ``init`` asked for, or the handshake could
  not detect skew at all.

**pytest is the oracle.**  Every collection rule below cites the installed pytest
source (``.venv/Lib/site-packages/_pytest/``, pytest 8.4.2) that it ports, and the
behaviours that source leaves ambiguous were probed by running pytest itself.  This
module must never ``import pytest``: it installs rustest's compat shim into
``sys.modules`` first (:func:`install_pytest_shim`), so a test module's own
``import pytest`` resolves to ``rustest.compat.pytest`` and its decorators leave the
``__rustest_*`` metadata this module reads.

**The fixture engine** (core 3) is the second half of this module.  Collection now
resolves each test's fixture *closure* — builtins, the conftest chain from rootdir down,
then the module and any test class — because that closure is what expands a
``@pytest.fixture(params=[...])`` fixture into one collected id per parameter.  The same
registry drives setup, scope caching and reverse-order teardown at execute time
(:class:`FixtureRunner`), so what was collected and what runs cannot drift apart.

**The execute half** (core 4) turns a collected id into a ``test_result`` line.  Three rules
carry the whole design: outcomes are classified **by exception type**, never by matching a
message (`_pytest/outcomes.py` defines ``Skipped``/``Failed``/``XFailed`` as classes for
exactly that reason); ``failed`` and ``error`` are separated by the **phase** that raised,
not by the exception; and a ``unittest.TestCase`` runs through ``unittest`` with a real
``TestResult`` whose callbacks are translated back into those same exception types, which is
how pytest itself does it (`_pytest/unittest.py::TestCaseFunction`) and what keeps one
classifier for both worlds.

**What the pytest differential does and does not pin.**  The execute tests compare *status*
per nodeid against real pytest, byte for byte.  They do **not** compare ``message`` text, and
that is a deliberate line: a skip reason of ``"skipped via rustest.skip"`` where pytest writes
``"unconditional skip"`` is a v1 wording inherited through ``decorators.py::skip_decorator``,
not an outcome defect, and pinning prose would make every upstream reword a red gate.  Message
*shape* is pinned where it is load-bearing — ``[XPASS(strict)]``, ``[NOTRUN]``, and the
traceback filtering — because those distinguish one outcome from another.

**Known scope limits of this worker** (each deliberate, none silent):

* *No warning channel.*  pytest reports a class with ``__init__``/``__new__`` via
  ``PytestCollectionWarning`` and still exits 0.  The protocol has no warnings field,
  so such a class is skipped and **no error entry is emitted** — emitting one would
  turn pytest's exit 0 into the orchestrator's exit 2 and diverge from the oracle.
* *No ``xfail_strict`` ini and no ``--runxfail``.*  Neither is on the wire, so ``strict``
  defaults to ``False`` per mark (`_pytest/skipping.py::pytest_addoption`) and xfail is
  always honoured.  A suite setting ``xfail_strict = true`` needs a protocol field.
* *Capture is stream-level, not fd-level.*  ``print`` and anything writing to
  ``sys.stdout``/``sys.stderr`` is captured; a subprocess or a C extension writing to the
  underlying descriptors is not.  Same trade-off as v1's ``capsys``; fd capture is 1c.
* *Boundary-teardown output goes to stderr, not to a test's ``stdout``.*  Class- and
  module-scoped teardown belongs to the tests that just finished; pytest prints it under the
  *previous* test's teardown section because ``runtestprotocol`` is handed ``nextitem``.  The
  execute wire has no lookahead, so :func:`drain_boundaries` runs outside any capture window
  rather than prefixing one test's teardown onto the next test's output.  Per-boundary
  attribution needs a place on the wire — 1c.
* *Traceback filtering reaches only the outermost exception.*  :func:`_format_exception`
  filters the frames of the exception it is given; the sub-tracebacks nested inside a
  ``BaseExceptionGroup`` — which :meth:`FixtureRunner.teardown` raises when several fixtures
  fail at once — are rendered unfiltered, so a runner frame can still appear inside a group's
  sub-traceback.  The status is unaffected; only the message is noisier.
* *No ``pytest_generate_tests`` hook.*  Decorator metadata (function **and** class level)
  and fixture ``params=`` are the only sources of parametrization; a module- or class-level
  ``pytest_generate_tests`` is not called.
* *Async tests sharing a loop run **sequentially**, where rustest v1 batched them.*  v1's
  ``async_executor.py::run_coroutines_parallel`` collects every async test in a loop scope
  wider than ``function`` into one ``asyncio.gather`` (dispatched from
  ``src/execution.rs``, l. 92-131).  That is **not** ported, and the reason is the oracle:
  pytest-asyncio drives each coroutine through ``asyncio.Runner.run``
  (`plugin.py::_synchronize_coroutine` l. 708-723), which runs one coroutine to completion
  and cannot be re-entered — so tests sharing a loop scope never overlap under it.  Probed
  on pytest 8.4.2 + pytest-asyncio 1.2.0 with two tests each awaiting ``asyncio.sleep(0.30)``
  on one session-scoped loop: 0.613 s wall, the second starting 3 ms *after* the first
  finished.  Batching would have been a wall-clock feature bought with a divergence in
  execution order, output interleaving and failure attribution, on the exact axis this phase
  exists to make faithful; a suite that wants the concurrency can still ``gather`` inside one
  test.  The loop-scope machinery batching would need is now here (:meth:`
  FixtureRunner.loop_runner`), so the decision is reversible behind an opt-in if a real suite
  ever pays for it.
* *``asyncio_debug`` is not on the wire.*  pytest-asyncio builds its runner with
  ``Runner(debug=_get_asyncio_debug(config))`` (l. 809-811); this worker always uses the
  default.  Debug mode changes only the loop's own diagnostics — slow-callback warnings,
  coroutine-origin tracking — and no test outcome.
* *``asyncio_mode`` defaults to ``auto``, where pytest-asyncio defaults to ``strict``.*  Both
  modes are implemented faithfully; only the default differs, and only because rustest cannot
  be uninstalled the way a plugin can.  The reasoning is at ``src/v2/config.rs::
  DEFAULT_ASYNCIO_MODE`` and the measurement is ``conformance/corpus/async/mode-default``.
* *No warning for a bare ``@usefixtures``.*  pytest warns that it has no effect
  (`_pytest/fixtures.py::_getusefixturesnames`); with no warnings channel the mark is simply
  inert, which is the same *behaviour* and one fewer line of output.
* *Markdown code blocks are a rustest tier with no pytest counterpart.*  See
  :func:`collect_markdown`.
* *No item reordering, and the visible symptom is a **setup-count** difference, not just an
  ordering one.*  pytest groups tests sharing a higher-scoped parametrized fixture
  (`_pytest/fixtures.py::reorder_items`); that pass needs the whole session's item list,
  which a per-file worker does not have.  Ids stay correct, but two tests sharing a
  module-scoped ``params=["a","b"]`` fixture cost **2** setups under pytest (grouped
  ``a a b b``) and **4** here (interleaved ``a b a b``) — both measured — so anything the
  fixture accumulates is reset twice as often.  See :func:`collect_module`.  Pinned by
  ``conformance/corpus/fixtures/module-param-reorder``, which the gate catches on the
  **ordered** ids alone: the tally, the id set and the exit code all agree.
* *``session`` and ``package`` scope are per-worker for teardown and per **file** for
  setup* — the latter measured, and narrower than this list claimed until Phase 1c
  Task 2.  See :data:`_SCOPE_BUCKET` and
  ``conformance/corpus/fixtures/session-scope``.
* *A file is enumerated exactly as handed over.*  pytest never collects ``conftest.py``
  as a test module (it matches no ``python_files`` pattern and is loaded as a plugin),
  so the orchestrator should not send one as a collect target; doing so anyway is
  harmless — a conventional conftest yields no tests — and imports it under its real
  name.
"""

from __future__ import annotations

# `asyncio` is deliberately NOT imported here: it costs ~240 ms on the reference machine and
# a worker pays it N times per run (one process per pool slot) for a suite that may contain
# no async test at all.  The two functions that need it -- `FixtureRunner.run_coroutine` and
# `FixtureRunner._close_loop` -- import it in their own bodies, and the only module-scope
# reference left is the annotation on `self._loop`, which `from __future__ import
# annotations` never evaluates.  Measured, Phase 2 Task 3: worker boot 550 ms -> 300 ms.
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence, Set as AbstractSet
import contextlib
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
import fnmatch
import functools
import importlib
import inspect
import io
import itertools
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
import traceback
import types
from typing import TYPE_CHECKING, Any, Final, NoReturn, NotRequired, TextIO, TypedDict, cast
import unittest
import warnings

from . import _v2_builtins as _builtins

if TYPE_CHECKING:
    # Type-check-only, so the ~240 ms runtime import stays deferred (see the note above
    # the stdlib imports).  `from __future__ import annotations` makes every annotation a
    # string, so nothing here is evaluated at import time.
    import asyncio
    import contextvars

__all__ = [
    "AsyncioConfig",
    "BUILTIN_FIXTURES",
    "DEFAULT_NAMING",
    "ABORT_EXCEPTIONS",
    "CAPTURE_CLOSED_MESSAGE",
    "PHASES",
    "PROTOCOL_VERSION",
    "SESSION_EXIT_EXIT",
    "SHUTDOWN_TEARDOWN_EXIT",
    "SCOPE_NAMES",
    "DEFAULT_ASYNCIO_MODE",
    "DEFAULT_ASYNCIO_TEST_LOOP_SCOPE",
    "STATUSES",
    "UNSUPPORTED_BUILTIN_FIXTURES",
    "CollectionRefusal",
    "FixtureClosure",
    "FixtureDef",
    "FixtureLookupError",
    "FixtureRegistry",
    "FixtureRunner",
    "MarkSpec",
    "NotInitializedError",
    "Naming",
    "PhaseReport",
    "BatchDoneResponse",
    "UnknownTestError",
    "ScopeMismatch",
    "Skip",
    "Xfail",
    "ExecutionPlan",
    "build_closure",
    "build_nodeid",
    "build_registry",
    "collect_file",
    "collect_module",
    "conftest_chain",
    "encode_response",
    "drain_at_shutdown",
    "drain_boundaries",
    "enumerate_module",
    "evaluate_condition",
    "evaluate_skip_marks",
    "evaluate_xfail_marks",
    "execute_batch",
    "execute_test",
    "fixture_param_dimensions",
    "handle_init",
    "handle_shutdown",
    "import_conftest",
    "import_test_module",
    "install_pytest_shim",
    "main",
    "matches_name_pattern",
    "reduce_reports",
    "report_for_phase",
    "resolve_module_identity",
    "execution_plan",
]

#: The protocol this worker **speaks**.  Mirrors ``PROTOCOL_VERSION`` in
#: ``src/v2/protocol.rs``; the two must move together — a worker declaring the other side's
#: number turns every run into a handshake error.
#:
#: v2 adds the execute ops (``execute_test`` -> ``test_result``) and ``init.invocation_dir``.
#: Both halves are implemented here: :func:`collect_file` and :func:`execute_test`.
#:
#: v3 adds ``collect_file.assert_key`` (assertion rewriting, :func:`collect_file`) and the
#: batch execute op (``execute_batch`` -> N x ``test_result`` + ``batch_done``,
#: :func:`execute_batch`).
#:
#: v4 adds the three asyncio ini values to ``init`` (:class:`AsyncioConfig`).  They are read
#: at *collection* time as well as at execution time — ``asyncio_mode`` decides whether an
#: ``async def`` + ``yield`` test acquires the synthesised ``xfail`` mark
#: (:func:`_async_generator_xfail`) — which is why they ride on ``init`` rather than on the
#: execute ops.
#:
#: v5 adds ``init.coverage`` — ``--cov``'s only wire footprint (:mod:`rustest._v2_coverage`).
#: **Absent** for a run without ``--cov``, and the absence is load-bearing: it is what makes
#: the worker register no ``sys.monitoring`` tool at all.  Measurement has to start at ``init``
#: rather than at execution, because a module's import-time lines are lines coverage.py counts.
#:
#: v6 adds ``init.pythonpath`` — the ``pythonpath`` ini as absolute posix directories,
#: prepended to ``sys.path`` in :func:`handle_init` before anything is imported, which is
#: where pytest does it (``Config._configure_python_path``, called from
#: ``pytest_load_initial_conftests``).  **Absent** when the ini is unset, which is almost
#: every project; the absence is why a plain run's ``init`` line is byte-identical to v5's
#: apart from the number.
#:
#: v7 adds ``execute_batch.max_fail`` — ``--maxfail=N``'s **remaining** budget for that
#: batch, so a worker can cut it on the Nth failure rather than only on the first.  Absent
#: when there is no limit, which keeps an ordinary batch line byte-identical to v6's.
PROTOCOL_VERSION: Final = 7

#: Exit code for "the response stream is complete, but a fixture teardown failed after the
#: last test was answered" — i.e. :func:`drain_at_shutdown` raised.
#:
#: **Why it cannot be 0.**  A class- or module-scoped teardown that fails on the *last* test
#: routed to a worker has no test left to be attributed to: that test was already answered
#: ``passed``.  Exiting 0 turns a real failure into a **green run** with the traceback buried
#: in stderr, which is the exact silent-failure shape this worker exists to refuse.
#:
#: **Why it is not 2.**  Exit 2 means *protocol drift* — a malformed line, an unknown op, an
#: id nobody collected — and a user's broken teardown is not that.  A distinct code keeps the
#: two diagnoses apart while still being fatal: the orchestrator treats any non-zero status
#: after ``bye`` as a failed run and reports the code
#: (``src/v2/collect.rs::a_nonzero_exit_after_bye_is_still_a_failure``), so ``bye`` is still
#: written and the stream is still well-formed — the run just does not come back green.
SHUTDOWN_TEARDOWN_EXIT: Final = 3


# ---------------------------------------------------------------------------
# wire shapes — the TypedDicts encode the omit-when-empty rules
# ---------------------------------------------------------------------------


class MarkSpecDict(TypedDict):
    """``MarkSpec`` (``src/v2/manifest.rs``).  A bare mark is ``{"name": "slow"}``."""

    name: str
    args: NotRequired[list[Any]]
    kwargs: NotRequired[dict[str, Any]]


class CollectedTestDict(TypedDict):
    """``CollectedTest`` (``src/v2/manifest.rs``), key order = serde field order."""

    id: str
    path: str
    qualname: str
    class_name: NotRequired[str]
    param_id: NotRequired[str]
    marks: NotRequired[list[MarkSpecDict]]
    fixtures: NotRequired[list[str]]


class CollectionErrorDict(TypedDict):
    """``CollectionErrorEntry`` (``src/v2/manifest.rs``)."""

    path: str
    message: str


class ReadyResponse(TypedDict):
    op: str
    protocol_version: int


class CollectedResponse(TypedDict):
    """``WorkerResponse::Collected`` (``src/v2/protocol.rs``).

    ``skipped`` is the module-level skip: the file asked, at import time, not to be
    collected at all (``pytest.skip(..., allow_module_level=True)`` or an
    ``importorskip`` that could not import).  It is **neither** ``tests`` nor ``error``,
    and it needs its own field because pytest reports it as neither:

    * it contributes **no node id** — probed on pytest 8.4.2, ``--collect-only -q`` over a
      tree with two module-level-skipped files lists only the surviving file's tests;
    * it *does* contribute **one ``skipped``** to the summary line — the same probe prints
      ``1 passed, 2 skipped``.

    So a count with no id, which is exactly the shape ``CollectionManifest::deselected``
    already has. Carrying it as ``tests`` would invent an id pytest does not have; carrying
    it as ``error`` would abort the session for a file pytest ran past.
    """

    op: str
    path: str
    tests: NotRequired[list[CollectedTestDict]]
    error: NotRequired[CollectionErrorDict]
    skipped: NotRequired[str]


class ResultResponse(TypedDict):
    """``WorkerResponse::TestResult`` (``src/v2/protocol.rs``), key order = serde field order.

    Named for the *response*, not for the wire tag: a ``TestResultDict`` would match the
    default ``python_classes = ["Test"]`` prefix and be probed for collection in every module
    that imports it — the same trap that renamed ``TestPlan`` to :class:`ExecutionPlan`.

    ``message``/``stdout``/``stderr`` are **omitted, never empty** — an unremarkable pass is
    the four-key line, which is the shape the golden string pins.
    """

    op: str
    id: str
    status: str
    duration_s: float
    message: NotRequired[str]
    stdout: NotRequired[str]
    stderr: NotRequired[str]


class BatchDoneResponse(TypedDict):
    """``WorkerResponse::BatchDone`` (``src/v2/protocol.rs``) — the batch stream terminator.

    Both fields are always written. ``executed`` is counted here, independently of the
    orchestrator's own tally, so a result that never reached the wire becomes a loud protocol
    error instead of a test quietly missing from the report; ``stopped`` says the batch ended
    early because ``-x`` fired, which the orchestrator must be able to tell apart from that
    same lost result.
    """

    op: str
    executed: int
    stopped: bool


class ByeResponse(TypedDict):
    op: str


class NotInitializedError(Exception):
    """`collect_file` arrived before `init` — a protocol violation, not a bad file.

    Routed to the fatal-on-drift path in :func:`main` (exit 2), never turned into a
    ``collected`` error entry: the worker has no rootdir, so it could not even name
    the file correctly in one.
    """


class UnknownTestError(Exception):
    """``execute_test`` named an id this worker never collected.

    The orchestrator routes an execute back to the worker that collected the file
    (``src/v2/collect.rs`` stem-hash routing), so an id that is not in this worker's index
    is **routing drift, not data**.  ``WorkerResponse`` has no error variant for the execute
    op, and the two candidate answers are not equivalent: replying ``status:"error"`` would
    put a fabricated test into the report and let a whole worker's worth of mis-routed ids
    look like ordinary failures.  So this takes the same path as an unknown op — stderr and
    exit 2 — which is what ``WorkerRequest::ExecuteTest``'s doc means by "a protocol error
    response, not a silent skip".
    """


class CollectionRefusal(Exception):
    """A file pytest itself refuses to collect, carrying pytest's own message.

    Raised for the two shapes pytest turns into a *collection error* (not a warning):
    an import-file mismatch (`_pytest/python.py::importtestmodule`) and a generator
    test function (`_pytest/python.py::pytest_pycollect_makeitem`).  Both abort the
    whole file, which is what pytest does.
    """


# ---------------------------------------------------------------------------
# naming rules
# ---------------------------------------------------------------------------

#: Source: `_pytest/python.py::pytest_addoption` — the three `addini` defaults.
DEFAULT_PYTHON_FILES: Final = ("test_*.py", "*_test.py")
DEFAULT_PYTHON_CLASSES: Final = ("Test",)
DEFAULT_PYTHON_FUNCTIONS: Final = ("test",)


@dataclass(frozen=True)
class Naming:
    """The three naming patterns from ``ResolvedConfig``, passed through ``init``.

    ``python_files`` is carried for completeness — file selection is the Rust
    orchestrator's job (it decides which paths to send), so the worker never
    consults it.
    """

    python_files: tuple[str, ...] = DEFAULT_PYTHON_FILES
    python_classes: tuple[str, ...] = DEFAULT_PYTHON_CLASSES
    python_functions: tuple[str, ...] = DEFAULT_PYTHON_FUNCTIONS


DEFAULT_NAMING: Final = Naming()


def matches_name_pattern(name: str, patterns: Sequence[str]) -> bool:
    """pytest's prefix-or-glob rule for ``python_classes`` / ``python_functions``.

    Port of `_pytest/python.py::PyCollector._matches_prefix_or_glob_option`::

        for option in self.config.getini(option_name):
            if name.startswith(option):
                return True
            elif ("*" in option or "?" in option or "[" in option) and fnmatch.fnmatch(name, option):
                return True
        return False

    The prefix test comes first, which is why the default ``python_functions =
    ["test"]`` collects ``testfoo`` (corpus ``collection/naming-testfoo``).  Mirrors
    ``src/v2/config.rs::matches_name_pattern``.
    """
    for option in patterns:
        if name.startswith(option):
            return True
        if ("*" in option or "?" in option or "[" in option) and fnmatch.fnmatch(name, option):
            return True
    return False


class _IgnoredProbe:
    """Probe class for :data:`IGNORED_ATTRIBUTES` (pytest calls it ``_EmptyClass``)."""


#: Names never considered for collection, however permissive the patterns are.
#: Port of `_pytest/python.py::IGNORED_ATTRIBUTES` — without it a wildcard pattern
#: such as ``python_functions = ["*"]`` would try to collect ``__init_subclass__``.
IGNORED_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    [
        *dir(types.ModuleType("empty_module")),
        "__builtins__",
        "__file__",
        "__cached__",
        *dir(_IgnoredProbe),
        *dir(_IgnoredProbe()),
    ]
)


# ---------------------------------------------------------------------------
# nodeids
# ---------------------------------------------------------------------------


def build_nodeid(path: str, parts: Sequence[str], param_id: str | None) -> str:
    """Port of ``src/v2/nodeid.rs::build_nodeid``.

    ``path`` is rootdir-relative posix, ``parts`` is the class chain followed by the
    function name, ``param_id`` is the bracket content *without* brackets.  Nothing
    is escaped: pytest sanitises ids when it generates them
    (`_pytest/python.py::IdMaker`), not when it assembles the nodeid.
    """
    nodeid = path + "".join("::" + part for part in parts)
    if param_id is not None:
        nodeid += "[" + param_id + "]"
    return nodeid


def _relative_posix(path: Path, rootdir: Path) -> str:
    """Rootdir-relative posix path — the manifest's ``path`` contract.

    Source: `_pytest/nodes.py::FSCollector.__init__` —
    ``str(self.path.relative_to(session.config.rootpath))`` with ``os.sep`` rewritten
    to ``/``.

    The outside-rootdir branch is a **v2 choice, not a port**: pytest falls back to
    ``_check_initialpaths_for_relpath``, which relativises against an *initial path*
    and returns ``None`` when none matches — pytest never produces an absolute
    nodeid, it produces no nodeid at all (the caller then requires ``parent`` to
    supply one).  The worker has no initialpaths and must return something
    addressable, so it emits the absolute posix path rather than ``..`` segments,
    which would break the "first segment is a path" nodeid contract in
    ``src/v2/nodeid.rs``.  Unreachable in practice: the orchestrator only sends files
    it walked from rootdir.
    """
    try:
        return path.relative_to(rootdir).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# core 1: module identity (issue #130)
# ---------------------------------------------------------------------------


def _resolve_package_path(path: Path) -> Path | None:
    """Highest directory above *path* still containing ``__init__.py``.

    Port of `_pytest/pathlib.py::resolve_package_path`, including the
    ``isidentifier()`` guard that stops the walk at a directory like ``my-tests``
    whose name could never appear in a dotted module name.
    """
    result: Path | None = None
    for parent in (path, *path.parents):
        if parent.is_dir():
            if not (parent / "__init__.py").is_file():
                break
            if not parent.name.isidentifier():
                break
            result = parent
    return result


def _compute_module_name(root: Path, module_path: Path) -> str | None:
    """Port of `_pytest/pathlib.py::compute_module_name`."""
    try:
        path_without_suffix = module_path.with_suffix("")
    except ValueError:  # pragma: no cover - only reachable for empty paths
        return None
    try:
        relative = path_without_suffix.relative_to(root)
    except ValueError:  # pragma: no cover - root always contains module_path here
        return None
    names = list(relative.parts)
    if not names:
        return None
    if names[-1] == "__init__":
        names.pop()
    return ".".join(names)


def resolve_module_identity(path: Path, rootdir: Path) -> tuple[str, str | None]:
    """Return ``(module_name, package_root)`` — the file's REAL import identity.

    Port of `_pytest/pathlib.py::resolve_pkg_root_and_module_name` as reached from
    `import_path` in its default ``ImportMode.prepend`` mode::

        try:
            pkg_root, module_name = resolve_pkg_root_and_module_name(path, ...)
        except CouldNotResolvePathError:
            pkg_root, module_name = path.parent, path.stem

    A file inside a package chain gets its full dotted name (``tests.unit.test_a``)
    and the package root as the directory to place on ``sys.path``; a file in a
    non-package directory gets its bare stem (``conftest``, ``test_a``) and
    ``package_root is None``, meaning "the sys.path root is the containing
    directory" (:func:`sys_path_root_for`).

    ``rootdir`` is **deliberately unused**: pytest's default importmode anchors the
    name on the ``__init__.py`` chain alone, never on rootdir — only
    ``ImportMode.importlib`` uses ``root`` (the ``module_name_from_path(path, root)``
    branch of `import_path`).  Probed: with rootdir set to the file's own directory,
    pytest still reports ``pkg.sub.test_deep``.  The parameter stays in the signature
    because it is the contract Task 3 was planned against and because an importlib
    mode would need it.

    Known limitation: ``consider_namespace_packages`` is not implemented.  pytest's
    ini default is ``False`` (`_pytest/main.py::pytest_addoption`), so this matches
    the default configuration; a suite that opts in would get bare-stem identities.
    """
    del rootdir  # see docstring: prepend-mode identity never consults rootdir
    package_path = _resolve_package_path(path)
    if package_path is not None:
        package_root = package_path.parent
        module_name = _compute_module_name(package_root, path)
        if module_name:
            return module_name, str(package_root)
    return path.stem, None


def sys_path_root_for(path: Path, package_root: str | None) -> str:
    """The directory pytest's ``prepend`` importmode puts on ``sys.path``."""
    return package_root if package_root is not None else str(path.parent)


def _import_mismatch_message(module_name: str, module_file: object, path: Path) -> str:
    """pytest's own wording for an import-file mismatch.

    Source: `_pytest/python.py::importtestmodule`, the ``ImportPathMismatchError``
    branch.  Reproduced verbatim so operators can search for the same string.
    """
    return (
        "import file mismatch:\n"
        + f"imported module {module_name!r} has this __file__ attribute:\n"
        + f"  {module_file}\n"
        + "which is not the same as the test file we want to collect:\n"
        + f"  {path}\n"
        + "HINT: remove __pycache__ / .pyc files and/or use a "
        + "unique basename for your test file modules"
    )


def _is_same_file(first: str, second: str) -> bool:
    """Port of `_pytest/pathlib.py::_is_same` (the Windows branch, which is a superset)."""
    if Path(first) == Path(second):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def import_test_module(path: Path, rootdir: Path) -> types.ModuleType:
    """Import *path* under its real dotted name and return the module.

    This is the #130 fix.  Because the module lands in ``sys.modules`` under the name
    Python itself would use, a test module's ``import conftest`` reaches **the same
    object** the worker imported — shared state in a conftest is visible, and a
    fixture defined there is the same function object.

    Port of `_pytest/pathlib.py::import_path` in ``ImportMode.prepend``:

    * insert the package root at ``sys.path[0]`` unless it is already there —
      ``if str(pkg_root) != sys.path[0]: sys.path.insert(0, str(pkg_root))``.  The
      insertion is permanent by design; pytest documents that restoring ``sys.path``
      afterwards breaks delayed imports inside the imported module.
    * ``importlib.import_module(module_name)``, which returns the cached module if
      that name is already imported — this is what makes same-stem collisions
      *first-wins*.
    * verify ``mod.__file__`` really is *path*; a mismatch is
      ``ImportPathMismatchError``, which pytest turns into a collection error.
      Probed against pytest 8.4.2: two ``test_dup.py`` files in different
      non-package directories collect the first and **error** on the second.
      ``PY_IGNORE_IMPORTMISMATCH=1`` skips the check, exactly as in
      `import_path` (``ignore = os.environ.get("PY_IGNORE_IMPORTMISMATCH", "")``);
      the escape hatch exists for build systems that legitimately import the same
      file under two paths, and honouring it keeps such suites collectable.

    ``insert_missing_modules`` is deliberately NOT ported: it exists only for
    ``ImportMode.importlib``'s synthetic dotted names (`_pytest/pathlib.py`
    docstring: "Used by ``import_path`` to create intermediate modules when using
    mode=importlib").  In prepend mode the parents are real packages and Python's
    own import machinery creates them.
    """
    module_name, package_root = resolve_module_identity(path, rootdir)
    root = sys_path_root_for(path, package_root)
    if not sys.path or root != sys.path[0]:
        sys.path.insert(0, root)

    _ = importlib.import_module(module_name)
    module = sys.modules[module_name]

    if path.name == "__init__.py":
        return module
    if os.environ.get("PY_IGNORE_IMPORTMISMATCH", "") == "1":
        return module

    module_file = getattr(module, "__file__", None)
    if module_file is None:
        raise CollectionRefusal(_import_mismatch_message(module_name, module_file, path))
    if module_file.endswith((".pyc", ".pyo")):
        module_file = module_file[:-1]
    init_suffix = os.sep + "__init__.py"
    if module_file.endswith(init_suffix):
        module_file = module_file[: -len(init_suffix)]
    if not _is_same_file(module_file, str(path)):
        raise CollectionRefusal(_import_mismatch_message(module_name, module_file, path))
    return module


# ---------------------------------------------------------------------------
# core 2: enumeration
# ---------------------------------------------------------------------------


def _safe_getattr(obj: object, name: str, default: object) -> object:
    """Port of `_pytest/compat.py::safe_getattr` — a property that raises is not a veto."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _unwrap(obj: object) -> object:
    """staticmethod/classmethod (and bound methods) carry the real function on ``__func__``.

    Source: `_pytest/python.py::PyCollector.istestfunction` and
    `pytest_pycollect_makeitem` ("mock seems to store unbound methods, normalize it").
    """
    return getattr(obj, "__func__", obj)


def _is_nose_test(obj: object) -> bool:
    """Port of `_pytest/python.py::PyCollector.isnosetest` — explicit ``__test__ = True``."""
    return _safe_getattr(obj, "__test__", False) is True


def _has_fixture_marker(obj: object) -> bool:
    """rustest's analogue of pytest's ``fixtures.getfixturemarker(obj) is None`` check.

    `_pytest/python.py::PyCollector.istestfunction` refuses to collect a fixture even
    when its name matches; rustest marks fixtures with ``__rustest_fixture__``
    (``python/rustest/decorators.py::fixture``), which the compat shim's
    ``pytest.fixture`` sets too.
    """
    return _safe_getattr(obj, "__rustest_fixture__", False) is True


def _is_test_function(obj: object, name: str, naming: Naming) -> bool:
    """Port of `_pytest/python.py::PyCollector.istestfunction`."""
    if not (matches_name_pattern(name, naming.python_functions) or _is_nose_test(obj)):
        return False
    candidate = _unwrap(cast(object, obj)) if isinstance(obj, (staticmethod, classmethod)) else obj
    return callable(candidate) and not _has_fixture_marker(candidate)


def _is_test_class(obj: type, name: str, naming: Naming) -> bool:
    """Port of `_pytest/python.py::PyCollector.istestclass`."""
    if not (matches_name_pattern(name, naming.python_classes) or _is_nose_test(obj)):
        return False
    return not inspect.isabstract(obj)


def _hasinit(obj: type) -> bool:
    """Port of `_pytest/python.py::hasinit`."""
    init: object = getattr(obj, "__init__", None)
    return bool(init) and init != object.__init__


def _hasnew(obj: type) -> bool:
    """Port of `_pytest/python.py::hasnew`."""
    new: object = getattr(obj, "__new__", None)
    return bool(new) and new != object.__new__


def _is_unittest_case(obj: object) -> bool:
    """Port of `_pytest/unittest.py::pytest_pycollect_makeitem`.

    Note what this does **not** check: the class name.  A ``unittest.TestCase``
    subclass is collected even when it fails ``python_classes`` — probed, and
    ``class Legacy(unittest.TestCase)`` does collect under pytest 8.4.2.  pytest also
    guards on ``sys.modules["unittest"]`` existing; this worker imports ``unittest``
    itself, and a module defining a ``TestCase`` subclass has necessarily imported it,
    so the guard is vacuous here.
    """
    if not inspect.isclass(obj):
        return False
    try:
        if not issubclass(obj, unittest.TestCase):
            return False
    except Exception:  # pragma: no cover - issubclass on exotic metaclasses
        return False
    return not inspect.isabstract(obj)


def _mro_ordered_members(cls: type) -> list[tuple[str, object]]:
    """Class members in pytest's collection order.

    Port of `_pytest/python.py::PyCollector.collect`: walk ``[cls.__dict__] +
    [base.__dict__ for base in cls.__mro__]`` de-duplicating by first sighting, then
    emit the per-dict groups in **reverse** order ("nodes inherited from base classes
    should come before subclasses").  Probed: ``class TestB(TestA)`` yields
    ``TestB::test_base`` then ``TestB::test_derived``; an **override** is attributed
    to the derived dict (first seen) and therefore sorts with the derived methods.
    """
    dicts: list[Mapping[str, object]] = [cast(Mapping[str, object], getattr(cls, "__dict__", {}))]
    for base in cls.__mro__:
        dicts.append(cast(Mapping[str, object], base.__dict__))

    seen: set[str] = set()
    groups: list[list[tuple[str, object]]] = []
    for namespace in dicts:
        members: list[tuple[str, object]] = []
        for name, obj in list(namespace.items()):
            if name in IGNORED_ATTRIBUTES or name in seen:
                continue
            seen.add(name)
            members.append((name, obj))
        groups.append(members)

    return [member for group in reversed(groups) for member in group]


def _json_safe(value: object) -> Any:
    """Make a mark argument JSON-encodable.

    Convention: anything JSON cannot represent is replaced by its ``repr()`` string.
    Marks are *data* on this wire — they are carried for 1b.2 to interpret, and a
    lossy-but-printable value beats refusing to collect the test.  Non-finite floats
    take the same route because ``NaN``/``Infinity`` are not valid JSON and serde
    would reject the line.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in cast(Sequence[object], value)]
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item) for key, item in cast(Mapping[object, object], value).items()
        }
    return repr(value)


#: Shared empty kwargs mapping for :class:`MarkSpec`.  A ``MappingProxyType`` rather than a
#: ``{}`` for two reasons: ``dataclasses`` refuses a ``dict`` as a field default outright
#: (mutable-default guard), and a read-only proxy cannot be mutated through one mark and
#: silently appear on every other mark that shares it.
_NO_KWARGS: Final[Mapping[str, object]] = types.MappingProxyType({})


@dataclass(frozen=True)
class MarkSpec:
    """One mark as collected — arguments still **live Python objects**.

    The wire form (:class:`MarkSpecDict`) is JSON-safe: :func:`_json_safe` replaces anything
    JSON cannot represent by its ``repr()``.  That is right for a manifest, which only ever
    *displays* marks, and fatal for the execute half, which has to *evaluate* them:

    * ``@pytest.mark.xfail(raises=ValueError)`` would arrive as the string
      ``"<class 'ValueError'>"``, and ``isinstance(exc, "<class 'ValueError'>")`` is not a
      check, it is a ``TypeError``;
    * a ``skipif`` condition that is falsy but not a builtin — a ``numpy.bool_(False)``, an
      object with ``__bool__`` — would arrive as a **non-empty repr string**, i.e. truthy,
      and silently skip a test that should have run.

    So collection keeps the objects and :meth:`to_wire` projects them lossily at the wire
    boundary only.  :attr:`ExecutionPlan.marks` therefore holds ``MarkSpec``, and
    ``CollectedTest.marks`` holds ``MarkSpecDict``; there is exactly one producer for both.
    """

    name: str
    args: tuple[object, ...] = ()
    kwargs: Mapping[str, object] = _NO_KWARGS

    def to_wire(self) -> MarkSpecDict:
        """The JSON-safe ``MarkSpec`` of ``src/v2/manifest.rs``, empty fields omitted."""
        spec: MarkSpecDict = {"name": self.name}
        if self.args:
            spec["args"] = [_json_safe(arg) for arg in self.args]
        if self.kwargs:
            spec["kwargs"] = {key: _json_safe(value) for key, value in self.kwargs.items()}
        return spec


def _spec_from_mark_dict(raw: object, owner: object) -> MarkSpec:
    """One ``__rustest_marks__`` entry -> one :class:`MarkSpec`.

    A non-dict entry is malformed metadata and becomes a hard refusal rather than a
    silent skip: the mark would vanish from the manifest with nothing to show for it,
    and a mark that silently disappears is exactly the class of failure the protocol's
    loud-on-drift philosophy exists to prevent.
    """
    if not isinstance(raw, dict):
        raise CollectionRefusal(
            "malformed rustest mark metadata on "
            + f"{getattr(owner, '__qualname__', owner)!r}: expected a dict, got {raw!r}"
        )
    mark = cast(Mapping[str, object], raw)
    return MarkSpec(
        name=str(mark.get("name", "")),
        args=tuple(cast(Sequence[object], mark.get("args", ()))),
        kwargs={
            str(key): value
            for key, value in cast(Mapping[object, object], mark.get("kwargs", {})).items()
        },
    )


def _spec_from_pytestmark(entry: object, owner: object) -> MarkSpec:
    """One ``pytestmark`` entry -> one ``MarkSpec``.

    ``pytestmark`` holds *decorator objects*, not dicts.  Through the compat shim an
    uncalled ``pytest.mark.slow`` is a ``decorators.py::BareOrFactoryMark`` and
    ``pytest.mark.skipif(...)`` is a ``MarkDecorator``; both expose ``name``/``args``/
    ``kwargs``, and so does real pytest's ``MarkDecorator``, so the duck typing covers a
    worker running without the shim too.  The ``mark_name`` fallback below is for the
    pre-#137 ``_MarkDecoratorFactory`` shape, which carried only that attribute and made an
    uncalled mark in ``pytestmark`` refuse the whole file.  Anything else is malformed and
    refuses the file rather than silently dropping a mark.
    """
    name = _safe_getattr(entry, "name", None)
    if isinstance(name, str):
        return _spec_from_mark_dict(
            {
                "name": name,
                "args": _safe_getattr(entry, "args", ()),
                "kwargs": _safe_getattr(entry, "kwargs", {}),
            },
            owner,
        )
    bare_name = _safe_getattr(entry, "mark_name", None)
    if isinstance(bare_name, str):
        return MarkSpec(bare_name)
    raise CollectionRefusal(
        "malformed pytestmark entry on "
        + f"{getattr(owner, '__qualname__', getattr(owner, '__name__', owner))!r}: "
        + f"{entry!r} is not a mark"
    )


def _pytestmark_specs(obj: object, *, consider_mro: bool) -> list[MarkSpec]:
    """Port of `_pytest/mark/structures.py::get_unpacked_marks`.

    For a class, read ``pytestmark`` out of each ``__dict__`` in **reversed MRO**
    order (base first) — never plain ``getattr``, which would report a base class's
    marks as the subclass's own and count them twice.  For anything else, read the
    attribute and accept either a single mark or a list.
    """
    mark_lists: list[object] = []
    if isinstance(obj, type) and consider_mro:
        for klass in reversed(obj.__mro__):
            mark_lists.append(klass.__dict__.get("pytestmark", []))
    elif isinstance(obj, type):
        mark_lists.append(obj.__dict__.get("pytestmark", []))
    else:
        mark_lists.append(_safe_getattr(obj, "pytestmark", []))

    specs: list[MarkSpec] = []
    for item in mark_lists:
        entries = cast(Sequence[object], item) if isinstance(item, list) else [item]
        specs.extend(_spec_from_pytestmark(entry, obj) for entry in entries)
    return specs


def _rustest_mark_specs(obj: object, *, consider_mro: bool) -> list[MarkSpec]:
    """Read ``__rustest_marks__`` with the same reversed-MRO ``__dict__`` discipline.

    For classes this deliberately mirrors `get_unpacked_marks`, for one extra reason:
    ``decorators.py::MarkDecorator.__call__`` seeds its list with
    ``getattr(func, "__rustest_marks__", [])``, so decorating a subclass **mutates the
    base class's list in place** and leaves both classes' ``__dict__`` pointing at the
    *same* object (issue #135; probed — ``A.__dict__[...] is B.__dict__[...]`` is
    True).  Plain ``getattr`` would report the merged list once per level; walking
    ``__dict__`` and skipping a list object already consumed (identity, not equality)
    reads that corrupted state exactly once, so this path neither amplifies the bug
    nor pretends it is not there.
    """
    mark_lists: list[object] = []
    if isinstance(obj, type) and consider_mro:
        seen_ids: set[int] = set()
        for klass in reversed(obj.__mro__):
            raw = klass.__dict__.get("__rustest_marks__")
            if raw is None or id(raw) in seen_ids:
                continue
            seen_ids.add(id(raw))
            mark_lists.append(raw)
    elif isinstance(obj, type):
        raw = obj.__dict__.get("__rustest_marks__")
        if raw is not None:
            mark_lists.append(raw)
    else:
        raw = _safe_getattr(obj, "__rustest_marks__", None)
        if raw is not None:
            mark_lists.append(raw)

    specs: list[MarkSpec] = []
    for raw_list in mark_lists:
        if not isinstance(raw_list, list):
            raise CollectionRefusal(
                "malformed rustest mark metadata on "
                + f"{getattr(obj, '__qualname__', obj)!r}: "
                + f"__rustest_marks__ must be a list, got {raw_list!r}"
            )
        specs.extend(_spec_from_mark_dict(raw, obj) for raw in cast(Sequence[object], raw_list))
    return specs


def _mark_specs(obj: object, *, consider_mro: bool = False) -> list[MarkSpec]:
    """All marks carried *directly* by *obj*, as :class:`MarkSpec` objects.

    Three sources, in the order pytest itself would report them for one node:

    * ``pytestmark`` — the attribute pytest reads
      (`_pytest/nodes.py` line 284/1599: ``own_markers.extend(get_unpacked_marks(self.obj))``);
    * ``__rustest_marks__`` — rustest's own decorator metadata
      (``decorators.py::MarkDecorator.__call__``);
    * ``__rustest_skip__`` — the reason string set by ``decorators.py::skip_decorator``,
      which is what the *compat* ``pytest.mark.skip`` uses instead of a mark entry.

    Conditions are **not** evaluated here — :func:`evaluate_skip_marks` and
    :func:`evaluate_xfail_marks` do that at *execute* time, which is where pytest does it
    (`_pytest/skipping.py::pytest_runtest_setup`).  (Note a v1-ism inherited through the
    shim: ``decorators.py::MarkDecorator._normalize_args`` evaluates a *string*
    ``skipif`` condition at decoration time, so such a condition usually arrives already
    reduced to a bool; ``xfail`` string conditions are not normalised and reach the
    evaluator intact.)
    """
    specs = _pytestmark_specs(obj, consider_mro=consider_mro)
    specs.extend(_rustest_mark_specs(obj, consider_mro=consider_mro))
    skip_reason = _safe_getattr(obj, "__rustest_skip__", None)
    if skip_reason is not None:
        specs.append(MarkSpec("skip", kwargs={"reason": skip_reason}))
    return specs


def _parametrization(func: object) -> "list[_Case] | None":
    """Read v1's parametrize metadata and return ``(param_id, values, marks)`` per case.

    The ids are **v1's, consumed verbatim** — not recomputed.  They are produced at
    decoration time by ``python/rustest/decorators.py::parametrize`` ->
    ``_build_cases`` -> ``_resolve_case_id`` (explicit ``ids=``, ``pytest.param(id=)``,
    else ``_generate_param_id``), and stacked decorators are cross-producted by
    ``_cross_product_cases``.  Recomputing them here is impossible without changing
    v1: the stored case keeps only the final id and the value mapping, so an explicit
    ``ids=["one", "two"]`` is indistinguishable from a generated one.

    Divergences from `_pytest/python.py::IdMaker` therefore carry over — v1 matches
    pytest for int/float/bool/None/short-str values, argname-matching tuples,
    explicit ids and ``param(id=)`` (which is every corpus case), and differs for
    long strings (v1 truncates), non-ASCII (pytest ascii-escapes), bytes, complex,
    enums, objects with ``__name__`` and container values (pytest falls back to
    ``<argname><index>``).  See the task report for the probed table.
    """
    cases = _safe_getattr(func, "__rustest_parametrization__", None)
    if cases is None:
        return None
    if not isinstance(cases, (list, tuple)):
        raise CollectionRefusal(
            "malformed rustest parametrization on "
            + f"{getattr(func, '__qualname__', func)!r}: expected a sequence of cases, "
            + f"got {cases!r}"
        )
    if not cases:
        return None

    raw_ids: list[str] = []
    valuesets: list[Mapping[str, object]] = []
    marksets: list[tuple[MarkSpec, ...]] = []
    for case in cast(Sequence[object], cases):
        if not isinstance(case, dict):
            # Silently collapsing to a single unparametrized entry would delete every
            # case from the manifest with no error anywhere; refuse the file instead.
            raise CollectionRefusal(
                "malformed rustest parametrization on "
                + f"{getattr(func, '__qualname__', func)!r}: "
                + f"expected a case dict, got {case!r}"
            )
        entry = cast(Mapping[str, object], case)
        raw_ids.append(str(entry.get("id", "")))
        values = entry.get("values", {})
        valuesets.append(
            {str(name): value for name, value in cast(Mapping[object, object], values).items()}
            if isinstance(values, dict)
            else {}
        )
        # `pytest.param(x, marks=...)` — the payloads `decorators.py::_mark_payloads`
        # writes are the same `{"name", "args", "kwargs"}` dicts `__rustest_marks__` uses,
        # so the existing reader takes them unchanged.
        raw_marks = entry.get("marks", ())
        marksets.append(
            tuple(_spec_from_mark_dict(raw, func) for raw in cast(Sequence[object], raw_marks))
            if isinstance(raw_marks, (list, tuple))
            else ()
        )
    return list(zip(_unique_parameterset_ids(raw_ids), valuesets, marksets, strict=True))


#: One parametrization case: its id component, the values it supplies, and the marks that
#: **that set alone** carries (`pytest.param(..., marks=...)`).
_Case = tuple[str, Mapping[str, object], tuple["MarkSpec", ...]]


def _cross_product_cases(outer: list[_Case], inner: list[_Case]) -> list[_Case]:
    """Combine an enclosing class's cases with a method's own — **pytest's order**.

    The *values* merge with the inner (method) call winning a name collision and the outer
    (class) dimension varying slowest, which is what makes the run order
    ``[10-1], [10-2], [20-1], [20-2]`` for a class carrying ``@parametrize("x", [1, 2])``
    and a method carrying ``@parametrize("y", [10, 20])``.

    The *id* joins **method component first**, which is the opposite of the values' nesting
    and is not a typo: pytest appends each ``parametrize`` call's id component to
    ``CallSpec2._idlist`` in the order the calls are *made*, and a method's own decorator is
    applied before the enclosing class's mark is unpacked. Measured on pytest 8.4.2:
    ``test_m[10-1]``.

    v1 emits ``test_m[1-10]``, and this worker used to inherit that spelling because it
    consumes v1's pre-computed ids. Phase 4 Task 1's review took the other side of that
    trade: the id is the thing a user writes in ``-k`` and reads in CI output, matching
    pytest is the product promise, and v1 is deleted in Task 2 — so the inherited spelling
    would have outlived the engine it came from.
    """
    # The **method** dimension is the outer loop as well as the leading id component: both
    # are the same fact, and pytest shows it as `[10-1], [10-2], [20-1], [20-2]`.
    return [
        (
            f"{inner_id}-{outer_id}",
            {**outer_values, **inner_values},
            (*outer_marks, *inner_marks),
        )
        for inner_id, inner_values, inner_marks in inner
        for outer_id, outer_values, outer_marks in outer
    ]


def _indirect_names(func: object) -> frozenset[str]:
    """Argnames a ``@parametrize(..., indirect=...)`` routes **through a fixture**.

    Read from ``__rustest_parametrization_indirect__``, which
    ``decorators.py::parametrize`` writes after normalising through
    ``_normalize_indirect`` (the port of `_pytest/python.py::Metafunc._resolve_args_directness`).
    An indirect name is pytest's ``arg_directness[...] == "indirect"``: it keeps its
    fixturedefs in the closure, its parametrized value is delivered to that fixture as
    ``request.param``, and the *test* receives whatever the fixture returns.
    """
    raw = _safe_getattr(func, "__rustest_parametrization_indirect__", None)
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(str(name) for name in cast(Sequence[object], raw))


def _unique_parameterset_ids(ids: list[str]) -> list[str]:
    """Port of `_pytest/python.py::IdMaker.make_unique_parameterset_ids`.

    Duplicate ids get a numeric suffix (``1`` -> ``1_0``, ``1_1``; ``a`` -> ``a0``,
    ``a1``), the underscore appearing only when the id already ends in a digit.
    Without this two cases would share a nodeid, which breaks the manifest's
    addressability contract outright.

    Faithful for a single ``parametrize`` call, which is where pytest applies it.
    pytest de-duplicates *per call* and only then joins stacked ids with ``-``; v1
    hands over ids already cross-producted, so for stacked decorators whose *inner*
    call contains duplicates the suffix lands on the joined id instead of the
    component (``1-1_0`` where pytest writes ``1_0-1``).  Both are unique; only
    pytest's is byte-exact, and the underlying stacked-duplicate id is already a v1
    divergence.
    """
    if len(ids) == len(set(ids)):
        return ids

    counts = Counter(ids)
    suffixes: defaultdict[str, int] = defaultdict(int)
    resolved = list(ids)
    for index, value in enumerate(ids):
        if counts[value] <= 1:
            continue
        suffix = "_" if value and value[-1].isdigit() else ""
        new_id = f"{value}{suffix}{suffixes[value]}"
        while new_id in set(resolved):
            suffixes[value] += 1
            new_id = f"{value}{suffix}{suffixes[value]}"
        resolved[index] = new_id
        suffixes[value] += 1
    return resolved


#: pytest's message for a test it cannot run asynchronously
#: (`_pytest/python.py::async_fail`, l. 137-147), reproduced verbatim so an operator greps
#: for the same string they would under pytest.
ASYNC_NOT_SUPPORTED_MESSAGE: Final = (
    "async def functions are not natively supported.\n"
    "You need to install a suitable plugin for your async framework, for example:\n"
    "  - anyio\n"
    "  - pytest-asyncio\n"
    "  - pytest-tornasync\n"
    "  - pytest-trio\n"
    "  - pytest-twisted"
)


def _async_generator_xfail(func: object, name: str, is_asyncio_test: bool) -> MarkSpec | None:
    """An `xfail(run=False)` mark for an ``async def`` + ``yield`` test, or ``None``.

    **Port of `pytest_asyncio/plugin.py::AsyncGenerator._from_function` (l. 512-531)**, which
    is the oracle here rather than bare pytest: rustest natively runs `async def` tests, so it
    occupies pytest-asyncio's role, and pytest-asyncio's answer to an async *generator* test
    is::

        unsupported_item_type_message = (
            f"Tests based on asynchronous generators are not supported. "
            f"{function.name} will be ignored."
        )
        async_gen_item.warn(PytestCollectionWarning(unsupported_item_type_message))
        async_gen_item.add_marker(
            pytest.mark.xfail(run=False, reason=unsupported_item_type_message)
        )

    Probed on this repo's pytest 8.4.2 + pytest-asyncio 1.2.0 in ``asyncio_mode = auto``:
    ``async def test_x(): assert 1 == 2; yield`` reports **xfailed** with that warning, and
    the body never runs. Reproduced here byte-for-byte in reason text and outcome; the
    ``PytestCollectionWarning`` is the already-documented no-warnings-channel gap.

    What this replaces is a **silent green**: the body was called, returned an async
    generator object, raised nothing, and the test reported PASSED — measured. Bare pytest
    (no async plugin) hard-fails the same shape via ``async_fail``; either answer is red,
    and the plugin's is the one rustest is impersonating.

    The mark is prepended so it is the *closest* `xfail`, which is what
    :func:`evaluate_xfail_marks` takes; a test that also carries its own ``@xfail`` still gets
    `run=False`, because a body that cannot run cannot run either way.

    **Only when the test is an asyncio test**, which is what *is_asyncio_test* carries.
    pytest-asyncio synthesises the mark inside ``AsyncGenerator._from_function``, and
    ``_from_function`` is only reached for an item the collection hook actually *converted*
    (l. 606-614) — in ``strict`` mode an unmarked async generator test is never converted, so
    it acquires no xfail, is called like any other function, returns an async generator object
    and fails through ``_pytest/python.py::async_fail``. Probed in strict mode: **failed**,
    not xfailed. Synthesising the mark unconditionally turned that red into a green ``xfailed``
    — a *worse* answer than the old silent pass, because an xfail is a result the reader
    trusts.
    """
    if not is_asyncio_test or not inspect.isasyncgenfunction(func):
        return None
    reason = f"Tests based on asynchronous generators are not supported. {name} will be ignored."
    return MarkSpec(name="xfail", kwargs={"run": False, "reason": reason})


def _requested_argnames(
    func: object,
    name: str,
    owner: type | None,
) -> list[str]:
    """Every name *func* requests, in signature order.

    Port of `_pytest/compat.py::getfuncargnames` (l. 133-171), which is what pytest
    itself uses to decide what a test — or a fixture — *requests*:

    * only ``POSITIONAL_OR_KEYWORD`` and ``KEYWORD_ONLY`` parameters count;
    * **a parameter with a default is not a fixture request** —
      ``and p.default is Parameter.empty``.  ``def test_x(tmp_path, flag=1)`` requests
      only ``tmp_path``; pytest would never try to resolve a fixture named ``flag``.
      Verified against the installed pytest:
      ``getfuncargnames(def test_defaults(tmp_path, flag=1, *, capsys, extra=2))``
      returns ``('tmp_path', 'capsys')``.
    * the bound-method first argument is dropped when the attribute is *not* a
      ``staticmethod`` — pytest decides that with
      ``inspect.getattr_static(cls, name)`` and an explicit comment ("Not using
      `getattr` because we don't want to resolve the staticmethod"), **not** by
      looking at the parameter's name.  So a method written ``def test_m(this)``
      loses ``this``, and a ``@staticmethod`` keeps its first parameter — neither of
      which a ``self``/``cls`` name test gets right.  The guard on
      ``POSITIONAL_ONLY`` is ported with it.

    Unlike :func:`_fixture_names` this keeps names supplied by ``parametrize``: pytest's
    ``initialnames`` are the raw ``getfuncargnames`` result and the parametrized names are
    excluded from *resolution* instead, via ``getfixtureclosure``'s ``ignore_args``
    (`_pytest/fixtures.py::getfixtureinfo` l. 1565-1578).  Keeping them here is what makes
    :func:`build_closure` a faithful port.

    ``mock.patch``-injected parameters are stripped last, exactly as
    ``getfuncargnames`` l. 169-171 does it: ``if hasattr(function, "__wrapped__"):
    arg_names = arg_names[num_mock_patch_args(function):]``.  Without it a
    ``@patch("mod.thing")``-decorated test asks for a fixture named after its mock argument
    and reports ``fixture 'mock_thing' not found`` — a setup error for a perfectly correct
    test, which is what the self-suite's ``test_patch_decorator.py`` was reporting.
    """
    try:
        signature = inspect.signature(cast(Any, func))
    except (TypeError, ValueError):  # pragma: no cover - builtins never reach here
        return []
    parameters = list(signature.parameters.values())
    accepted = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    names = [
        parameter.name
        for parameter in parameters
        if parameter.kind in accepted and parameter.default is inspect.Parameter.empty
    ]
    has_positional_only = any(
        parameter.kind is inspect.Parameter.POSITIONAL_ONLY for parameter in parameters
    )
    if (
        names
        and not has_positional_only
        and owner is not None
        and not isinstance(inspect.getattr_static(owner, name, None), staticmethod)
    ):
        names = names[1:]
    if hasattr(func, "__wrapped__"):
        names = names[_num_mock_patch_args(func) :]
    return names


def _default_arg_names(func: object, name: str, owner: type | None) -> frozenset[str]:
    """Parameters that carry a **default**, i.e. what `getfuncargnames` deliberately drops.

    Port of `_pytest/compat.py::get_default_arg_names` (l. 174-181), used by
    :func:`_validate_if_using_arg_names` for pytest's more specific wording: parametrizing a
    name the function already accepts *with a default* is a different mistake from
    parametrizing a name it does not accept at all, and pytest says so.

    The bound-method first argument is not stripped here because it never has a default.
    """
    _ = name, owner
    try:
        signature = inspect.signature(cast(Any, func))
    except (TypeError, ValueError):  # pragma: no cover - builtins never reach here
        return frozenset()
    return frozenset(
        parameter.name
        for parameter in signature.parameters.values()
        if parameter.default is not inspect.Parameter.empty
    )


def _validate_if_using_arg_names(
    func: object,
    name: str,
    owner: type | None,
    argnames: AbstractSet[str],
    indirect: AbstractSet[str],
    fixturenames: AbstractSet[str],
) -> None:
    """Every parametrized name must be something the test can actually receive.

    Port of `_pytest/python.py::Metafunc._validate_if_using_arg_names` (pytest 8.4.2,
    l. 1455-1483), message for message. Four shapes, all measured on the oracle first and
    all of which rustest used to accept silently — the test ran and **passed**, with the
    parameter simply never delivered:

    * ``@parametrize("nosuch", ...)`` on a function that does not take ``nosuch`` ->
      ``In <func>: function uses no argument 'nosuch'``;
    * the same with ``indirect`` -> ``... uses no **fixture** 'nosuch'``. The word changes
      with the directness of *that* name, which is why `indirect` is a set here and not a
      flag;
    * ``@parametrize("val", ...)`` where ``def test(val=7)`` -> ``In <func>: function
      already takes an argument 'val' with a default value``;
    * one bad name inside a multi-name ``"a,b"`` -> reported for that name alone.

    All four are collection errors, exit 2, on both runners.
    """
    defaults = _default_arg_names(func, name, owner)
    func_name = str(_safe_getattr(func, "__name__", name))
    for arg in sorted(argnames):
        if arg in fixturenames:
            continue
        if arg in defaults:
            raise CollectionRefusal(
                f"In {func_name}: function already takes an argument "
                + f"{arg!r} with a default value"
            )
        kind = "fixture" if arg in indirect else "argument"
        raise CollectionRefusal(f"In {func_name}: function uses no {kind} {arg!r}")


def _num_mock_patch_args(func: object) -> int:
    """Port of `_pytest/compat.py::num_mock_patch_args` (l. 88-104).

    ``mock.patch`` records one ``_patch`` object per decorator on the wrapper's
    ``patchings``, and prepends a positional argument for each one that has **no**
    ``attribute_name`` (i.e. is not ``patch.object(..., new_callable=...)`` style naming its
    own target) and whose ``new`` is still the ``DEFAULT`` sentinel (i.e. the user did not
    supply a replacement, so a ``MagicMock`` is created and passed in).  A ``patch`` with an
    explicit ``new=`` injects nothing and must not shift the count.

    Both sentinels are consulted — ``mock.DEFAULT`` and ``unittest.mock.DEFAULT`` — because
    the standalone ``mock`` backport and the stdlib module are different objects, and pytest
    looks each up in ``sys.modules`` rather than importing either.
    """
    patchings = _safe_getattr(func, "patchings", None)
    if not patchings:
        return 0
    sentinels = [
        _safe_getattr(sys.modules.get(module), "DEFAULT", object())
        for module in ("mock", "unittest.mock")
    ]
    return len(
        [
            patching
            for patching in cast(Sequence[object], patchings)
            if not _safe_getattr(patching, "attribute_name", None)
            and any(_safe_getattr(patching, "new", None) is sentinel for sentinel in sentinels)
        ]
    )


def _fixture_names(
    func: object,
    name: str,
    owner: type | None,
    param_names: frozenset[str],
) -> list[str]:
    """Direct fixture parameters, in signature order (the manifest's ``fixtures``).

    Port of `_pytest/compat.py::getfuncargnames` (l. 133-171), which is what pytest
    itself uses to decide what a test *requests*:

    * only ``POSITIONAL_OR_KEYWORD`` and ``KEYWORD_ONLY`` parameters count;
    * **a parameter with a default is not a fixture request** —
      ``and p.default is Parameter.empty``.  ``def test_x(tmp_path, flag=1)`` requests
      only ``tmp_path``; pytest would never try to resolve a fixture named ``flag``.
      Verified against the installed pytest:
      ``getfuncargnames(def test_defaults(tmp_path, flag=1, *, capsys, extra=2))``
      returns ``('tmp_path', 'capsys')``.
    * the bound-method first argument is dropped when the attribute is *not* a
      ``staticmethod`` — pytest decides that with
      ``inspect.getattr_static(cls, name)`` and an explicit comment ("Not using
      `getattr` because we don't want to resolve the staticmethod"), **not** by
      looking at the parameter's name.  So a method written ``def test_m(this)``
      loses ``this``, and a ``@staticmethod`` keeps its first parameter — neither of
      which a ``self``/``cls`` name test gets right.  The guard on
      ``POSITIONAL_ONLY`` is ported with it.

    This is :func:`_requested_argnames` minus the names the *direct* parametrization
    supplies: those come from the decorator, not from a fixture, so reporting them as
    requested fixtures would be a lie on the wire.  An ``indirect=`` name is **not**
    subtracted — it really is resolved through a fixture of that name, which is what
    `_pytest/python.py::Metafunc._get_direct_parametrize_args` encodes by filtering on
    ``"direct"``.
    """
    return [
        argname for argname in _requested_argnames(func, name, owner) if argname not in param_names
    ]


# ---------------------------------------------------------------------------
# core 3: the fixture engine — registry, closure, param expansion, execution
# ---------------------------------------------------------------------------

#: Fixture scopes, narrowest first.  Port of `_pytest/scope.py::Scope`, whose members
#: carry the comment "Scopes need to be listed from lower to higher"; the index into
#: this tuple *is* pytest's ``_SCOPE_INDICES`` and drives both the closure sort
#: (`getfixtureclosure`'s ``sort_by_scope``) and the ScopeMismatch check.
SCOPE_NAMES: Final = ("function", "class", "module", "package", "session")

_SCOPE_INDEX: Final[Mapping[str, int]] = {name: index for index, name in enumerate(SCOPE_NAMES)}

#: Which teardown bucket the worker actually caches each declared scope in.
#:
#: ``package`` and ``session`` collapse onto one worker-lifetime *teardown* bucket.
#: **Documented limitation, not an oversight:** a worker is handed an arbitrary subset of
#: the run's files (``src/v2/collect.rs`` routes by stem hash), so it cannot know when the
#: last test of a package — or of the session — has run anywhere else.  A package fixture
#: is therefore not torn down at the package boundary.
#:
#: **The setup granularity is narrower than that, and narrower than this comment used to
#: claim.**  It said a session fixture "executes once per worker"; measured by
#: ``conformance/corpus/fixtures/session-scope`` at ``-n 1``, a *conftest* session fixture
#: executes once per **file**.  Two tests in one file share it correctly; two files do not.
#: The cause is not this table and not :meth:`FixtureRunner.teardown` — which really does
#: leave the session bucket alone at a module boundary — but :func:`build_registry`, called
#: once per file: the conftest *module* is cached, yet ``parse_factories`` mints a fresh
#: :class:`FixtureDef` from it each time, and :attr:`FixtureRunner._cache` is keyed on
#: ``FixtureDef`` *identity* (``eq=False``, deliberately, so it keys the way pytest's does).
#: Two files present two keys for one fixture and the cache misses.  A yield teardown is
#: then queued once per file as well.  Waived in ``conformance/waivers-v2-run.toml`` with
#: the fix shape: cache ``FixtureDef`` objects per ``(conftest path, fixture name)``, which
#: closes the single-worker case outright and leaves only the genuine cross-worker problem.
#:
#: ``class`` has its own bucket and a real boundary, but the boundary is **caller-driven**:
#: pytest gets it from the collection tree (`SetupState.teardown_exact` unwinds every node
#: the next item does not share), and a worker has no tree, so
#: :meth:`FixtureRunner.note_test_boundary` compares ``CollectedTest.class_name`` instead.
#: :meth:`FixtureRunner.setup` calls it, so a caller cannot forget; a caller that drives the
#: runner some other way must call it, or one class's fixtures leak into the next.
_SCOPE_BUCKET: Final[Mapping[str, str]] = {
    "function": "function",
    "class": "class",
    "module": "module",
    "package": "session",
    "session": "session",
}

#: Teardown buckets, narrowest first — the order :meth:`FixtureRunner.teardown` unwinds.
_BUCKET_ORDER: Final = ("function", "class", "module", "session")


# ---------------------------------------------------------------------------
# asyncio — the loop-scope model, ported from pytest_asyncio/plugin.py (1.2.0)
# ---------------------------------------------------------------------------
#
# pytest-asyncio implements "a loop per scope" as **fixtures**: `_create_scoped_runner_fixture`
# (l. 799-835) registers one `_{scope}_scoped_runner` fixture per `Scope` member, each holding
# an `asyncio.Runner`, and an async test or fixture *requests* the one matching its loop scope
# (l. 459-467, l. 742-743).  The scope of the fixture is the lifetime of the loop, for free.
#
# This worker has the same two ingredients — a scope-keyed cache and per-scope teardown
# buckets — so the port keeps the shape rather than the mechanism: :attr:`FixtureRunner._loops`
# is keyed by scope name and each entry registers its close on the matching bucket
# (:data:`_SCOPE_BUCKET`), which is drained at exactly the boundary the fixture would have
# been finalised at.  Because a runner's close finalizer is pushed *before* the finalizer of
# whatever fixture caused it to exist, the bucket's LIFO drain closes the loop **after** every
# async fixture that ran on it — the same ordering the request graph gives pytest.
#
# The keying is on the **scope name**, not on the teardown bucket: `package` and `session`
# collapse to one bucket here (see :data:`_SCOPE_BUCKET`) but pytest-asyncio has two distinct
# runner fixtures, so two distinct loops.  Sharing the bucket costs the `package` loop a
# too-late close, which is the already-documented package-scope divergence; sharing the *loop*
# would have made `id(get_running_loop())` compare equal across two scopes pytest keeps apart.

#: pytest-asyncio's default `asyncio_default_test_loop_scope` (plugin.py l. 123-128).
DEFAULT_ASYNCIO_TEST_LOOP_SCOPE: Final = "function"

#: rustest's default `asyncio_mode`.  pytest-asyncio's is ``strict``; the reasoning for the
#: difference lives with the config that produces it (``src/v2/config.rs::
#: DEFAULT_ASYNCIO_MODE``) and is measured by ``conformance/corpus/async/mode-default``.
DEFAULT_ASYNCIO_MODE: Final = "auto"

#: The kwargs ``@mark.asyncio`` accepts.
#:
#: pytest-asyncio's ``_get_marked_loop_scope`` (l. 763-781) allows ``{"loop_scope", "scope"}``
#: and raises ``ValueError`` for anything else.  ``timeout`` is added because it is **rustest's
#: own** documented extension — v1's ``decorators.py::asyncio`` accepts it and
#: ``src/execution.rs`` (l. 825-830) applies it — and ``docs/guide/async-testing.md`` names it
#: as the thing "pytest-asyncio lacks out of the box".  Dropping it would have broken a
#: shipped feature to gain conformance on an error message for a keyword pytest-asyncio has no
#: meaning for.
_ASYNCIO_MARK_KWARGS: Final = frozenset({"loop_scope", "scope", "timeout"})

#: `plugin.py::_DUPLICATE_LOOP_SCOPE_DEFINITION_ERROR` (l. 752-755), verbatim.
_DUPLICATE_LOOP_SCOPE_ERROR: Final = (
    'An asyncio pytest marker defines both "scope" and "loop_scope", '
    'but it should only use "loop_scope".\n'
)


@dataclass(frozen=True)
class AsyncioConfig:
    """The three ``asyncio_*`` ini values, as ``init`` delivered them.

    Immutable and passed by value because it is read from two halves of the worker that must
    not be able to disagree: collection consults :attr:`mode` to decide whether an async
    generator test is xfailed, and execution consults all three to decide which loop a
    coroutine runs on.
    """

    #: ``auto`` or ``strict``; validated orchestrator-side (``src/v2/config.rs``).
    mode: str = DEFAULT_ASYNCIO_MODE
    #: ``None`` when unset, and that is a **third answer**, not a synonym for ``"function"``:
    #: `plugin.py::pytest_fixture_setup` l. 736-741 resolves an async fixture's loop scope as
    #: ``mark ?? this ?? fixturedef.scope``, so an unset option leaves a ``scope="module"``
    #: async fixture on a *module*-scoped loop.
    default_fixture_loop_scope: str | None = None
    #: Always set — the oracle gives it the real default ``"function"``.
    default_test_loop_scope: str = DEFAULT_ASYNCIO_TEST_LOOP_SCOPE


#: The configuration a :class:`FixtureRunner` built without one uses.
_DEFAULT_ASYNCIO: Final = AsyncioConfig()


def _is_asyncio_fixture_function(func: object) -> bool:
    """Port of `plugin.py::_is_asyncio_fixture_function` (l. 186-188).

    The attribute is set by ``pytest_asyncio.fixture`` (l. 191-197, via
    ``_make_asyncio_fixture_function``) and by nothing else — in particular **not** by
    ``pytest.fixture``/``rustest.fixture``, which is the whole distinction ``strict`` mode
    turns on.  ``rustest/compat/pytest_asyncio.py`` sets it for the shimmed decorator.
    """
    func = getattr(func, "__func__", func)
    return bool(_safe_getattr(func, "_force_asyncio_fixture", False))


def _is_coroutine_or_asyncgen(func: object) -> bool:
    """Port of `plugin.py::_is_coroutine_or_asyncgen` (l. 199-200)."""
    return inspect.iscoroutinefunction(func) or inspect.isasyncgenfunction(func)


def _asyncio_mark(marks: Sequence[MarkSpec]) -> MarkSpec | None:
    """The closest ``asyncio`` mark, or ``None`` — pytest's ``get_closest_marker``.

    :func:`_mark_specs` already emits function marks before class-chain marks before module
    marks, which is ``iter_markers`` order, so "first match" is "closest".
    """
    return next((mark for mark in marks if mark.name == "asyncio"), None)


def _marked_loop_scope(mark: MarkSpec, default_loop_scope: str) -> str:
    """Port of `plugin.py::_get_marked_loop_scope` (l. 763-781), errors included.

    Three rules, each with a probe under it:

    * a positional argument, or a keyword outside :data:`_ASYNCIO_MARK_KWARGS`, is a
      ``ValueError`` naming the only keyword the mark takes;
    * ``scope=`` is a deprecated alias for ``loop_scope=`` and **both together** is an error
      (l. 771-774; pytest-asyncio raises ``pytest.UsageError``, which this raises as a
      ``ValueError`` carrying the same message — both land in the setup phase and are
      reported as the same ``error`` outcome, and importing pytest here is forbidden).
      pytest-asyncio also warns for the alias alone, which this worker has no channel for
      (the documented no-warnings gap);
    * an absent (or falsy) scope falls back to ``asyncio_default_test_loop_scope``.

    The final ``assert scope in {...}`` is reproduced as a real error rather than an
    ``assert``: a mark carrying ``loop_scope="sesion"`` must not depend on ``-O`` to be
    caught, and "loop scope silently became the default" is precisely the silent-wrong-answer
    shape the rest of this module refuses.
    """
    if mark.args or (set(mark.kwargs) - _ASYNCIO_MARK_KWARGS):
        raise ValueError("mark.asyncio accepts only a keyword argument 'loop_scope'.")
    if "scope" in mark.kwargs and "loop_scope" in mark.kwargs:
        raise ValueError(_DUPLICATE_LOOP_SCOPE_ERROR)
    scope = mark.kwargs.get("loop_scope") or mark.kwargs.get("scope") or default_loop_scope
    if scope not in _SCOPE_INDEX:
        raise ValueError(
            f"{scope!r} is not a valid loop_scope. Valid scopes are: {', '.join(SCOPE_NAMES)}."
        )
    return cast(str, scope)


def _apply_contextvar_changes(
    context: contextvars.Context,
) -> Callable[[], None] | None:
    """Port of `plugin.py::_apply_contextvar_changes` (l. 385-414), verbatim in behaviour.

    Copies whatever *context* changed into the **current** context and returns an undo, or
    ``None`` when nothing changed.

    The oracle's own comment says why a fixture author cannot do this themselves: "the author
    of the fixture can't write such a finalizer because they have no way to capture the
    Context in which the setup function was run" (l. 368-375).

    **It is what makes a fixture's ``ContextVar`` reach the test that requested it**, and it
    became load-bearing here the moment test bodies got their own context
    (:func:`_consume_test_result`). Before that, fixtures and tests shared the runner's
    context and the reachability was accidental; isolating the test body alone broke it —
    measured, a session fixture setting a ``ContextVar`` went from ``from-fixture`` to
    ``unset`` where pytest keeps saying ``from-fixture``. The two halves are one change.

    ``var.get() is context.get(var)`` compares identity, not equality, exactly as the oracle
    does: a fixture that reassigns a ``ContextVar`` to an equal-but-distinct object has still
    changed it, and a ``__eq__`` that raises must not take the teardown down with it.
    """
    context_tokens: list[tuple[contextvars.ContextVar[Any], contextvars.Token[Any]]] = []
    for var in context:
        try:
            if var.get() is context.get(var):
                # Unmodified, so leave it as-is.
                continue
        except LookupError:
            # Not yet set in the current context at all.
            pass
        context_tokens.append((var, var.set(context.get(var))))

    if not context_tokens:
        return None

    def restore_contextvars() -> None:
        while context_tokens:
            var, token = context_tokens.pop()
            var.reset(token)

    return restore_contextvars


def _default_event_loop_policy() -> object:
    """Port of `plugin.py::_get_event_loop_policy` (l. 634-637), warning filter included.

    ``asyncio.get_event_loop_policy`` is deprecated from 3.12 and emits a
    ``DeprecationWarning`` the plugin suppresses; a suite running with ``-W error`` would
    otherwise fail inside rustest's own machinery rather than in anything it wrote.  On an
    interpreter that has removed the API the answer is ``None``, which
    :func:`_temporary_event_loop_policy` treats as "leave the policy alone" — the loop is
    then built by whatever the interpreter's default is, which is the same loop pytest-asyncio
    would end up with once the policy concept is gone.
    """
    import asyncio

    getter = _safe_getattr(asyncio, "get_event_loop_policy", None)
    if getter is None:  # pragma: no cover - 3.16+, where policies are removed
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return cast(Callable[[], object], getter)()


@contextlib.contextmanager
def _temporary_event_loop_policy(policy: object) -> Iterator[None]:
    """Port of `plugin.py::_temporary_event_loop_policy` (l. 619-631).

    The policy is installed only for as long as it takes to *build* the loop, and the previous
    policy **and the previous current-loop** are both restored afterwards.  Restoring the loop
    matters as much as restoring the policy: ``set_event_loop_policy`` resets the policy's
    idea of the current loop, so without the restore a nested scope's loop creation would
    detach the outer scope's loop from ``asyncio.get_event_loop()`` and a library that calls
    it mid-test would build a second one.
    """
    import asyncio

    if policy is None:  # pragma: no cover - 3.16+, where policies are removed
        yield
        return
    old_policy = _default_event_loop_policy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            old_loop = asyncio.get_event_loop()
        except RuntimeError:
            old_loop = None
        asyncio.set_event_loop_policy(cast(Any, policy))
        try:
            yield
        finally:
            asyncio.set_event_loop_policy(cast(Any, old_policy))
            asyncio.set_event_loop(old_loop)


def _asyncio_timeout(mark: MarkSpec | None) -> float | None:
    """rustest's own ``@mark.asyncio(timeout=...)``, or ``None``.

    **Not from the oracle** — pytest-asyncio has no timeout at all, which is exactly what
    ``docs/guide/async-testing.md`` advertises rustest as adding.  The semantics ported are
    v1's: ``src/execution.rs`` l. 825-830 reads the kwarg off the mark and
    ``async_executor.py::_wrap_test_for_gather`` l. 58-60 applies it as
    ``asyncio.wait_for(coro, timeout)``, so a test that overruns is *cancelled* rather than
    left running, and the message is v1's.

    A non-numeric or non-positive value is rejected by ``decorators.py::asyncio`` at decoration
    time; this re-checks because a mark can also arrive through ``pytest.mark.asyncio``, which
    performs no validation, and a string timeout would otherwise surface as a ``TypeError``
    from deep inside ``asyncio``.
    """
    if mark is None:
        return None
    raw = mark.kwargs.get("timeout")
    if raw is None:
        return None
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise ValueError(f"asyncio mark timeout must be a number, got {raw!r}")
    if raw <= 0:
        raise ValueError(f"asyncio mark timeout must be positive, got {raw!r}")
    return float(raw)


#: Builtin fixtures this worker provides — :mod:`rustest._v2_builtins`, which owns both the
#: implementations and the order they are registered in.  Mirrored here because it is part of
#: this module's public surface (the "supported builtins" list a lookup failure prints).
BUILTIN_FIXTURES: Final = tuple(_builtins.V2_BUILTIN_FIXTURES)

#: Fixtures real pytest provides that this worker does not yet.  Requesting one is an
#: error with its own wording rather than pytest's ``not found``: "not found" would send
#: an operator hunting for a missing ``@fixture`` that was never theirs to write.
#:
#: Phase 3 Task 2 emptied most of it — ``cache``, ``capfd``, ``caplog``, ``mocker``,
#: ``pytestconfig``, ``tmpdir`` and ``tmpdir_factory`` all moved into
#: :data:`BUILTIN_FIXTURES`; Phase 4 Task 1c moved ``recwarn`` (MECHANISM M5), whose entry
#: here claimed it "needs a warnings channel, which the v2 wire does not have" — it does
#: not, because it records **in-process** and tells the orchestrator nothing.  What is left
#: is genuinely unimplemented, and each entry is a distinct piece of machinery rather than a
#: variation on one that exists:
#:
#: * the ``*binary`` capture pair and ``capteesys`` need a bytes-flavoured capture class and,
#:   for ``capteesys``, a *tee* — output both captured and passed through;
#: * ``pytester``/``testdir`` are pytest's own in-process test harness;
#: * ``record_property`` and friends write JUnit XML attributes, and there is no XML report;
#: * ``doctest_namespace`` belongs to a doctest collector this engine does not have.
UNSUPPORTED_BUILTIN_FIXTURES: Final = frozenset(
    {
        "capfdbinary",
        "capsysbinary",
        "capteesys",
        "doctest_namespace",
        "pytester",
        "record_property",
        "record_testsuite_property",
        "record_xml_attribute",
        "testdir",
    }
)


class FixtureLookupError(Exception):
    """A fixture a test requested cannot be resolved.

    Carries pytest's own message shape (`_pytest/fixtures.py::FixtureLookupError.formatrepr`
    l. 861-877), so an operator greps for the same string they would under pytest.  The
    execute half reports this as status ``error`` — pytest reports an unresolvable fixture
    as a setup **error**, never as a failure.
    """


class ScopeMismatch(Exception):
    """A wider-scoped fixture requested a narrower-scoped one.

    Port of `_pytest/fixtures.py::FixtureRequest._check_scope`.  Allowing it would cache
    the narrow fixture for the wide fixture's lifetime, which is a silently wrong result
    rather than an error — exactly the class of defect this worker refuses to ship.
    """


@dataclass(eq=False)
class FixtureDef:
    """One registered fixture.  Port of `_pytest/fixtures.py::FixtureDef`'s data half.

    ``eq=False`` keeps dataclass identity semantics, so an instance is hashable and can
    key the runtime cache the way pytest keys off the ``FixtureDef`` object itself.

    ``params`` is ``(id, value)`` pairs, already de-duplicated by
    :func:`_unique_parameterset_ids`; ``None`` means the fixture is not parametrized
    (pytest's ``fixturedef.params is not None`` test in ``pytest_generate_tests``).
    """

    name: str
    func: Callable[..., object]
    scope: str
    #: `(id, value, marks)` per parameter — the marks being those of a
    #: `pytest.param(..., marks=...)` inside `params=`, which pytest carries because
    #: `FixtureManager.pytest_generate_tests` hands `fixturedef.params` to
    #: `metafunc.parametrize` as ordinary parameter sets.
    params: tuple[tuple[str, object, tuple["MarkSpec", ...]], ...] | None
    autouse: bool
    baseid: str
    argnames: tuple[str, ...]
    #: True for a plain ``def`` in a test-class body, whose first parameter is ``self``.
    #: :meth:`FixtureRunner._call` binds it to the test's instance, as pytest's
    #: ``resolve_fixture_function`` does.  False for module-level fixtures and for
    #: ``staticmethod``/``classmethod`` ones, which need no instance.
    needs_instance: bool = False


def _fixture_param_cases(
    func: object, fixture_name: str
) -> tuple[tuple[str, object, tuple[MarkSpec, ...]], ...] | None:
    """Read v1's ``__rustest_fixture_params__`` into ``(id, value)`` pairs.

    The ids are v1's, consumed verbatim, exactly as :func:`_parametrization` consumes the
    ``@parametrize`` ids — produced by ``decorators.py::_build_fixture_params`` ->
    ``_resolve_case_id``.  The same divergence table applies (v1 matches
    `_pytest/python.py::IdMaker` for int/float/bool/None/short-str and explicit ids,
    differs for long strings, non-ASCII, bytes, complex, enums and containers).

    :func:`_unique_parameterset_ids` is applied here because v1 does **not** apply it to
    fixture params: pytest de-duplicates per ``parametrize`` call
    (`IdMaker.make_unique_parameterset_ids`), and ``params=[1, 1]`` would otherwise
    collect two tests sharing one nodeid.

    ``params=[]`` is treated as *not parametrized*.  pytest instead generates one call
    marked with ``empty_parameter_set_mark`` (skip by default); the worker has no
    skip-injection stage until Task 3, and a silently-dropped test would be worse than a
    documented gap.
    """
    raw = _safe_getattr(func, "__rustest_fixture_params__", None)
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raise CollectionRefusal(
            f"malformed rustest fixture params on {fixture_name!r}: "
            + f"expected a sequence of cases, got {raw!r}"
        )
    ids: list[str] = []
    values: list[object] = []
    marksets: list[tuple[MarkSpec, ...]] = []
    for case in cast(Sequence[object], raw):
        if not isinstance(case, dict):
            raise CollectionRefusal(
                f"malformed rustest fixture params on {fixture_name!r}: "
                + f"expected a case dict, got {case!r}"
            )
        entry = cast(Mapping[str, object], case)
        ids.append(str(entry.get("id", "")))
        values.append(entry.get("value"))
        raw_marks = entry.get("marks", ())
        marksets.append(
            tuple(_spec_from_mark_dict(item, func) for item in cast(Sequence[object], raw_marks))
            if isinstance(raw_marks, (list, tuple))
            else ()
        )
    if not ids:
        return None
    return tuple(zip(_unique_parameterset_ids(ids), values, marksets, strict=True))


def _call_with_optional_argument(func: Callable[..., object], arg: object) -> None:
    """Port of `_pytest/python.py::_call_with_optional_argument` (l. 682-689).

    xunit hooks may take the collected object or take nothing: ``def setup_module()`` and
    ``def setup_module(module)`` are both legal, and pytest decides by counting the
    signature's parameters rather than by trying and catching a ``TypeError`` -- which would
    swallow a genuine ``TypeError`` raised *inside* a correct one-argument hook.
    """
    numargs = len(_requested_argnames(func, getattr(func, "__name__", "hook"), None))
    if numargs:
        _ = func(arg)
    else:
        _ = func()


def _get_first_non_fixture_func(
    holder: object, names: Sequence[str]
) -> Callable[..., object] | None:
    """Port of `_pytest/python.py::_get_first_non_fixture_func` (l. 674-679).

    A name that has been turned into a *fixture* is not an xunit hook -- pytest looks past
    it rather than calling it twice, and rustest's marker for that is
    ``__rustest_fixture__`` where pytest's is ``FixtureFunctionDefinition``.
    """
    for name in names:
        meth = _safe_getattr(holder, name, None)
        if meth is not None and not _safe_getattr(meth, "__rustest_fixture__", False):
            return cast("Callable[..., object]", meth)
    return None


def _xunit_fixturedefs(
    holder: object,
    baseid: str,
    *,
    kind: str,
) -> list[FixtureDef]:
    """The xunit setup/teardown hooks of *holder*, as autouse fixtures.

    Port of `_pytest/python.py`'s four ``_register_setup_*_fixture`` methods (l. 559-627 for
    the module pair, l. 776-839 for the class pair). pytest injects each as an **autouse
    fixture** at the right scope rather than calling it from a bespoke hook, "so we play
    nicely and unsurprisingly with other fixtures (#517)" -- and doing the same here means
    ordering, teardown-on-error and scope caching are the ones this worker already has.

    Nothing is registered when neither half exists, which is what keeps a suite that uses no
    xunit hooks byte-identical to before.

    Ported faithfully, including the two rules that are easy to miss:

    * ``setup_function``/``teardown_function`` **stand down inside a class** --
      ``if request.instance is not None: yield; return`` (l. 607-612) -- because
      ``setup_method`` owns that case;
    * the hooks are looked up through :func:`_get_first_non_fixture_func`, so a name the
      suite turned into a fixture is not also called as a hook.

    **Not ported:** ``setUpModule``/``tearDownModule`` are accepted alongside
    ``setup_module``/``teardown_module`` (pytest checks both, in that order), but
    ``unittest.TestCase``'s own ``setUp``/``tearDown`` are not -- those already run through
    the unittest path.
    """
    arg_of: Callable[[object], object]
    if kind == "module":
        setup = _get_first_non_fixture_func(holder, ("setUpModule", "setup_module"))
        teardown = _get_first_non_fixture_func(holder, ("tearDownModule", "teardown_module"))
        scope = "module"
        arg_of = lambda request: _safe_getattr(request, "module", None)  # noqa: E731
    elif kind == "function":
        setup = _get_first_non_fixture_func(holder, ("setup_function",))
        teardown = _get_first_non_fixture_func(holder, ("teardown_function",))
        scope = "function"
        arg_of = lambda request: _safe_getattr(request, "function", None)  # noqa: E731
    elif kind == "class":
        setup = _get_first_non_fixture_func(holder, ("setup_class",))
        teardown = _get_first_non_fixture_func(holder, ("teardown_class",))
        scope = "class"
        arg_of = lambda request: _safe_getattr(request, "cls", None)  # noqa: E731
    else:  # "method"
        setup = _get_first_non_fixture_func(holder, ("setup_method",))
        teardown = _get_first_non_fixture_func(holder, ("teardown_method",))
        scope = "function"
        arg_of = lambda request: _safe_getattr(request, "function", None)  # noqa: E731
    if setup is None and teardown is None:
        return []

    is_method = kind == "method"

    def xunit_fixture(request: object) -> Iterator[None]:
        instance = _safe_getattr(request, "instance", None)
        if kind == "function" and instance is not None:
            # Bound to an instance: `setup_method` owns this one (pytest l. 607-612).
            yield
            return
        arg = arg_of(request)
        if setup is not None:
            # A method hook is re-read off the *instance* so it binds to this test's object,
            # which is what `getattr(instance, setup_name)` does in pytest (l. 825).
            bound = _safe_getattr(instance, setup.__name__, setup) if is_method else setup
            _call_with_optional_argument(cast("Callable[..., object]", bound), arg)
        yield
        if teardown is not None:
            bound = _safe_getattr(instance, teardown.__name__, teardown) if is_method else teardown
            _call_with_optional_argument(cast("Callable[..., object]", bound), arg)

    label = _safe_getattr(holder, "__qualname__", None) or _safe_getattr(holder, "__name__", kind)
    return [
        FixtureDef(
            # pytest's own "use a unique name to speed up lookup" naming, so two classes in
            # one module cannot collide and neither can shadow a user fixture.
            name=f"_xunit_setup_{kind}_fixture_{label}",
            func=xunit_fixture,
            scope=scope,
            params=None,
            autouse=True,
            baseid=baseid,
            argnames=("request",),
            needs_instance=False,
        )
    ]


class FixtureRegistry:
    """The fixtures visible to one file: builtins, its conftest chain, its own module.

    Port of `_pytest/fixtures.py::FixtureManager`'s ``_arg2fixturedefs`` /
    ``_nodeid_autousenames`` pair, with one deliberate simplification: pytest stores every
    fixture in the session and filters per node with ``_matchfactories`` (l. 1872-1878,
    "``fixturedef.baseid in parentnodeids``"), because one session holds every file's
    fixtures at once.  A worker builds a registry *per file*, from exactly that file's
    ancestry, so every entry is applicable by construction and the filter is a no-op.
    ``baseid`` is still recorded — it is what a future session-wide registry would filter
    on, and it is the only thing that says where a fixture came from.

    Registration order is furthest-to-nearest (builtins, rootdir conftest, ..., nearest
    conftest, module, class), which is what makes ``defs[-1]`` the *nearest* definition —
    pytest's own convention: "``fixturedefs`` is sorted from furthest to closest, so use
    negative indexing to go in reverse" (`_get_active_fixturedef` l. 601-603).
    """

    def __init__(self) -> None:
        super().__init__()
        self._name2defs: dict[str, list[FixtureDef]] = {}
        self._autouse: list[str] = []

    def register(self, fixturedef: FixtureDef) -> None:
        """Port of `_pytest/fixtures.py::FixtureManager._register_fixture` (l. 1758-1769).

        pytest's ``has_location`` split (locationless plugin fixtures inserted *before*
        conftest fixtures) does not apply: every fixture this worker registers has a
        location, and the builtins are registered first anyway, which is the position
        that split exists to produce.
        """
        self._name2defs.setdefault(fixturedef.name, []).append(fixturedef)
        if fixturedef.autouse and fixturedef.name not in self._autouse:
            self._autouse.append(fixturedef.name)

    @property
    def autouse_names(self) -> tuple[str, ...]:
        """Autouse fixture names, outermost registration first.

        Matches `_getautousenames` (l. 1606-1611), which walks ``node.listchain()`` —
        session, package, module, class — and yields each level's names in turn, then
        ``deduplicate_names`` (l. 1494-1497) keeps the first sighting.  Registering in
        chain order and skipping repeats gives the identical tuple.
        """
        return tuple(self._autouse)

    def getfixturedefs(self, name: str) -> tuple[FixtureDef, ...] | None:
        """Port of `getfixturedefs` (l. 1852-1870): ``None`` when the name is unknown."""
        defs = self._name2defs.get(name)
        return tuple(defs) if defs else None

    def names(self) -> tuple[str, ...]:
        """Every registered fixture name — the "available fixtures" line of an error."""
        return tuple(self._name2defs)

    def child(self) -> FixtureRegistry:
        """A copy that further registrations do not leak back into.

        Used for class bodies: pytest registers a class's fixtures at the class nodeid
        (`_pytest/python.py::Class.collect` l. 772,
        ``parsefactories(self.newinstance(), self.nodeid)``), so they are invisible to the
        module-level tests that were collected before the class.
        """
        clone = FixtureRegistry()
        clone._name2defs = {name: list(defs) for name, defs in self._name2defs.items()}
        clone._autouse = list(self._autouse)
        return clone

    def parse_factories(self, holder: object, baseid: str, owner: type | None = None) -> None:
        """Port of `_pytest/fixtures.py::FixtureManager.parsefactories` (l. 1786-1850).

        ``dir(holder)`` is pytest's own traversal, and it is **sorted** — so two autouse
        fixtures in one conftest run in alphabetical order, not definition order.  Probed:
        that is real pytest behaviour, not an artefact.

        Two rustest-specific substitutions, both from ``decorators.py::fixture``:
        pytest's ``type(obj_ub) is FixtureFunctionDefinition`` test becomes the
        ``__rustest_fixture__`` marker, and ``marker.name or name`` becomes
        ``__rustest_fixture_name__ or name``.  The rest — scope, params, ids, autouse —
        maps attribute for attribute, including through the compat shim, which forwards
        ``pytest.fixture`` straight into ``rustest.fixture`` (``compat/pytest.py`` l. 586-642).

        A fixture *imported* into a module is registered at the module's baseid, exactly as
        under pytest: ``dir()`` does not distinguish defined from imported names.
        """
        for name in dir(holder):
            obj = _safe_getattr(holder, name, None)
            if not _has_fixture_marker(obj):
                continue
            declared = _safe_getattr(obj, "__rustest_fixture_name__", None)
            fixture_name = declared if isinstance(declared, str) and declared else name
            scope = _safe_getattr(obj, "__rustest_fixture_scope__", "function")
            if not isinstance(scope, str) or scope not in _SCOPE_INDEX:
                raise CollectionRefusal(
                    f"fixture {fixture_name!r} has an unknown scope {scope!r}; "
                    + f"expected one of {', '.join(SCOPE_NAMES)}"
                )
            self.register(
                FixtureDef(
                    name=fixture_name,
                    func=cast(Callable[..., object], obj),
                    scope=scope,
                    params=_fixture_param_cases(obj, fixture_name),
                    autouse=_safe_getattr(obj, "__rustest_fixture_autouse__", False) is True,
                    baseid=baseid,
                    argnames=tuple(_requested_argnames(obj, name, owner)),
                    needs_instance=owner is not None
                    and inspect.isfunction(inspect.getattr_static(owner, name, None)),
                )
            )


@dataclass(frozen=True)
class FixtureClosure:
    """The result of `getfixtureclosure`: every name a test transitively needs.

    ``names`` is scope-sorted (widest first), which is both pytest's instantiation order
    and — because ``pytest_generate_tests`` walks it in order — the order the parametrized
    fixtures' id components appear in a nodeid.
    """

    names: tuple[str, ...]
    arg2defs: Mapping[str, tuple[FixtureDef, ...]]
    registry: FixtureRegistry


def usefixtures_names(marks: Sequence[MarkSpec]) -> list[str]:
    """The names every ``usefixtures`` mark on a test contributes, closest-first.

    Port of `_pytest/fixtures.py::FixtureManager._getusefixturesnames` (l. 1613-1626):
    ``for marker_node, mark in node.iter_markers_with_node(name="usefixtures"): yield from
    mark.args``.  *marks* arrives in ``iter_markers`` order already (:func:`_mark_specs`
    emits function, then the class chain, then module), so the ordering is inherited rather
    than re-derived.

    **Divergence, deliberate:** pytest additionally *warns* for a bare ``@usefixtures``
    (``usefixtures() in <nodeid> without arguments has no effect``).  The protocol has no
    warnings channel, so an argument-less mark contributes nothing and says nothing — the
    same observable effect on the run, one fewer line of output.  Recorded in the module
    docstring's scope-limit list.
    """
    return [str(arg) for mark in marks if mark.name == "usefixtures" for arg in mark.args]


def build_closure(
    registry: FixtureRegistry,
    argnames: Sequence[str],
    ignore_args: AbstractSet[str] = frozenset(),
    usefixtures: Sequence[str] = (),
) -> FixtureClosure:
    """Port of `_pytest/fixtures.py::FixtureManager.getfixtureclosure` (l. 1624-1664).

    The initial set is ``deduplicate_names(autousenames, usefixturesnames, argnames)``
    (`getfixtureinfo` l. 1568-1570) — **autouse first**, which is why an autouse
    parametrized fixture contributes the *leftmost* id component even for a test that
    never mentions it (probed: ``test_auto_only[1]``/``[2]``) — then ``usefixtures``, then
    the requested names.  The middle slot is load-bearing for ids in exactly the same way:
    a parametrized fixture pulled in by ``usefixtures`` contributes its id component ahead
    of one the signature asks for.

    The fixpoint loop is pytest's verbatim, including iterating the list *while appending
    to it* — that is load-bearing, since a dependency discovered mid-pass must be resolved
    in the same pass to keep the append order (and therefore the id-component order)
    identical to pytest's.

    ``ignore_args`` are the names the test parametrizes directly
    (`_get_direct_parametrize_args`): they stay in the closure but are never resolved, so
    a fixture of the same name is shadowed rather than double-parametrized.

    The final ``sort(key=sort_by_scope, reverse=True)`` is a **stable** sort in both
    languages, so same-scope names keep their discovery order; a name with no fixturedef
    sorts as ``Function`` (pytest's ``except KeyError`` branch).
    """
    closure = list(dict.fromkeys([*registry.autouse_names, *usefixtures, *argnames]))
    arg2defs: dict[str, tuple[FixtureDef, ...]] = {}
    lastlen = -1
    while lastlen != len(closure):
        lastlen = len(closure)
        for argname in closure:  # noqa: B020 - appends during iteration are pytest's algorithm
            if argname in ignore_args or argname in arg2defs:
                continue
            defs = registry.getfixturedefs(argname)
            if defs:
                arg2defs[argname] = defs
                for arg in defs[-1].argnames:
                    if arg not in closure:
                        closure.append(arg)

    def sort_by_scope(argname: str) -> int:
        defs = arg2defs.get(argname)
        return _SCOPE_INDEX[defs[-1].scope] if defs else 0

    closure.sort(key=sort_by_scope, reverse=True)
    return FixtureClosure(tuple(closure), arg2defs, registry)


def fixture_param_dimensions(
    closure: FixtureClosure,
    direct_argnames: AbstractSet[str],
) -> list[tuple[str, tuple[tuple[str, object, tuple[MarkSpec, ...]], ...]]]:
    """Which closure fixtures parametrize the test, in pytest's order.

    Port of `_pytest/fixtures.py::FixtureManager.pytest_generate_tests` (l. 1666-1710):
    walk ``metafunc.fixturenames`` (= :attr:`FixtureClosure.names`); skip a name the test
    parametrizes itself ("If the test itself parametrizes using this argname, give it
    precedence"); otherwise walk the fixturedefs **from nearest to furthest**
    (``for fixturedef in reversed(fixture_defs)``) and take the first parametrized one,
    stopping early at an override that does not request its own super fixture (pytest
    #1953 — "if the fixture overrides another fixture, while requesting the super fixture,
    keep going in case the super fixture is parametrized").

    The returned order is the order the id components appear in the nodeid, because
    ``metafunc.parametrize`` appends to ``CallSpec2._idlist`` in call order.  Probed
    against pytest 8.4.2 for every ordering that matters — two fixtures
    (``test_two_fixtures[1-a]``, signature order irrelevant), scope sorting
    (``test_scope_order[m1-1-f1]``: module fixture first), autouse (leftmost), and a
    fixture-plus-``@parametrize`` mix (``test_mixed[1-7]``: **fixture components precede
    direct ones**, because pluggy calls the last-registered ``pytest_generate_tests``
    first and ``FixtureManager`` is registered after the ``python`` plugin).
    """
    dimensions: list[tuple[str, tuple[tuple[str, object, tuple[MarkSpec, ...]], ...]]] = []
    for argname in closure.names:
        defs = closure.arg2defs.get(argname)
        if not defs or argname in direct_argnames:
            continue
        for fixturedef in reversed(defs):
            if fixturedef.params is not None:
                dimensions.append((argname, fixturedef.params))
                break
            if argname not in fixturedef.argnames:
                break
    return dimensions


@dataclass(frozen=True)
class ExecutionPlan:
    """Everything the execute half needs to run one collected test.

    Worker-local and never serialised: the manifest carries ids, this carries the live
    objects behind one id.  Built during collection because the closure has to be resolved
    then anyway (it is what expands parametrized fixtures into separate ids), and
    recomputing it at execute time could only drift.
    """

    id: str
    path: Path
    module: types.ModuleType
    parts: tuple[str, ...]
    func: Callable[..., object] | None
    owner: type | None
    closure: FixtureClosure
    fixture_params: Mapping[str, object]
    direct_params: Mapping[str, object]
    argnames: tuple[str, ...]
    marks: tuple[MarkSpec, ...]
    unittest_case: type | None = None
    unittest_method: str | None = None

    @property
    def class_name(self) -> str | None:
        """The class chain this test belongs to, or ``None`` at module level.

        Identical to ``CollectedTest.class_name`` on the wire.  It is what marks a
        **class-scope boundary**: :meth:`FixtureRunner.note_test_boundary` compares it
        against the previous test's and tears class scope down when it changes.
        """
        return ".".join(self.parts[:-1]) if len(self.parts) > 1 else None


class _ItemNode:
    """The collection-tree node ``request.node`` returns — pytest's ``Function`` item.

    v2 has no collection tree (the manifest replaced it), so this is a **façade** over the
    :class:`ExecutionPlan` carrying the attributes real conftests actually read.  It exists
    because "no tree" is an implementation choice and ``request.node.name`` is a public
    pytest API: rustest's own ``tests/test_conftest_nested`` uses it in an autouse fixture,
    and 21 of its tests were erroring on ``'SubRequest' object has no attribute 'node'``.

    What is *not* here is as deliberate as what is: no ``session``, no ``config``, no
    ``parent``, no ``listchain`` — each would need a tree, and returning a stub that answers
    plausibly would be worse than an ``AttributeError`` that names the missing feature.

    ``own_markers`` is a live list, which is what makes :meth:`add_marker` observable to
    ``get_closest_marker``.  Marks added at *run* time do not retroactively change the
    outcome, because pytest evaluates ``skip``/``xfail`` in ``pytest_runtest_setup``
    (`_pytest/skipping.py`, ``tryfirst``) — before fixtures, let alone before the body.  A
    ``request.applymarker(pytest.mark.skipif(...))`` from a test body is therefore
    bookkeeping under pytest too, which is exactly what the self-suite asserts.
    """

    def __init__(self, plan: ExecutionPlan, instance: object | None) -> None:
        super().__init__()
        self._plan = plan
        #: pytest's ``Item.name`` is the last ``::`` segment, parametrization included
        #: (``test_add[1-2]``); ``nodeid`` is the whole thing.
        self.name: Final = plan.id.rsplit("::", 1)[-1]
        self.nodeid: Final = plan.id
        self.path: Final = plan.path
        #: ``fspath`` is pytest's deprecated ``py.path`` spelling, kept as the same value so
        #: an old conftest reading it gets a path rather than an ``AttributeError``.
        self.fspath: Final = plan.path
        self.cls: Final = plan.owner
        self.module: Final = plan.module
        #: pytest's ``Function.function`` is ``self.obj``, which for a ``TestCaseFunction``
        #: is the **bound method on the instance** (`_pytest/python.py::PyobjMixin.obj` ->
        #: ``getattr(self.instance, self.name)``), not a plain function. A ``TestCase`` plan
        #: carries no ``func``, so it is read off the instance here -- which is what makes
        #: ``setup_method(self, request.function)`` receive a method rather than ``None``.
        self.function: Final = (
            _safe_getattr(instance, plan.unittest_method, None)
            if plan.unittest_case is not None and plan.unittest_method is not None
            else plan.func
        )
        self.instance: Final = instance
        self.own_markers: list[MarkSpec] = list(plan.marks)

    @property
    def originalname(self) -> str:
        """`_pytest/python.py::Function.originalname` — the name without the ``[params]``."""
        return self._plan.parts[-1] if self._plan.parts else self.name

    @property
    def keywords(self) -> Mapping[str, object]:
        """A read-only stand-in for pytest's ``Node.keywords`` chain-map.

        Real ``keywords`` is a `NodeKeywords` that also carries the node's name and every
        parent's; this carries the mark names, the node name and — as of Phase 3 Task 2 —
        the names of the enclosing class and module, which is what completes the answer for
        the check people actually write, ``"slow" in item.keywords``, when the mark is on the
        class.  Marks already arrive with the class and module chain folded in
        (:func:`_mark_specs`), so this only adds the *node names* pytest's chain-map carries.

        Probed against pytest 8.4.2 for a ``@pytest.mark.slow class TestGroup`` in
        ``test_kw.py``: pytest answers ``['', 'kw', 'TestGroup', 'slow', 'test_inside',
        'test_kw.py']``.  The two this does not carry are the **session**'s name (``""``) and
        the **directory** node's (``'kw'``), because both are tree nodes and there is no tree
        — and neither is a name anything keys on: `-k` matches against these, and matching a
        rootdir's own basename would select every test in the run.
        """
        names: dict[str, object] = {mark.name: True for mark in self.own_markers}
        names[self.name] = True
        if self._plan.class_name is not None:
            for part in self._plan.class_name.split("."):
                names[part] = True
        names[self.path.name] = True
        return names

    @property
    def config(self) -> _builtins.Config:
        """`Node.config` — the same object ``request.config`` and ``pytestconfig`` answer."""
        return _builtins.current_config()

    def add_marker(self, marker: object, append: bool = True) -> None:
        """Port of `_pytest/nodes.py::Node.add_marker` (l. 265-287).

        ``append=False`` prepends, which is how pytest lets a late marker win
        ``get_closest_marker``.  A raw string is accepted for the same reason pytest accepts
        one (``item.add_marker("slow")``).
        """
        spec = (
            MarkSpec(name=marker)
            if isinstance(marker, str)
            else _spec_from_pytestmark(marker, self._plan.func)
        )
        if append:
            self.own_markers.append(spec)
        else:
            self.own_markers.insert(0, spec)

    def iter_markers(self, name: str | None = None) -> Iterator[MarkSpec]:
        """Port of `Node.iter_markers` — closest first, which is the order
        :func:`_mark_specs` already emits (function, class chain, module)."""
        for mark in self.own_markers:
            if name is None or mark.name == name:
                yield mark

    def get_closest_marker(self, name: str, default: MarkSpec | None = None) -> MarkSpec | None:
        """Port of `Node.get_closest_marker` — the first match in ``iter_markers`` order."""
        return next(self.iter_markers(name), default)


class SubRequest:
    """The ``request`` object a fixture (or a test) receives.

    **Named without the underscore on purpose.** The class name reaches users through
    ``AttributeError``/``TypeError`` messages -- ``'SubRequest' object has no attribute
    'node'`` -- and pytest's own class is `_pytest/fixtures.py::SubRequest` (l. 727). A
    reader who searches the message should land on pytest's documentation for the same
    object, not on a name that exists nowhere else.

    pytest special-cases ``request`` in `_get_active_fixturedef` (l. 566-570): it is never
    a registered fixture, it is a ``PseudoFixtureDef`` synthesised per requester.  Same
    here — which is why ``request`` appearing in a closure never raises "not found".

    Deliberately smaller than pytest's ``SubRequest``: ``param``, ``scope``,
    ``fixturename``, ``addfinalizer``, ``getfixturevalue``, ``node``, ``applymarker``,
    ``instance``, ``cls``, ``function``, ``module``, ``path``, ``keywords`` and ``config``
    are what fixtures actually use; ``session`` needs a collection tree the worker does not
    build.  ``param`` is *absent* (not ``None``) on an unparametrized fixture, so
    ``request.param`` raises ``AttributeError`` exactly as under pytest.

    ``config`` is a **subset** and loud past its edge — see
    :class:`rustest._v2_builtins.Config`.  It answers ``rootpath``, ``inipath``,
    ``invocation_params.dir``, ``cache`` and ``getini`` for the six ini values ``init``
    carries; ``getoption`` without a default raises pytest's own ``no option named`` error,
    because the v2 wire carries no command-line options and a fabricated answer would let a
    suite report on a mode it never ran in.
    """

    def __init__(
        self,
        runner: FixtureRunner,
        closure: FixtureClosure,
        params: Mapping[str, object],
        fixturename: str | None,
        scope: str,
        chain: tuple[FixtureDef, ...],
    ) -> None:
        super().__init__()
        self._runner = runner
        self._closure = closure
        self._params = params
        self._chain = chain
        # Captured now rather than read from the runner on access: a finalizer runs during
        # some *later* test's boundary drain, and a `request.node` that answered with
        # whatever test is current then would misattribute the teardown it belongs to.
        self._node = runner.current_node
        self.fixturename: Final = fixturename
        #: The **declared** scope of the fixture holding this request — ``"session"`` stays
        #: ``"session"`` even though the runner caches it in the worker-lifetime bucket.
        #: `SubRequest._scope` is the declared scope, it is what ``ScopeMismatch`` reports,
        #: and a fixture reading ``request.scope`` must see what it wrote.
        self.scope: Final = scope
        if fixturename is not None and fixturename in params:
            self.param: object = params[fixturename]

    def addfinalizer(self, finalizer: Callable[[], object]) -> None:
        """Port of `SubRequest.addfinalizer` (l. 722-724).

        pytest keeps a fixture's finalizers on that fixture's own ``FixtureDef`` and drains
        them LIFO in ``finish`` (l. 1055-1061), so they belong to the *fixture*, not to the
        scope: a fixture finalised early — by a parametrization change — takes its
        finalizers with it.  :meth:`FixtureRunner.add_finalizer` attaches to the fixture
        currently being set up for exactly that reason; only a ``request`` held by the test
        itself falls back to the function bucket, which is pytest's ``TopRequest`` behaviour
        (its finalizers live on the item).
        """
        self._runner.add_finalizer(finalizer)

    def getfixturevalue(self, argname: str) -> object:
        """Port of `FixtureRequest.getfixturevalue` (l. 527-534): resolve a name on demand.

        Routed through the same `_get_active_fixturedef` path a declared dependency takes —
        which is pytest's structure exactly (``getfixturevalue`` *is* a thin wrapper over
        it) — so a dynamic request is scope-checked too.  Names outside the static closure
        fall back to the registry, mirroring pytest's
        ``getfixturedefs(argname, self._pyfuncitem)`` branch (l. 583-587).
        """
        value, _fixturedef = self._runner._resolve_active(  # pyright: ignore[reportPrivateUsage]
            argname, self._closure, self._params, self._chain, self.scope
        )
        return value

    @property
    def node(self) -> _ItemNode:
        """The collection node for the test being set up — see :class:`_ItemNode`.

        pytest's ``SubRequest.node`` returns the node for the *request's own scope* (l. 758-778):
        a module-scoped fixture's request answers with the ``Module``.  There is only one
        node kind here, so every scope answers with the item; a session-scoped fixture asking
        ``request.node.name`` therefore gets the name of whichever test happened to trigger
        its setup, which is the same value pytest's ``TopRequest`` would give a
        function-scoped fixture and is *not* what pytest gives a session-scoped one.
        Recorded rather than papered over: the alternative is a tree.
        """
        node = self._node
        if node is None:
            raise AttributeError(
                "request.node is only available while a test is being set up or run"
            )
        return node

    @property
    def instance(self) -> object | None:
        """The test-class instance, or ``None`` at module level (`FixtureRequest.instance`)."""
        return self._runner.instance

    @property
    def cls(self) -> type | None:
        return self.node.cls

    @property
    def function(self) -> object:
        return self.node.function

    @property
    def module(self) -> types.ModuleType:
        return self.node.module

    @property
    def path(self) -> Path:
        return self.node.path

    @property
    def keywords(self) -> Mapping[str, object]:
        """`FixtureRequest.keywords` (l. 208-211): ``self.node.keywords``."""
        return self.node.keywords

    @property
    def config(self) -> _builtins.Config:
        """`FixtureRequest.config` — see :class:`rustest._v2_builtins.Config` for the subset.

        Unlike :attr:`node` this does **not** need a live test: it is whole-run state, so a
        session fixture built before any test can read it.
        """
        return _builtins.current_config()

    def applymarker(self, marker: object) -> None:
        """Port of `FixtureRequest.applymarker` (l. 449-458): ``self.node.add_marker(marker)``.

        pytest's own docstring is the whole semantics — "Apply a marker to a single test
        function invocation" — and the timing caveat lives in :class:`_ItemNode`: marks
        applied from a fixture or a body are recorded, not retroactively evaluated, because
        `skipping.py::pytest_runtest_setup` has already run.
        """
        self.node.add_marker(marker)


@dataclass
class _Cached:
    value: object
    param_key: object
    finalizer: _Finalizer


@dataclass
class _Finalizer:
    """One fixture's teardown list — a direct model of ``FixtureDef._finalizers``.

    Three kinds of entry land here, and the order they are appended in is the whole
    contract, because `FixtureDef.finish` drains it **LIFO**
    (l. 1055-1061, ``while self._finalizers: fin = self._finalizers.pop()``):

    1. whatever the fixture body registered through ``request.addfinalizer``;
    2. the post-yield teardown, appended by `call_fixture_func` *after* the body ran;
    3. one entry per **dependent** fixture, appended by `FixtureDef.execute`
       (l. 1115-1121) as each consumer is set up.

    Popping that stack therefore runs dependents first (newest), then this fixture's own
    post-yield half, then the body's finalizers in reverse — which is what makes tearing
    down a parametrized fixture tear its dependents down first (pytest #4871).
    """

    #: Draining is **destructive** — ``while calls: calls.pop()`` — which is both pytest's
    #: own loop and the entire reason a finalizer cannot run twice.  A fixture finished early
    #: by a parametrization change is still sitting on its scope bucket, and the bucket drain
    #: finds its list already empty.  No "already done" flag: a guard no test can kill is a
    #: guard that is not doing anything.
    fixturedef: FixtureDef | None
    calls: list[Callable[[], object]] = field(default_factory=list)


_NO_PARAM: Final = object()


class FixtureRunner:
    """Executes fixture setup and teardown for one worker process.

    Scope caching is keyed on the :class:`FixtureDef` object, as pytest keys on the
    ``FixtureDef`` instance itself (``fixturedef.cached_result``), so a fixture and the
    fixture it overrides cache independently.

    **Teardown is reverse setup order.**  pytest gets that from two stacks that both pop:
    ``FixtureDef.finish`` (l. 1053-1072, ``while self._finalizers: fin = self._finalizers.pop()``)
    and ``SetupState.teardown_exact``, which unwinds the node stack narrowest-first.  This
    keeps one finalizer stack per scope bucket, appended in setup order and drained in
    reverse, and :meth:`teardown` unwinds buckets narrowest-first — the same observable
    order without a collection tree.  A wider fixture can never depend on a narrower one
    (:class:`ScopeMismatch`), so a single stack per bucket is sufficient.

    Teardown exceptions are collected, not swallowed: one is re-raised, several become a
    ``BaseExceptionGroup``, mirroring ``FixtureDef.finish`` l. 1065-1072.  The cache entry
    is dropped either way ("Even if finalization fails, we invalidate the cached fixture
    value").
    """

    def __init__(self, asyncio_config: AsyncioConfig = _DEFAULT_ASYNCIO) -> None:
        super().__init__()
        #: The three ``asyncio_*`` ini values.  Defaulted so a caller driving the runner
        #: directly (this module's own tests) need not thread config it is not testing.
        self.asyncio: Final = asyncio_config
        self._cache: dict[FixtureDef, _Cached] = {}
        self._finalizers: dict[str, list[_Finalizer]] = {name: [] for name in _BUCKET_ORDER}
        #: One entry per fixture body currently executing; ``addfinalizer`` targets the top.
        self._extras_stack: list[list[Callable[[], object]]] = []
        #: The class chain of the last test :meth:`note_test_boundary` saw, so a change of
        #: class can drain class scope.  ``None`` means "module level, or nothing yet".
        self._current_class: str | None = None
        #: The file of the last test :meth:`note_module_boundary` saw — the module-scope
        #: analogue of :attr:`_current_class`.  ``None`` means "nothing yet".
        self._current_module: Path | None = None
        #: The test-class instance the current test (and its class fixtures) is bound to.
        #: pytest builds one per ``Function`` and both the test method and any class-body
        #: fixture see the same object (`resolve_fixture_function` l. 1142-1164, which
        #: rebinds the fixture to ``request.instance``).  ``None`` for a module-level test.
        self.instance: object | None = None
        #: The node ``request.node`` answers with, rebuilt per test by :meth:`setup`.  It is
        #: state rather than an argument because a fixture reaches its request object several
        #: frames below the call that knows which test is running.
        self.current_node: _ItemNode | None = None
        #: One ``asyncio.Runner`` per **loop scope name**, created on first use by
        #: :meth:`loop_runner` and closed when that scope's teardown bucket drains.  This is
        #: pytest-asyncio's ``_{scope}_scoped_runner`` fixture family (plugin.py l. 799-835)
        #: without the fixture machinery — see the module-level note above
        #: :class:`AsyncioConfig` for why the keying is on the scope and not on the bucket.
        self._loops: dict[str, asyncio.Runner] = {}
        #: Loop scopes currently being *created*, so resolving a user ``event_loop_policy``
        #: fixture that is itself async cannot recurse into loop creation forever.
        self._loops_opening: set[str] = set()
        #: The closure and fixture params of the test being set up, so :meth:`loop_runner`
        #: can resolve an ``event_loop_policy`` override through the ordinary fixture path.
        #: ``None`` before the first :meth:`setup`, which is when a runner driven directly by
        #: a test of this module can still ask for a loop.
        self._active: tuple[FixtureClosure, Mapping[str, object]] | None = None

    # -- setup ------------------------------------------------------------

    def setup(self, plan: ExecutionPlan) -> dict[str, object]:
        """Instantiate *plan*'s closure and return the test function's keyword arguments.

        Port of `_pytest/fixtures.py::FixtureRequest._fillfixtures` as reached from
        `_pytest/python.py::Function.setup` (l. 1673-1674): iterate the closure **in order**
        and resolve anything not already supplied.  Closure order is scope-sorted, so
        session/module fixtures are built before function ones and autouse before
        requested — and teardown therefore unwinds in exactly the reverse.

        Directly parametrized argnames are seeded from ``plan.direct_params`` rather than
        resolved, which is how pytest's pre-filled ``funcargs`` avoids looking up a
        fixture that does not exist for a ``@parametrize``d name.

        :meth:`note_test_boundary` is called first so a class-scope fixture cannot leak
        across a class boundary.  It is public as well, so a caller can drive the boundary
        explicitly, but calling it here means Task 3 cannot forget to.
        """
        self.note_test_boundary(plan.class_name)
        self._active = (plan.closure, plan.fixture_params)
        self._open_test_loop(plan)
        self.instance = self._build_instance(plan)
        self.current_node = _ItemNode(plan, self.instance)
        values: dict[str, object] = dict(plan.direct_params)
        # `indirect` names are deliberately absent from `direct_params`: they are ordinary
        # closure members whose fixture reads the parametrized value off `request.param`
        # (seeded into `plan.fixture_params` at collection), so the loop below resolves them
        # like any other fixture — scope-checked, cached per parameter, torn down in order.
        for name in plan.closure.names:
            if name in values:
                continue
            values[name] = self.resolve(name, plan.closure, plan.fixture_params, ())
        return {name: values[name] for name in plan.argnames if name in values}

    @staticmethod
    def _build_instance(plan: ExecutionPlan) -> object | None:
        """The object this test runs on, built once per test and shared with its fixtures.

        Port of `_pytest/python.py::Function.instance` (l. 1637-1646), which **caches**
        ``self._instance = self._getinstance()``, and of
        `_pytest/unittest.py::TestCaseFunction._getinstance` (l. 208-210), which is
        ``self.parent.obj(self.name)`` -- the ``TestCase`` constructed with the method name
        it will run.

        Building the ``TestCase`` **here** rather than in :func:`_run_call` is what makes
        MECHANISM M4 fixable at all. pytest's ``unittest_setup_method_fixture`` calls
        ``setup(request.instance, request.function)``, so ``setup_method`` must receive the
        *same* object the body then runs on; a second instance constructed at call time
        would take every attribute ``setup_method`` assigned with it. The single cached
        instance is exactly pytest's arrangement.
        """
        if plan.unittest_case is not None:
            factory = cast("Callable[[str], object]", plan.unittest_case)
            return factory(plan.unittest_method or "runTest")
        return plan.owner() if plan.owner is not None else None

    def _open_test_loop(self, plan: ExecutionPlan) -> None:
        """Resolve — and build — an async test's event loop during **setup**.

        pytest-asyncio does both here, not at call time: `PytestAsyncioFunction.setup`
        (l. 459-463) appends ``_{self._loop_scope}_scoped_runner`` to the item's fixture names
        before delegating to ``super().setup()``, so the scope is computed and the runner
        fixture is instantiated as part of the setup phase.

        Both halves are observable and neither is cosmetic:

        * **the phase a bad mark fails in.**  ``@pytest.mark.asyncio(loop_scope="x", scope="y")``
          raises while the loop scope is being resolved, so pytest reports it as an ``ERROR``
          at setup and exits 1.  Resolving it lazily at call time instead reported ``failed``
          — a different outcome word, a different section of the report, and a different
          answer to "did this test run".  Measured both ways against pytest 8.4.2.
        * **when the loop comes into existence.**  Building it here puts its close finalizer
          *underneath* every fixture finalizer of the same bucket, so the LIFO drain closes
          the loop after the fixtures that may need it, whichever of them asked for a loop
          first.

        Restricted to a body that is genuinely ``async def``, because that is the condition
        under which pytest-asyncio's collection hook substitutes its own item class at all
        (l. 606-614).  A **sync** test in ``auto`` mode has a nominal loop scope and no loop:
        creating one would cost every synchronous test in every suite an event loop it never
        touches.
        """
        if plan.func is None or not _is_coroutine_or_asyncgen(plan.func):
            return
        scope = self.test_loop_scope(plan)
        if scope is not None:
            _ = self.loop_runner(scope)

    def note_test_boundary(self, class_name: str | None) -> None:
        """Tear down class scope when the incoming test belongs to a different class.

        pytest gets this from the collection tree: `SetupState.teardown_exact` unwinds every
        node the next item does not share, so leaving ``TestA`` for ``TestB`` finalises
        ``TestA``'s class-scoped fixtures before ``TestB`` sets up.  A worker has no tree, so
        the boundary is the change in ``CollectedTest.class_name`` within a module — probed
        against pytest 8.4.2, which emits ``setup:TestA, A.one, A.two, teardown:TestA,
        setup:TestB, B.three, teardown:TestB`` for one class-scoped fixture used by two
        classes.

        **A module-level test always ends class scope**, which is not the same rule as
        "the class changed".  `_pytest/fixtures.py::SubRequest.node` (l. 758-778) resolves the
        node a class-scoped fixture is finalised with, and for a test that is not in a class
        ``get_scope_node`` returns ``None``, whereupon::

            if node is None and scope is Scope.Class:
                # Fallback to function item itself.
                node = self._pyfuncitem

        — so the fixture is torn down with that *one test*, exactly like a function-scoped
        one.  Probed: three module-level tests requesting one ``scope="class"`` fixture see
        three different values (the self-suite's ``test_isolation_1/2/3`` assert exactly
        ``1``, ``2``, ``3``).  Comparing ``None != None`` and concluding "same class" caches
        the first value for the whole file, which is a silently wrong result rather than an
        error — 14 of them in rustest's own suite before this branch existed.

        Called automatically by :meth:`setup`; exposed for callers that want to drive the
        boundary themselves.  A module change is handled by ``teardown("module")``, which
        drains class scope and resets this marker on the way past.
        """
        if class_name is None or class_name != self._current_class:
            self.teardown("class")
        self._current_class = class_name

    def note_module_boundary(self, path: Path) -> None:
        """Tear down module scope when the incoming test belongs to a different file.

        The module-scope twin of :meth:`note_test_boundary`, and it exists for the same
        reason: pytest unwinds the ``Module`` node from the collection tree
        (`SetupState.teardown_exact`) and a worker has no tree.  Without it a module-scoped
        fixture would live until Shutdown — and, worse, two files each defining ``class
        TestA`` would compare equal at the *class* boundary, so the first file's class-scoped
        fixtures would never be finalised either.

        **Divergence, deliberate and documented.**  pytest tears a module down during the
        *outgoing* test's teardown phase, because ``runtestprotocol`` is handed ``nextitem``.
        The execute wire has no lookahead, so this fires at the start of the *incoming*
        test and a module-fixture teardown failure is reported against that test's setup —
        the wrong test, but loudly, which beats losing the failure entirely.  Called by
        :func:`_run_phases`, not by :meth:`setup`, so the fixture engine's contract is
        unchanged and a caller driving the runner itself keeps the choice.
        """
        if path != self._current_module:
            self.teardown("module")
        self._current_module = path

    def resolve(
        self,
        name: str,
        closure: FixtureClosure,
        params: Mapping[str, object],
        chain: tuple[FixtureDef, ...],
    ) -> object:
        """Return one fixture's value, executing and caching it if needed.

        The test itself is the requester here, so the requesting scope is ``function`` —
        pytest's ``TopRequest._scope`` (l. 690-692) with its ``_check_scope`` that "always
        has function scope so always valid" (l. 701-707).
        """
        value, _fixturedef = self._resolve_active(name, closure, params, chain, "function")
        return value

    def _resolve_active(
        self,
        name: str,
        closure: FixtureClosure,
        params: Mapping[str, object],
        chain: tuple[FixtureDef, ...],
        requesting_scope: str,
    ) -> tuple[object, FixtureDef | None]:
        """Port of `_get_active_fixturedef` (l. 566-641) + `FixtureDef.execute` (l. 1074-1140).

        Returns the value **and** the fixturedef that produced it, because the caller needs
        the def to register itself as a dependent (see below).  ``None`` for ``request``,
        which is pytest's ``PseudoFixtureDef`` and is excluded from both the scope check and
        the dependency cascade.

        ``chain`` is the fixturedefs currently being resolved, outermost first — pytest's
        ``_iter_chain`` walk. An overriding fixture that requests its own name gets the
        *next* definition up (l. 596-608, ``index = -1``, decremented once per matching
        request in the chain), and exhausting the levels is an error rather than infinite
        recursion.

        **The dependency order is pytest #4871 and it is not an optimisation.**  Dependencies
        are resolved BEFORE this fixture's own cache is consulted, because resolving them can
        finalise *us*: pytest's comment at l. 1077-1082 says so outright — "This needs to be
        done before checking if we have a cached value, since if a dependent fixture has
        their cache invalidated, e.g. due to parametrization, they finalize themselves and
        fixtures depending on it (which will likely include this fixture) setting
        `self.cached_result = None`."  Then every dependency gets a finalizer that finishes
        *this* fixture (l. 1115-1121, ``requested_fixtures_that_should_finalize_us``), so
        tearing a dependency down tears its dependents down first.  Registering that only
        after the cache check is pytest #12135 ("avoid adding our finalizer multiple times").

        Without both halves a module-scoped fixture built on a module-scoped **parametrized**
        fixture is handed a stale value: probed, pytest re-creates it per parameter and emits
        ``setup:base:a, setup:derived(a), test, teardown:derived(a), teardown:base:a,
        setup:base:b, setup:derived(b), test``.
        """
        if name == "request":
            requester = chain[-1] if chain else None
            return (
                SubRequest(
                    self,
                    closure,
                    params,
                    requester.name if requester is not None else None,
                    requester.scope if requester is not None else "function",
                    chain,
                ),
                None,
            )

        defs = closure.arg2defs.get(name) or closure.registry.getfixturedefs(name)
        if not defs:
            raise FixtureLookupError(_fixture_not_found_message(name, closure.registry))

        index = -1 - sum(1 for entry in chain if entry.name == name)
        if -index > len(defs):
            raise FixtureLookupError(
                f" recursive dependency involving fixture '{name}' detected"
                + _available_fixtures_suffix(closure.registry)
            )
        fixturedef = defs[index]

        # Checked against the SELECTED def, not the nearest one: an override chain can put a
        # differently scoped definition at `index`, and pytest checks the def it is about to
        # execute (l. 633, `self._check_scope(fixturedef, fixturedef._scope)`).
        self._check_scope(fixturedef, requesting_scope, chain)

        sub_chain = (*chain, fixturedef)
        kwargs: dict[str, object] = {}
        dependencies: list[FixtureDef] = []
        for argname in fixturedef.argnames:
            value, dependency = self._resolve_active(
                argname, closure, params, sub_chain, fixturedef.scope
            )
            kwargs[argname] = value
            if dependency is not None:
                dependencies.append(dependency)

        param_key = params.get(name, _NO_PARAM)
        cached = self._cache.get(fixturedef)
        if cached is not None:
            if cached.param_key is param_key or cached.param_key == param_key:
                return cached.value, fixturedef
            # "We have a previous but differently parametrized fixture instance so we
            # need to tear it down before creating a new one." (FixtureDef.execute
            # l. 1119-1122).  It is the *registered* finalizer that runs -- running a
            # freshly made empty one would drop the cache and silently skip both the
            # post-yield teardown and the dependents hanging off it.
            self._finish(cached.finalizer)

        finalizer = _Finalizer(fixturedef)
        for dependency in dependencies:
            dependency_cache = self._cache.get(dependency)
            if dependency_cache is not None:
                dependency_cache.finalizer.calls.append(functools.partial(self._finish, finalizer))

        value = self._call(fixturedef, kwargs, finalizer)
        self._finalizers[_SCOPE_BUCKET[fixturedef.scope]].append(finalizer)
        self._cache[fixturedef] = _Cached(value, param_key, finalizer)
        return value, fixturedef

    def _check_scope(
        self,
        fixturedef: FixtureDef,
        requesting_scope: str,
        chain: tuple[FixtureDef, ...],
    ) -> None:
        """Port of `SubRequest._check_scope` (l. 780-801) — including its message, verbatim.

        A wider-scoped fixture may not request a narrower-scoped one; allowing it would cache
        the narrow value for the wide lifetime, which is silently wrong rather than an error.

        One divergence, unavoidable: pytest renders each frame's path with ``bestrelpath``
        against ``session.path`` (l. 803-809); the runner has no session, so the path is
        absolute.  Everything else — the sentence, the two headings, the
        ``path:lineno:  def name(sig)`` frame format — is byte-identical.
        """
        if _SCOPE_INDEX[requesting_scope] <= _SCOPE_INDEX[fixturedef.scope]:
            return
        fixture_stack = "\n".join(_format_fixturedef_line(entry) for entry in chain)
        raise ScopeMismatch(
            f"ScopeMismatch: You tried to access the {fixturedef.scope} scoped "
            + f"fixture {fixturedef.name} with a {requesting_scope} scoped request object. "
            + f"Requesting fixture stack:\n{fixture_stack}\n"
            + f"Requested fixture:\n{_format_fixturedef_line(fixturedef)}"
        )

    def _call(
        self, fixturedef: FixtureDef, kwargs: Mapping[str, object], finalizer: _Finalizer
    ) -> object:
        """Port of `call_fixture_func` (l. 916-932) — run to the yield, defer the rest.

        The finalizer is registered *after* the body runs, so a finalizer the body added
        through ``request.addfinalizer`` sits below it on the stack and therefore runs
        after it, matching pytest's single per-fixture LIFO list.  A finalizer is appended
        even for a non-yield fixture: `FixtureDef.execute` schedules ``finish`` in a
        ``finally`` regardless (l. 1132-1136), and that is what drops the cached value at
        the end of the scope.

        A fixture defined in a test-class body is bound to the same instance the test
        method gets — `resolve_fixture_function` l. 1152-1163
        (``fixturefunc.__get__(instance)``).  Without it the fixture would be called with
        ``self`` missing.

        An **async** fixture is driven on the event loop of its own *loop scope*
        (:meth:`fixture_loop_scope`): an ``async def`` body is awaited to its value, and an
        ``async def`` + ``yield`` body is advanced to the yield at setup and to exhaustion at
        teardown — on the **same** loop, which is why the scope is captured into the teardown
        partial rather than recomputed.  That is pytest-asyncio's model
        (`plugin.py::_wrap_async_fixture`/`_wrap_asyncgen_fixture`, l. 299-382), and without
        it the fixture value handed to a test is a **coroutine object**, silently truthy,
        failing only wherever the test first uses it.

        Whether the body is driven at all is :meth:`wraps_async_fixture`'s decision, and in
        ``strict`` mode the answer for a plain ``@pytest.fixture async def`` is *no* — the
        oracle's behaviour, reproduced deliberately.
        """
        func = fixturedef.func
        if fixturedef.needs_instance and self.instance is not None:
            func = cast(Callable[..., object], cast(Any, func).__get__(self.instance))
        wraps_async = self.wraps_async_fixture(fixturedef.func)
        self._extras_stack.append(finalizer.calls)
        try:
            if wraps_async and inspect.isasyncgenfunction(func):
                import contextvars

                loop_scope = self.fixture_loop_scope(fixturedef)
                # `_wrap_asyncgen_fixture` l. 317-318: one context, built here, used for BOTH
                # halves of the generator -- the setup run below and the resume in the
                # finalizer -- which is why it is captured into the teardown partial.
                context = contextvars.copy_context()
                agen = cast(Any, func)(**kwargs)
                try:
                    value = self.run_coroutine(agen.__anext__(), loop_scope, context=context)
                except StopAsyncIteration:
                    raise FixtureLookupError(f"{fixturedef.name} did not yield a value") from None
                teardown: Callable[[], object] | None = functools.partial(
                    _teardown_async_yield,
                    self,
                    fixturedef,
                    agen,
                    loop_scope,
                    context,
                    _apply_contextvar_changes(context),
                )
            elif inspect.isgeneratorfunction(func):
                generator = cast(Any, func)(**kwargs)
                try:
                    value = cast(object, next(generator))
                except StopIteration:
                    raise FixtureLookupError(f"{fixturedef.name} did not yield a value") from None
                teardown = functools.partial(_teardown_yield, fixturedef, generator)
            else:
                value = func(**kwargs)
                # Detected on the **value**, and duck-typed, exactly as the test-body guard
                # in `_consume_test_result` is — pytest's own
                # `hasattr(result, "__await__")` (`_pytest/python.py` l. 158).
                #
                # NOT because the decorator hides anything: `decorators.fixture` returns the
                # function unchanged (it only attaches `__rustest_fixture__*` attributes), so
                # `inspect.iscoroutinefunction` on the registered callable is perfectly
                # reliable — verified. The reason is the *other* half of pytest's check: a
                # fixture may legitimately hand back an awaitable that is not a coroutine —
                # an `asyncio.Future`, a task wrapper, any object with `__await__` — and
                # `iscoroutine` says False for every one of them, so the narrow check handed
                # the test an un-awaited object. Same false green as the test-body one, one
                # layer along.
                #
                # Gated on `wraps_async` so it does not leak into `strict` mode, where the
                # oracle's rule is "an async fixture the user did not mark is not the
                # plugin's business" (see `wraps_async_fixture`). In `auto` this is reached
                # for a *sync* fixture too, where pytest-asyncio returns early -- that is the
                # extension, and it is confined to the mode that already means "rustest runs
                # async things for you".
                if wraps_async and hasattr(value, "__await__"):
                    import contextvars

                    # `_wrap_async_fixture` l. 365-378: run the body in a copied context,
                    # replay whatever it changed into the caller's, and register the undo as
                    # an ordinary finalizer of this fixture -- which is what
                    # `request.addfinalizer(reset_contextvars)` is.
                    context = contextvars.copy_context()
                    value = self.run_coroutine(
                        value, self.fixture_loop_scope(fixturedef), context=context
                    )
                    reset = _apply_contextvar_changes(context)
                    if reset is not None:
                        finalizer.calls.append(reset)
                teardown = None
        finally:
            _ = self._extras_stack.pop()
        if teardown is not None:
            finalizer.calls.append(teardown)
        return value

    # -- asyncio ----------------------------------------------------------

    def run_coroutine(
        self,
        coro: Any,
        scope: str,
        timeout: float | None = None,
        context: contextvars.Context | None = None,
    ) -> object:
        """Drive *coro* to completion on the event loop of *scope*.

        *scope* is **required and has no default**, which is the point: every one of the four
        call sites has already resolved a loop scope from the config and the marks, and a
        default here would let a fifth one silently pick a loop rather than fail to compile.
        "It ran on the wrong loop" is diagnosed three frames inside somebody's library.

        *context* is the ``contextvars.Context`` the coroutine runs in, and ``None`` means
        "the runner's own" — which is what an ``asyncio.Runner`` uses by default and what
        every *fixture* call site passes.  The **test** call site passes a fresh
        ``copy_context()``; see :func:`_consume_test_result` for why the two differ.

        **One loop per loop-scope, for that scope's lifetime** — the model this replaced was
        one loop per *worker*, which made ``@mark.asyncio(loop_scope=...)`` decorative and
        made two tests asking for different scopes compare ``id(get_running_loop())`` equal.
        The loop comes from :meth:`loop_runner`, so its lifetime is the matching teardown
        bucket's; see the note above :class:`AsyncioConfig`.

        Execution is `asyncio.Runner.run`, which is what pytest-asyncio uses
        (`_synchronize_coroutine` l. 708-723, `runner.run(coro, context=context)`) rather
        than a bare ``loop.run_until_complete``.  The difference is not cosmetic: ``Runner``
        wraps the coroutine in a Task, installs and restores a SIGINT handler around the run,
        and on close drains async generators and the default executor before closing the
        loop.

        **Sequential, one coroutine at a time, and that is the oracle's behaviour** —
        `Runner.run` cannot be re-entered, so two tests sharing a loop scope do not overlap
        under pytest-asyncio.  Probed on pytest 8.4.2 + pytest-asyncio 1.2.0: two tests each
        awaiting ``asyncio.sleep(0.30)`` on one *session*-scoped loop take 0.613 s wall, with
        the second starting 0.003 s after the first ended.  rustest v1 batched such tests into
        one ``asyncio.gather`` (`async_executor.py::run_coroutines_parallel`); that is not
        reproduced here — see the module docstring's scope-limit list.

        *timeout* is rustest's own extension (:func:`_asyncio_timeout`) and is applied
        **inside** the loop with ``asyncio.wait_for``, so an overrunning test is cancelled on
        the loop it is running on rather than abandoned.
        """
        import asyncio

        runner = self.loop_runner(scope)
        if timeout is not None:
            coro = asyncio.wait_for(coro, timeout)
            try:
                return runner.run(coro, context=context)
            except TimeoutError:
                # v1's wording (`async_executor.py` l. 85), kept so a suite that greps its
                # CI output for the phrase keeps finding it.
                _fail(f"Test timed out after {timeout} seconds")
        return runner.run(coro, context=context)

    def loop_runner(self, scope: str) -> asyncio.Runner:
        """The ``asyncio.Runner`` for *scope*, created on first use.

        Port of `plugin.py::_create_scoped_runner_fixture` (l. 799-829)::

            with _temporary_event_loop_policy(new_loop_policy):
                runner = Runner(debug=debug_mode).__enter__()

        — with the fixture's *scope* becoming an entry on the matching teardown bucket.  The
        close finalizer is registered **before** the caller's own finalizer can be (a fixture's
        finalizer is pushed only after its body returns, and the body is what asks for the
        loop), so the bucket's LIFO drain closes the loop last: every async fixture that ran
        on it has already been unwound.  That is the ordering pytest gets from the runner
        fixture being *requested* by the fixtures that need it.

        ``debug`` is not honoured: ``asyncio_debug`` is not on the wire.  It changes only the
        loop's own diagnostics (slow-callback warnings, coroutine-origin tracking) and no test
        outcome, so it is a named gap rather than a silent one.
        """
        import asyncio

        existing = self._loops.get(scope)
        if existing is not None:
            return existing
        policy = self._event_loop_policy(scope)
        with _temporary_event_loop_policy(policy):
            runner = asyncio.Runner()
            # `__enter__` is `_lazy_init` — it builds the loop *now*, which has to happen
            # inside the policy context or the policy would have no effect on it.
            _ = runner.__enter__()
        self._loops[scope] = runner
        self._finalizers[_SCOPE_BUCKET[scope]].append(
            _Finalizer(None, [functools.partial(self._close_loop, scope)])
        )
        return runner

    def _event_loop_policy(self, scope: str) -> object:
        """The event loop policy a new loop for *scope* is built under.

        pytest-asyncio ships ``event_loop_policy`` as a **session-scoped autouse fixture**
        returning ``asyncio.get_event_loop_policy()`` (l. 838-841), and each runner fixture
        requests it (l. 804-808).  Overriding that fixture is the documented way to run a
        suite on ``uvloop`` — so the override has to go through the ordinary fixture path,
        not a special case: a user's ``event_loop_policy`` may itself request fixtures, and it
        is scope-checked like any other (a ``scope="function"`` override feeding a
        session-scoped loop is the same :class:`ScopeMismatch` pytest raises).

        Two guards:

        * before the first :meth:`setup` there is no closure to resolve against, so the
          default policy is used — that is the path a test of this module driving
          :meth:`run_coroutine` by hand takes;
        * a scope already being opened returns the default rather than recursing, which is
          what an ``async def event_loop_policy`` would otherwise do forever.  pytest-asyncio
          cannot hit this (its fixture is sync by contract); reaching it here means the
          override is unusable, and a default policy is a better answer than a
          ``RecursionError``.
        """
        if self._active is None or scope in self._loops_opening:
            return _default_event_loop_policy()
        closure, params = self._active
        if not closure.registry.getfixturedefs("event_loop_policy"):
            return _default_event_loop_policy()
        self._loops_opening.add(scope)
        try:
            value, _def = self._resolve_active("event_loop_policy", closure, params, (), scope)
        finally:
            self._loops_opening.discard(scope)
        return value

    def _close_loop(self, scope: str) -> None:
        """Close one scope's runner — the teardown half of :meth:`loop_runner`.

        Port of the `_scoped_runner` fixture's exit path (l. 817-827): close the runner, and
        turn the ``RuntimeError`` a test that closed the loop out from under it provokes into
        a warning rather than a teardown failure, because the runner is already gone either
        way and failing here would attribute the fault to whatever scope happened to end.
        pytest-asyncio raises it as a ``RuntimeWarning``; with no warnings channel it goes to
        stderr, with the plugin's own text so the phrase is greppable.
        """
        runner = self._loops.pop(scope, None)
        if runner is None:
            return
        try:
            runner.close()
        except RuntimeError:
            print(
                "An exception occurred during teardown of an asyncio.Runner. "
                + "The reason is likely that you closed the underlying event loop in a test, "
                + "which prevents the cleanup of asynchronous generators by the runner.\n"
                + traceback.format_exc(),
                file=sys.stderr,
            )

    def _close_loops(self) -> None:
        """Close every runner still open — the belt to :meth:`loop_runner`'s braces.

        Every runner registers its own close on a teardown bucket, and
        :meth:`teardown_all` drains every bucket, so this normally finds nothing.  It exists
        because "the loop was left open" is a leak with no symptom in the run that caused it:
        the process exits, the loop is collected, and the missing ``shutdown_asyncgens`` is
        never reported.
        """
        for scope in list(self._loops):
            self._close_loop(scope)

    def fixture_loop_scope(self, fixturedef: FixtureDef) -> str:
        """The loop scope an async *fixture* runs on.

        Port of `plugin.py::pytest_fixture_setup` (l. 736-741), verbatim in precedence::

            loop_scope = (
                getattr(fixturedef.func, "_loop_scope", None)
                or default_loop_scope
                or fixturedef.scope
            )

        The third fallback is the one that surprises: with ``asyncio_default_fixture_loop_scope``
        unset, an async fixture's loop scope is **its own caching scope**, so a
        ``scope="module"`` async fixture gets a module-lived loop without anyone configuring
        one.  That is why the option travels as an ``Option`` all the way from
        ``src/v2/config.rs``: collapsing "unset" into ``"function"`` would put every wider
        async fixture on a loop that dies before it does.

        The scope check is **inside** this method rather than beside its callers, because the
        answer and its legality are one question: there is no caller that wants an
        unchecked loop scope, and a second entry point would be a second way to skip
        :meth:`_check_loop_scope`.
        """
        func = getattr(fixturedef.func, "__func__", fixturedef.func)
        marked = _safe_getattr(func, "_loop_scope", None)
        scope = marked or self.asyncio.default_fixture_loop_scope or fixturedef.scope
        if scope not in _SCOPE_INDEX:
            raise ValueError(
                f"{scope!r} is not a valid loop_scope for fixture {fixturedef.name!r}. "
                + f"Valid scopes are: {', '.join(SCOPE_NAMES)}."
            )
        self._check_loop_scope(fixturedef, cast(str, scope))
        return cast(str, scope)

    def _check_loop_scope(self, fixturedef: FixtureDef, loop_scope: str) -> None:
        """A fixture may not run on a loop narrower than itself.  ``ScopeMismatch`` if it does.

        **The oracle gets this for free and this port had to be told.**  pytest-asyncio
        acquires the loop through the fixture's *own* request —
        ``runner = request.getfixturevalue(f"_{loop_scope}_scoped_runner")``
        (`plugin.py::pytest_fixture_setup` l. 742-743) — so pytest's ordinary
        ``SubRequest._check_scope`` fires: a session-scoped fixture asking for a
        function-scoped runner fixture is the same error as a session-scoped fixture asking
        for any other function-scoped fixture.  :meth:`loop_runner` is called directly here,
        which skips the fixture graph and therefore skipped the check.

        **What it cost, measured.**  ``@pytest_asyncio.fixture(scope="session",
        loop_scope="function")`` — a shape reachable straight from the configuration
        pytest-asyncio's own deprecation warning steers people towards — ran **green** under
        rustest while pytest reported ``2 errors``: the async-generator's teardown resumed on
        a *different, newly built* loop after the loop its setup ran on had been closed
        (probed: ``teardown same=False setup_loop_closed=True``).  A fixture holding anything
        loop-bound — a connection pool, a task, a lock — is then torn down against a foreign
        loop, silently, in the one direction where "it passed" is the wrong answer.

        The first two lines of the message are pytest's, byte for byte, including the
        synthetic ``_{scope}_scoped_runner`` fixture name an operator would grep for.  The
        ``Requested fixture:`` line is **not** faked: pytest points at
        ``pytest_asyncio/plugin.py:800``'s ``_scoped_runner`` because that function exists;
        here the loop is :meth:`loop_runner`, and naming a plugin file this runner does not
        use would send a reader to source that has nothing to do with the failure.
        """
        if _SCOPE_INDEX[loop_scope] >= _SCOPE_INDEX[fixturedef.scope]:
            return
        raise ScopeMismatch(
            f"ScopeMismatch: You tried to access the {loop_scope} scoped "
            + f"fixture _{loop_scope}_scoped_runner with a {fixturedef.scope} scoped "
            + "request object. Requesting fixture stack:\n"
            + f"{_format_fixturedef_line(fixturedef)}\n"
            + "Requested fixture:\n"
            + f"  the {loop_scope}-scoped event loop "
            + "(python/rustest/_v2_worker.py::FixtureRunner.loop_runner)"
        )

    def wraps_async_fixture(self, func: object) -> bool:
        """Would pytest-asyncio drive this fixture's body on a loop?

        Port of `plugin.py::pytest_fixture_setup`'s two early returns (l. 728-735)::

            if not _is_asyncio_fixture_function(fixturedef.func):
                if asyncio_mode == Mode.STRICT:
                    return (yield)          # left alone entirely
                if not _is_coroutine_or_asyncgen(fixturedef.func):
                    return (yield)

        In **strict** mode an ``async def`` fixture written with a plain ``@pytest.fixture``
        is therefore *not* awaited, and the test is handed a **coroutine object** — probed
        under pytest 8.4.2 + pytest-asyncio 1.2.0, which additionally emits pytest's own
        ``PytestRemovedIn9Warning`` about it.  That looks like a false green and is one; it is
        also exactly what the oracle does, and the escape hatches are the two the oracle
        offers: ``@pytest_asyncio.fixture`` or ``asyncio_mode = auto``.

        The ``auto`` answer here is ``True`` for a *sync* fixture as well, where the oracle
        returns early.  The difference is only reachable through rustest's own extension —
        :meth:`_call` awaits a sync fixture that *returns* an awaitable — and keeping the
        extension inside the ``auto`` branch is what stops strict mode from acquiring a
        behaviour the oracle has no counterpart for.
        """
        return _is_asyncio_fixture_function(func) or self.asyncio.mode != "strict"

    def test_loop_scope(self, plan: ExecutionPlan) -> str | None:
        """The loop scope a *test body* runs on, or ``None`` if rustest must not run it.

        ``None`` is the strict-mode answer for an unmarked coroutine test, and it is what
        makes the mode observable at all.  pytest-asyncio decides this at collection, in
        `pytest_pycollect_makeitem_convert_async_functions_to_subclass` (l. 606-614)::

            if _get_asyncio_mode(node.config) == Mode.AUTO and not node.get_closest_marker("asyncio"):
                node.add_marker("asyncio")
            if node.get_closest_marker("asyncio"):
                updated_item = specialized_item_class._from_function(node)

        — i.e. auto marks every async function, and only a marked item is converted.  An
        unconverted one falls through to pytest's own ``pytest_pyfunc_call``, which calls it
        and hits ``async_fail`` (`_pytest/python.py` l. 150-159).  Probed: in strict mode an
        unmarked ``async def test_x`` and an unmarked ``async def`` + ``yield`` test both
        report **failed** with :data:`ASYNC_NOT_SUPPORTED_MESSAGE`, while a marked one runs.

        The scope itself is `PytestAsyncioFunction._loop_scope` (l. 476-489): the closest
        ``asyncio`` mark's ``loop_scope``, else ``asyncio_default_test_loop_scope``.
        """
        mark = _asyncio_mark(plan.marks)
        if mark is None and self.asyncio.mode == "strict":
            return None
        default = self.asyncio.default_test_loop_scope
        return default if mark is None else _marked_loop_scope(mark, default)

    def add_finalizer(self, finalizer: Callable[[], object]) -> None:
        """Attach a ``request.addfinalizer`` callable to the fixture being set up.

        Outside any fixture body — a ``request`` the test itself holds — it goes on the
        function bucket, which is where pytest's ``TopRequest.addfinalizer`` puts it
        (``self._pyfuncitem.addfinalizer``).
        """
        if self._extras_stack:
            self._extras_stack[-1].append(finalizer)
        else:
            self._finalizers["function"].append(_Finalizer(None, [finalizer]))

    # -- teardown ---------------------------------------------------------

    def teardown(self, scope: str) -> None:
        """Unwind every bucket up to and including *scope*, narrowest first.

        Port of `_pytest/runner.py::SetupState.teardown_exact`, which pops the node stack
        from the deepest node upwards.  Task 3 calls ``teardown("function")`` after each
        test and ``teardown("module")`` when the worker leaves a module; :meth:`teardown_all`
        at Shutdown.

        Raises the single teardown exception, or a ``BaseExceptionGroup`` of several —
        never swallows one.  pytest reports a teardown failure as an **error** even when
        the test body passed, so the execute half maps this to status ``error``.
        """
        limit = _BUCKET_ORDER.index(_SCOPE_BUCKET.get(scope, scope))
        if limit >= _BUCKET_ORDER.index("class"):
            # Leaving the class (or anything wider) invalidates the boundary marker, so the
            # next `note_test_boundary` compares against nothing rather than a dead class.
            self._current_class = None
        if limit >= _BUCKET_ORDER.index("module"):
            self._current_module = None
        exceptions: list[BaseException] = []
        for bucket in _BUCKET_ORDER[: limit + 1]:
            stack = self._finalizers[bucket]
            while stack:
                try:
                    self._finish(stack.pop())  # noqa: B023 - popped, not captured
                except BaseException as exc:  # noqa: BLE001 - re-raised below, never dropped
                    exceptions.append(exc)
        if len(exceptions) == 1:
            raise exceptions[0]
        if exceptions:
            raise BaseExceptionGroup("errors while tearing down fixtures", exceptions[::-1])

    def teardown_all(self) -> None:
        """Unwind every scope — the worker is shutting down.

        Every loop's close is already an entry on a teardown bucket (:meth:`loop_runner`), so
        ``teardown("session")`` closes them in the right order by itself: a session-scoped
        async fixture is unwound on a live loop and the loop is closed after it.  The sweep in
        the ``finally`` is the backstop for a loop whose bucket never drained — an
        already-failed teardown, or a runner built outside any setup — and for the ordinary
        case it finds nothing.
        """
        try:
            self.teardown("session")
        finally:
            self._close_loops()

    def _finish(self, finalizer: _Finalizer) -> None:
        """Drain one fixture's teardown and drop its cached value, even if it raised.

        Port of `FixtureDef.finish` (l. 1053-1072): LIFO over the fixture's finalizer list —
        the post-yield half first (appended last by ``call_fixture_func``), then whatever the
        body registered, in reverse — collecting exceptions rather than stopping, and
        invalidating the cache regardless ("Even if finalization fails, we invalidate the
        cached fixture value").

        The eviction is guarded on **identity**, and that guard is load-bearing.  pytest
        finalises a ``FixtureDef`` object and clears *its own* ``cached_result``; here the
        cache is a dict keyed by fixturedef, and a finalizer can outlive the entry it was
        made for — a parametrization change finishes the old instance and immediately caches
        a new one, while the *old* finalizer is still sitting on its scope bucket waiting to
        be drained.  Popping unconditionally would then evict the **live** value at the end
        of the scope, and the next request would rebuild a fixture that was supposed to be
        cached (or, worse, re-run a session fixture mid-run).  Comparing
        ``cached.finalizer is finalizer`` makes a stale drain a no-op, exactly as pytest's
        per-object model makes it one.
        """
        exceptions: list[BaseException] = []
        while finalizer.calls:
            call = finalizer.calls.pop()
            try:
                _ = call()
            except BaseException as exc:  # noqa: BLE001 - re-raised below, never dropped
                exceptions.append(exc)
        if finalizer.fixturedef is not None:
            cached = self._cache.get(finalizer.fixturedef)
            if cached is not None and cached.finalizer is finalizer:
                del self._cache[finalizer.fixturedef]
        if len(exceptions) == 1:
            raise exceptions[0]
        if exceptions:
            name = finalizer.fixturedef.name if finalizer.fixturedef else "request"
            raise BaseExceptionGroup(
                f"errors while tearing down fixture {name!r}", exceptions[::-1]
            )


def _teardown_async_yield(
    runner: FixtureRunner,
    fixturedef: FixtureDef,
    generator: object,
    loop_scope: str,
    context: contextvars.Context,
    reset_contextvars: Callable[[], None] | None,
) -> None:
    """The async twin of :func:`_teardown_yield`: resume an ``async def`` + ``yield`` fixture.

    Same contract — exhausting the generator is success, a second yield is the user error
    pytest calls "fixture function has more than one 'yield'" — driven through the loop
    instead of ``next()``.

    *loop_scope* is the scope :meth:`FixtureRunner._call` resolved at **setup** and is passed
    through rather than recomputed, so the second half of the generator runs on the same loop
    as the first.  Recomputing would give the same answer today and would silently stop doing
    so the moment anything about the fixture's loop scope became state rather than a pure
    function of the fixturedef — and "the async generator resumed on a different loop" is an
    error message that names the generator, not the mistake.
    """

    async def drain() -> None:
        try:
            _ = await cast(Any, generator).__anext__()
        except StopAsyncIteration:
            return
        raise ValueError(f"fixture {fixturedef.name} has more than one 'yield'")

    _ = runner.run_coroutine(drain(), loop_scope, context=context)
    # `_wrap_asyncgen_fixture` l. 335-337: the undo runs AFTER the generator is drained, so
    # the second half of the body still sees the variables its first half set.
    if reset_contextvars is not None:
        reset_contextvars()


def _teardown_yield(fixturedef: FixtureDef, generator: object) -> None:
    """Port of `_pytest/fixtures.py::_teardown_yield_fixture` (l. 934-949).

    A second yield is an error, not a silent stop: the code after it would never run.
    """
    try:
        _ = next(cast(Any, generator))
    except StopIteration:
        return
    raise FixtureLookupError(f"fixture function has more than one 'yield': {fixturedef.name}")


def _format_fixturedef_line(fixturedef: FixtureDef) -> str:
    """Port of `SubRequest._format_fixturedef_line` (l. 803-809).

    ``getfslineno`` returns a **0-based** line and pytest prints ``lineno + 1``; that is
    exactly ``co_firstlineno``, which is what this reads.  The path is absolute where pytest
    would relativise against the session — see :meth:`FixtureRunner._check_scope`.
    """
    func = fixturedef.func
    try:
        path = inspect.getsourcefile(cast(Any, func)) or "<unknown>"
        lineno = cast(Any, func).__code__.co_firstlineno
    except (OSError, TypeError, AttributeError):  # pragma: no cover - exotic callables
        path, lineno = "<unknown>", 1
    try:
        signature = str(inspect.signature(cast(Any, func)))
    except (TypeError, ValueError):  # pragma: no cover - builtins never reach here
        signature = "(...)"
    return f"{path}:{lineno}:  def {getattr(func, '__name__', fixturedef.name)}{signature}"


def _available_fixtures_suffix(registry: FixtureRegistry) -> str:
    """The two trailing lines of pytest's lookup error (l. 876-877), verbatim."""
    return (
        "\n available fixtures: {}".format(", ".join(sorted(registry.names())))
        + "\n use 'pytest --fixtures [testpath]' for help on them."
    )


def _fixture_not_found_message(name: str, registry: FixtureRegistry) -> str:
    """pytest's ``fixture '<name>' not found`` message (l. 875-877), verbatim.

    A name this worker knows *of* but does not implement gets its own wording instead —
    ``capfd`` is not a fixture the user forgot to write, it is a gap in this worker, and
    sending them to ``pytest --fixtures`` for it would waste their time.
    """
    if name in UNSUPPORTED_BUILTIN_FIXTURES:
        return (
            f"fixture '{name}' is not supported by the rustest v2 worker yet "
            + f"(supported builtins: {', '.join(BUILTIN_FIXTURES)})"
        )
    return f"fixture '{name}' not found" + _available_fixtures_suffix(registry)


# -- registry construction --------------------------------------------------

#: Conftest modules already imported in this worker, keyed by absolute path.
#: pytest's ``PytestPluginManager._conftest_plugins`` / ``get_plugin(str(conftestpath))``
#: cache (`_pytest/config/__init__.py::_importconftest` l. 695-698) — without it a conftest
#: would be re-imported once per test file, resetting its module-level state.
_conftest_modules: dict[Path, types.ModuleType] = {}


def conftest_chain(path: Path, rootdir: Path) -> list[Path]:
    """The ``conftest.py`` files that apply to *path*, outermost first.

    Port of `_pytest/config/__init__.py::_loadconftestmodules` (l. 638-668)::

        for parent in reversed((directory, *directory.parents)):
            if self._is_in_confcutdir(parent):
                conftestpath = parent / "conftest.py"
                if conftestpath.is_file():
                    ...

    with ``confcutdir`` at its default, which `Config._preparse` (l. 1424-1429) sets to
    ``self.inipath.parent`` when a config file was found and ``self.rootpath`` otherwise —
    i.e. rootdir in every case the v2 orchestrator produces.  ``_is_in_confcutdir`` is
    ``path not in confcutdir.parents``, so the conftest **at** rootdir is included and
    anything strictly above it is not.

    Divergence, deliberate: pytest reaches directories outside rootdir when an explicit
    ``--confcutdir`` says so.  The worker has no such option on the wire, so the walk stops
    at rootdir; a file outside rootdir gets no conftests at all rather than an unbounded
    walk to the filesystem root.
    """
    directory = path.parent if path.is_file() or path.suffix else path
    chain: list[Path] = []
    for parent in (directory, *directory.parents):
        conftest = parent / "conftest.py"
        if conftest.is_file():
            chain.append(conftest)
        if parent == rootdir:
            break
    chain.reverse()
    return chain


def import_conftest(path: Path, rootdir: Path) -> types.ModuleType:
    """Import one ``conftest.py`` under its real identity, cached by path.

    Port of `_pytest/config/__init__.py::_importconftest` (l. 687-737).  The load-bearing
    line is the one pytest annotates itself::

        # conftest.py files there are not in a Python package all have module
        # name "conftest", and thus conflict with each other. Clear the existing
        # before loading the new one, otherwise the existing one will be
        # returned from the module cache.
        pkgpath = resolve_package_path(conftestpath)
        if pkgpath is None:
            try:
                del sys.modules[conftestpath.stem]
            except KeyError:
                pass

    Without it, two sibling non-package directories each holding a ``conftest.py`` would
    give the second one an import-file mismatch — probed under pytest 8.4.2, which collects
    both happily, so the eviction is required for parity, not an optimisation.

    Consequence worth stating: ``sys.modules["conftest"]`` names *the most recently
    imported* non-package conftest, so a test module's own ``import conftest`` sees the one
    for its own directory only because the chain is imported immediately before the module.
    pytest has the identical hazard (it does not restore ``sys.modules`` either).
    """
    cached = _conftest_modules.get(path)
    if cached is not None:
        return cached
    if _resolve_package_path(path) is None:
        _ = sys.modules.pop(path.stem, None)
    module = import_test_module(path, rootdir)
    _conftest_modules[path] = module
    return module


def _conftest_baseid(conftest: Path, rootdir: Path) -> str:
    """The visibility id of a conftest's fixtures.

    Port of `_pytest/fixtures.py::FixtureManager.pytest_plugin_registered` (l. 1587-1600):
    the conftest's **directory**, relative to rootdir, posix-separated, with ``"."``
    normalised to ``""`` — pytest writes that normalisation out explicitly, because ``""``
    is the nodeid of the session and a rootdir conftest is visible to everything.
    """
    relative = _relative_posix(conftest.parent, rootdir)
    return "" if relative == "." else relative


def _register_builtin_fixtures(registry: FixtureRegistry) -> None:
    """Register this worker's builtins, at total visibility.

    The implementations live in :mod:`rustest._v2_builtins` and are ports of pytest's own
    plugins — ``capsys``/``capfd`` of `_pytest/capture.py`, ``caplog`` of
    `_pytest/logging.py`, ``cache`` of `_pytest/cacheprovider.py`, ``tmp_path``/``tmpdir`` of
    `_pytest/tmpdir.py` + `_pytest/legacypath.py`, ``mocker`` of pytest-mock's plugin.  Each
    carries ``__rustest_fixture__`` metadata, so the ordinary
    :meth:`FixtureRegistry.parse_factories` path could read them; they are registered by name
    instead so that :data:`BUILTIN_FIXTURES` is the single list, and a fixture added to the
    module without being listed is simply not registered rather than silently shadowing a
    user's.

    ``baseid=""`` is pytest's "always matches" marker for a plugin-provided fixture
    (`FixtureDef.__init__`: "For other plugins, the baseid is the empty string").
    Registered first, so any user fixture of the same name shadows it — which is what
    pytest's furthest-to-nearest ordering does for plugin fixtures too.
    """
    for name in BUILTIN_FIXTURES:
        func = getattr(_builtins, name)
        registry.register(
            FixtureDef(
                name=name,
                func=cast(Callable[..., object], func),
                scope=cast(str, getattr(func, "__rustest_fixture_scope__", "function")),
                params=None,
                autouse=False,
                baseid="",
                argnames=tuple(_requested_argnames(func, name, None)),
            )
        )


def build_registry(path: Path, rootdir: Path) -> tuple[types.ModuleType, FixtureRegistry]:
    """Import one test file with its conftest chain and assemble its fixture registry.

    **The single entry point :func:`collect_file` uses**, so the path the tests exercise is
    the path production takes — there is no second, subtly different assembly to drift from.

    Order is load-bearing twice over:

    * *Conftests before the module*, as pytest loads them (they are plugins, resolved during
      ``_set_initial_conftests``, long before any test module is imported).  It is what makes
      a test module's own ``import conftest`` reach the object the registry just parsed —
      corpus ``fixtures/autouse``, and the shape v1 got wrong by importing it twice.
    * *Builtins, then each conftest rootdir-down, then the module* — registration order **is**
      the shadowing rule, so ``defs[-1]`` is the nearest definition, which is what corpus
      ``fixtures/override-nearest`` asserts.

    All of it happens during collection, not at execute time, because the closure this feeds
    is what expands parametrized fixtures into separate nodeids.
    """
    registry = FixtureRegistry()
    _register_builtin_fixtures(registry)
    for conftest in conftest_chain(path, rootdir):
        conftest_module = import_conftest(conftest, rootdir)
        registry.parse_factories(conftest_module, _conftest_baseid(conftest, rootdir))
        _register_declared_plugins(conftest_module, registry)
    module = import_test_module(path, rootdir)
    # xunit module/function hooks, as autouse fixtures, registered **before** the module's
    # own fixtures — `Module.collect` calls `_register_setup_module_fixture` and
    # `_register_setup_function_fixture` at l. 554-555 and only then `parsefactories`
    # (l. 556). Autouse order is registration order, so the reverse makes `setup_function`
    # run *after* a user's autouse fixture. Measured: pytest
    # `['setup_function', 'fixture']`, rustest `['fixture', 'setup_function']`.
    module_baseid = _relative_posix(path, rootdir)
    for kind in ("module", "function"):
        for fixturedef in _xunit_fixturedefs(module, module_baseid, kind=kind):
            registry.register(fixturedef)
    registry.parse_factories(module, module_baseid)
    _register_declared_plugins(module, registry)
    return module, registry


def _plugin_specs(raw: object) -> list[str]:
    """``pytest_plugins`` as a list of module names, accepting pytest's two spellings.

    Port of `_pytest/config/__init__.py::_get_plugin_specs_as_list` (pytest 8.4.2): a single
    ``str`` is one name, a non-string sequence is a list of names, anything else is ignored
    here (pytest raises ``UsageError``; refusing the whole file over a malformed declaration
    would be a harsher failure than the feature warrants, and the fixtures it would have
    contributed then show up as an ordinary "fixture not found").
    """
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(name) for name in cast(Sequence[object], raw)]
    return []


def _register_declared_plugins(module: types.ModuleType, registry: FixtureRegistry) -> None:
    """Honour a module's ``pytest_plugins`` declaration by registering its fixtures.

    pytest reaches this through
    `_pytest/config/__init__.py::PytestPluginManager.consider_module` ->
    ``_import_plugin_specs(getattr(mod, "pytest_plugins", []))`` -> ``import_plugin`` ->
    ``register`` -> ``FixtureManager.pytest_plugin_registered`` -> ``parsefactories(plugin)``
    **with no nodeid**, which is why plugin fixtures are visible everywhere rather than only
    below the conftest that named them.  ``baseid=""`` reproduces that ("For other plugins,
    the baseid is the empty string" — `FixtureDef.__init__`).

    This is the *fixture* half of the plugin protocol and nothing else: hooks a named module
    defines are not called, because v2 has no hook system.  A plugin that only supplies
    fixtures — the overwhelmingly common shape, and the one rustest's own
    ``tests/test_pytest_plugins_and_applymarker`` exercises — works; one that implements
    ``pytest_collection_modifyitems`` is silently inert, exactly as it is under v1.

    An unimportable plugin raises out of here into :func:`collect_file`, which turns it into a
    collection error entry for the file — the same treatment a broken ``import`` in the
    conftest itself gets.
    """
    for name in _plugin_specs(_safe_getattr(module, "pytest_plugins", None)):
        registry.parse_factories(importlib.import_module(name), "")


def _markdown_registry(path: Path, rootdir: Path) -> FixtureRegistry:
    """:func:`build_registry` minus the module import — a `.md` file has none to import."""
    registry = FixtureRegistry()
    _register_builtin_fixtures(registry)
    for conftest in conftest_chain(path, rootdir):
        conftest_module = import_conftest(conftest, rootdir)
        registry.parse_factories(conftest_module, _conftest_baseid(conftest, rootdir))
        _register_declared_plugins(conftest_module, registry)
    return registry


def _build_entry(
    rel_path: str,
    parts: tuple[str, ...],
    param_id: str | None,
    marks: list[MarkSpec],
    fixtures: list[str],
) -> CollectedTestDict:
    """Assemble one ``CollectedTest``, omitting every empty optional field.

    ``class_name`` carries the whole class chain (``TestBox.TestInner``), which is
    identical to the innermost class name for every non-nested case; ``qualname``
    already carries the same chain plus the function name.

    This is the **only** place a :class:`MarkSpec` is projected onto the wire, so the
    lossy ``_json_safe`` step cannot leak into the copy the execute half evaluates.
    """
    entry: CollectedTestDict = {
        "id": build_nodeid(rel_path, parts, param_id),
        "path": rel_path,
        "qualname": ".".join(parts),
    }
    if len(parts) > 1:
        entry["class_name"] = ".".join(parts[:-1])
    if param_id is not None:
        entry["param_id"] = param_id
    if marks:
        entry["marks"] = [spec.to_wire() for spec in marks]
    if fixtures:
        entry["fixtures"] = fixtures
    return entry


@dataclass(frozen=True)
class _CollectContext:
    """The per-file state every collection helper needs, threaded instead of global."""

    module: types.ModuleType
    path: Path
    rel_path: str
    naming: Naming
    plans: list[ExecutionPlan]
    #: Collection reads exactly one thing from it -- :attr:`AsyncioConfig.mode`, which
    #: decides whether an async generator test is xfailed (:func:`_async_generator_xfail`).
    #: Carried on the context rather than passed down the four call levels that separate
    #: :func:`collect_module` from :func:`_collect_function`.
    asyncio: AsyncioConfig = _DEFAULT_ASYNCIO


def _collect_function(
    obj: object,
    name: str,
    parts: tuple[str, ...],
    owner: type | None,
    outer_marks: list[MarkSpec],
    registry: FixtureRegistry,
    context: _CollectContext,
    outer_cases: list[_Case] | None = None,
    outer_indirect: frozenset[str] = frozenset(),
) -> list[CollectedTestDict]:
    """Port of `_pytest/python.py::pytest_pycollect_makeitem`'s function branch.

    A matching name that is not a function warns and collects nothing; ``__test__ =
    False`` hides it; a generator function is a hard ``fail()`` in pytest, i.e. a
    collection error for the whole module.

    **Parametrization is the cross product of the fixture dimensions and the decorator
    cases**, in that order, with the last dimension varying fastest — which is what
    `Metafunc.parametrize` produces by looping ``for callspec in self._calls`` outside and
    ``for param in parametersets`` inside, and therefore what :func:`itertools.product`
    produces here.  Fixture components come first because pluggy runs
    ``FixtureManager.pytest_generate_tests`` before ``python.py``'s (probed:
    ``test_mixed[1-7]`` for ``@parametrize("direct", [7, 8])`` over a ``params=[1, 2]``
    fixture).  This is the change that expands corpus ``fixtures/parametrized-fixture``
    into ``test_number[1]``/``test_number[2]``.

    Limitation: pytest additionally unwraps ``functools.partial``/``__wrapped__``
    chains via ``get_real_func`` before deciding "not a function"; this port checks
    the unwrapped attribute only, so a ``functools.partial`` test object is skipped
    where pytest would collect it.
    """
    func = _unwrap(obj)
    if not inspect.isfunction(func):
        return []
    if _safe_getattr(func, "__test__", True) is False:
        return []
    if inspect.isgeneratorfunction(func):
        raise CollectionRefusal(
            f"'yield' keyword is allowed in fixtures, but not in tests ({name})"
        )

    marks = _mark_specs(func) + outer_marks
    # `plugin.py` l. 609-613: auto mode marks every async function, and only a marked item is
    # converted into the plugin's own item class.  The two together are "is this an asyncio
    # test", and that is the only thing collection needs the mode for.
    is_asyncio_test = context.asyncio.mode != "strict" or _asyncio_mark(marks) is not None
    async_generator_mark = _async_generator_xfail(func, name, is_asyncio_test)
    if async_generator_mark is not None:
        marks = [async_generator_mark, *marks]
    full_parts = (*parts, name)
    own_cases = _parametrization(func)
    if outer_cases:
        cases = (
            list(outer_cases) if own_cases is None else _cross_product_cases(outer_cases, own_cases)
        )
    else:
        cases = own_cases
    direct_cases: list[tuple[str | None, Mapping[str, object], tuple[MarkSpec, ...]]] = (
        [(None, {}, ())]
        if cases is None
        else [(case_id, values, case_marks) for case_id, values, case_marks in cases]
    )
    parametrized = frozenset(name for _case_id, values, _m in direct_cases for name in values)
    # `outer_indirect` carries a **class-level** `@parametrize(..., indirect=[...])`, whose
    # metadata `decorators.py::parametrize` wrote onto the class object where a method cannot
    # see it -- functions do not inherit class attributes. Apex Member Designer's
    # `TestAPIEndpoints` is exactly that shape (`@pytest.mark.parametrize("model_class,
    # endpoint,model_instance", MODEL_CONFIGS, indirect=["model_instance"])` on the *class*),
    # and reading only the function's own metadata left 120 of its tests receiving the raw
    # string instead of the fixture's return: `'str' object has no attribute 'model_dump'`.
    # Threaded explicitly for the same reason `outer_cases` is, and it composes through
    # nested classes for free.
    indirect = (_indirect_names(func) | outer_indirect) & parametrized
    # `_pytest/fixtures.py::FixtureManager.getfixtureinfo` passes
    # `_get_direct_parametrize_args`, which is the **direct** names only
    # (`_pytest/python.py::Metafunc._get_direct_parametrize_args` filters on
    # `_params_directness[...] == "direct"`). An *indirect* name must keep its fixturedefs,
    # because routing the value through that fixture is the whole feature.
    direct_argnames = parametrized - indirect

    argnames = _requested_argnames(func, name, owner)
    closure = build_closure(
        registry,
        argnames,
        ignore_args=direct_argnames,
        usefixtures=usefixtures_names(marks),
    )
    # Both direct and indirect names are excluded here: pytest skips a fixture's own
    # `params=` whenever "the test itself parametrizes using this argname"
    # (`FixtureManager.pytest_generate_tests`), and an indirect `@parametrize` is still the
    # test parametrizing it.
    # pytest validates this on the Metafunc, i.e. after the closure is known, and so does
    # this: `closure.names` is the port of `metafunc.fixturenames`.
    _validate_if_using_arg_names(
        func, name, owner, parametrized, indirect, frozenset(closure.names)
    )
    dimensions = fixture_param_dimensions(closure, parametrized)

    entries: list[CollectedTestDict] = []
    for combination in itertools.product(*(values for _name, values in dimensions)):
        fixture_ids = [param_id for param_id, _value, _marks in combination]
        fixture_params = {
            dimension[0]: value for dimension, (_id, value, _marks) in zip(dimensions, combination)
        }
        # A `pytest.param(..., marks=...)` inside a fixture's `params=` marks the tests that
        # draw that value, exactly as one inside `@parametrize` marks its own case.
        fixture_marks = [mark for _id, _value, marks in combination for mark in marks]
        for case_id, case_values, case_marks in direct_cases:
            id_parts = [*fixture_ids, *([] if case_id is None else [case_id])]
            param_id = "-".join(id_parts) if id_parts else None
            # A parameter set's own marks come **last**, so a per-case `xfail` is evaluated
            # after (and can override the effect of) anything the function or module
            # declared -- which is the order pytest builds `own_markers` in for the item.
            case_all_marks = [*marks, *fixture_marks, *case_marks]
            entry = _build_entry(
                context.rel_path,
                full_parts,
                param_id,
                case_all_marks,
                _fixture_names(func, name, owner, frozenset(case_values) - indirect),
            )
            entries.append(entry)
            context.plans.append(
                ExecutionPlan(
                    id=entry["id"],
                    path=context.path,
                    module=context.module,
                    parts=full_parts,
                    func=cast(Callable[..., object], func),
                    owner=owner,
                    closure=closure,
                    # An indirect name's value rides here, keyed by the fixture it is routed
                    # to, which is exactly where `SubRequest.__init__` looks for
                    # `request.param` and where `_resolve_active` reads its per-parameter
                    # cache key. Nothing else has to change for teardown-per-parameter and
                    # for a wider-scoped fixture to be rebuilt once per value.
                    fixture_params={
                        **fixture_params,
                        **{key: value for key, value in case_values.items() if key in indirect},
                    },
                    direct_params={
                        key: value for key, value in case_values.items() if key not in indirect
                    },
                    argnames=tuple(argnames),
                    marks=tuple(case_all_marks),
                )
            )
    return entries


def _unittest_class_registry(
    cls: type,
    parts: tuple[str, ...],
    registry: FixtureRegistry,
    context: _CollectContext,
) -> FixtureRegistry:
    """The registry a ``TestCase``'s methods resolve against.

    A **child** of the module's, exactly as `_pytest/python.py::Class.collect` uses a child
    nodeid, carrying at most one extra fixture: the class-scoped autouse wrapper around
    ``setUpClass``/``tearDownClass`` (`_pytest/unittest.py` l. 116-170).

    pytest skips registering it entirely for a ``@unittest.skip``-decorated class (l. 92-96)
    — ``TestCase.run`` reports that skip itself, and building the class would run
    ``setUpClass`` for a class nobody is going to test.  Ported, so a skipped class with a
    deliberately-exploding ``setUpClass`` still reports SKIPPED rather than ERROR.

    **Three fixtures, not one — MECHANISM M4.**  ``UnitTestCase.collect`` (l. 85-96) calls
    *three* registrars in a row, and rustest used to port only the middle one::

        self._register_unittest_setup_method_fixture(cls)   # setup_method  / teardown_method
        self._register_unittest_setup_class_fixture(cls)    # setUpClass    / tearDownClass
        self._register_setup_class_fixture()                # setup_class   / teardown_class

    The first and third are pytest's **xunit** hooks, and a ``TestCase`` subclass gets them
    *as well as* unittest's native ones. Nothing about the class opts in; inheriting
    ``unittest.TestCase`` does not opt a suite out of pytest's own vocabulary.

    dateutil's ``tests/test_parser.py::ParserTest`` is exactly that shape: it declares its
    shared state in ``@classmethod def setup_class(cls)`` — the pytest spelling — so under
    rustest ``cls.tzinfos``/``brsttz``/``default``/``uni_str``/``str_str`` were never
    assigned, and precisely the 8 tests of ~240 in the class that read one of the five failed
    with ``AttributeError``. The failure set was self-confirming: every test that never
    touched class state passed.

    ``setup_method`` is registered through :func:`_unittest_setup_method_fixture` rather than
    through :func:`_xunit_fixturedefs` because pytest uses a *different* function for the
    ``TestCase`` case (l. 174-200) whose call convention is not the same: it passes
    ``(instance, request.function)`` unconditionally, where the plain-class version
    (`python.py::_register_setup_method_fixture`) goes through
    ``_call_with_optional_argument``. ``setup_class``, by contrast, IS the plain-class
    registrar — pytest calls `Class._register_setup_class_fixture` itself — so it reuses
    :func:`_xunit_fixturedefs` verbatim.

    Note what is *not* registered: ``setup_function``/``teardown_function``, which pytest
    scopes to module-level functions and stands down inside any class.
    """
    class_registry = registry.child()
    if _is_unittest_skipped(cls):
        return class_registry
    baseid = f"{context.rel_path}::{'::'.join(parts)}"
    defs: list[FixtureDef] = []

    method_fixture = _unittest_setup_method_fixture(cls)
    if method_fixture is not None:
        defs.append(
            FixtureDef(
                name=f"_unittest_setup_method_fixture_{cls.__qualname__}",
                func=method_fixture,
                scope="function",
                params=None,
                autouse=True,
                baseid=baseid,
                argnames=("request",),
                needs_instance=False,
            )
        )

    fixture = _unittest_class_fixture(cls)
    if fixture is not None:
        defs.append(
            FixtureDef(
                name=f"_unittest_setUpClass_fixture_{cls.__qualname__}",
                func=fixture,
                scope="class",
                params=None,
                autouse=True,
                baseid=baseid,
                argnames=(),
            )
        )

    # `_register_setup_class_fixture()` — the *plain-class* xunit pair, reached here because
    # `UnitTestCase.collect` calls it on a TestCase too.
    defs.extend(_xunit_fixturedefs(cls, baseid, kind="class"))

    for definition in defs:
        class_registry.register(definition)
    return class_registry


def _unittest_setup_method_fixture(cls: type) -> Callable[..., Iterator[None]] | None:
    """``setup_method``/``teardown_method`` on a ``TestCase``, or ``None``.

    Port of `_pytest/unittest.py::UnitTestCase._register_unittest_setup_method_fixture`
    (l. 172-200).  Three details are pytest's and none is incidental:

    * the hooks are found with a plain ``getattr``, **not** ``_get_first_non_fixture_func``
      — pytest uses the bare form here, so a ``setup_method`` that is also a fixture is
      still called as a hook on a ``TestCase`` (it would not be on a plain class);
    * they are called as ``setup(self, request.function)`` with **both** arguments always,
      never through ``_call_with_optional_argument``. A ``TestCase``'s ``setup_method`` that
      takes only ``self`` is a ``TypeError`` under pytest, and reproducing that is the point
      of porting rather than paraphrasing;
    * a ``@unittest.skip``-decorated *instance* raises ``skip.Exception`` from the fixture,
      before ``setup_method`` runs.

    ``self`` is ``request.instance``, which is the instance the body will run on — see
    :meth:`FixtureRunner._build_instance` for why that is now true.
    """
    setup = _safe_getattr(cls, "setup_method", None)
    teardown = _safe_getattr(cls, "teardown_method", None)
    if setup is None and teardown is None:
        return None

    def unittest_setup_method_fixture(request: object) -> Iterator[None]:
        instance = _safe_getattr(request, "instance", None)
        if _is_unittest_skipped(instance):
            raise _Skipped(_safe_getattr(instance, "__unittest_skip_why__", ""))
        function = _safe_getattr(request, "function", None)
        if setup is not None:
            cast("Callable[..., object]", setup)(instance, function)
        yield
        if teardown is not None:
            cast("Callable[..., object]", teardown)(instance, function)

    return unittest_setup_method_fixture


def _collect_unittest_class(
    cls: type,
    name: str,
    parts: tuple[str, ...],
    outer_marks: list[MarkSpec],
    registry: FixtureRegistry,
    context: _CollectContext,
) -> list[CollectedTestDict]:
    """Port of `_pytest/unittest.py::UnitTestCase.collect` — the discovery half only.

    Names come from ``unittest.TestLoader().getTestCaseNames(cls)``, which applies
    ``testMethodPrefix`` and **sorts** them (``sortTestMethodsUsing``); probed, and
    ``test_zeta``/``test_alpha`` really do come back alphabetically, unlike a plain
    class.  Nothing is instantiated and nothing is run.

    ``UnitTestCase.nofuncargs = True``, so these items take no fixtures — the
    ``fixtures`` field is left empty rather than filled from the signature, and the
    :class:`ExecutionPlan` carries an empty closure so Task 3's execute path can tell a
    ``TestCase`` method (which it must drive through ``unittest``) from a plain function.

    The ``runTest`` fallback is **only** reached when the loop yielded nothing, and it is
    never itself subject to the ``__test__`` filter — pytest sets ``foundsomething = True``
    *after* that filter and then checks ``if not foundsomething``.  Probed: a ``TestCase``
    whose only ``test_*`` method carries ``__test__ = False`` and which also defines
    ``runTest`` collects as ``Legacy::runTest`` under pytest 8.4.2.  (pytest additionally
    skips ``twisted.trial``'s inherited ``runTest``; twisted is not in scope here.)
    """
    if _safe_getattr(cls, "__test__", True) is False:
        return []

    class_marks = _mark_specs(cls, consider_mro=True) + outer_marks
    child_parts = (*parts, name)
    class_registry = _unittest_class_registry(cls, child_parts, registry, context)
    closure = build_closure(class_registry, (), usefixtures=usefixtures_names(class_marks))
    loader = unittest.TestLoader()
    entries: list[CollectedTestDict] = []

    def record(method_name: str, marks: list[MarkSpec]) -> None:
        entry = _build_entry(context.rel_path, (*child_parts, method_name), None, marks, [])
        entries.append(entry)
        context.plans.append(
            ExecutionPlan(
                id=entry["id"],
                path=context.path,
                module=context.module,
                parts=(*child_parts, method_name),
                func=None,
                owner=cls,
                closure=closure,
                fixture_params={},
                direct_params={},
                argnames=(),
                marks=tuple(marks),
                unittest_case=cls,
                unittest_method=method_name,
            )
        )

    for method_name in loader.getTestCaseNames(cast(type[unittest.TestCase], cls)):
        method = _safe_getattr(cls, method_name, None)
        if _safe_getattr(method, "__test__", True) is False:
            continue
        record(method_name, _mark_specs(_unwrap(method)) + class_marks)

    if not entries and getattr(cls, "runTest", None) is not None:
        record("runTest", class_marks)
    return entries


def _collect_class(
    cls: type,
    name: str,
    parts: tuple[str, ...],
    outer_marks: list[MarkSpec],
    registry: FixtureRegistry,
    context: _CollectContext,
    outer_cases: list[_Case] | None = None,
    outer_indirect: frozenset[str] = frozenset(),
) -> list[CollectedTestDict]:
    """Port of `_pytest/python.py::Class.collect`.

    A class with an ``__init__`` or a ``__new__`` is refused — pytest emits
    ``PytestCollectionWarning("cannot collect test class ... because it has a
    __init__ constructor")`` and returns ``[]``, **without** failing the run.  The
    protocol has no warning channel in v1, so the class is skipped silently; emitting
    a collection error instead would turn pytest's exit 0 into exit 2 (corpus
    ``collection/class-collection``).

    Fixtures defined in the class body are registered into a **child** registry
    (`_pytest/python.py::Class.collect` l. 772: ``parsefactories(self.newinstance(),
    self.nodeid)``), so they are visible to this class's methods and to nothing else —
    including the module-level tests collected before it.  pytest parses them off an
    *instance*; this parses them off the class with the ``getattr_static`` staticmethod
    rule already used for test methods, which avoids instantiating a class during
    collection for the same result.
    """
    if _safe_getattr(cls, "__test__", True) is False:
        return []
    if _hasinit(cls) or _hasnew(cls):
        return []

    class_marks = _mark_specs(cls, consider_mro=True) + outer_marks
    child_parts = (*parts, name)
    class_registry = registry.child()
    class_baseid = f"{context.rel_path}::{'::'.join(child_parts)}"
    # ...and the class/method pair, which `Class.collect` registers at l. 769-770 — again
    # *before* `parsefactories` (l. 772), which is what puts `setup_method` ahead of a
    # class-body autouse fixture.
    for kind in ("class", "method"):
        for fixturedef in _xunit_fixturedefs(cls, class_baseid, kind=kind):
            class_registry.register(fixturedef)
    class_registry.parse_factories(cls, class_baseid, cls)
    # A class-level `@parametrize` writes its cases onto the **class object**
    # (`decorators.py::parametrize` is target-agnostic), where a method cannot see them:
    # functions do not inherit class attributes.  So the cases are read here and handed down
    # explicitly, which is also what keeps them out of `_parametrization(func)` and stops the
    # same dimension being counted twice.  `_safe_getattr` walks the MRO, so a subclass of a
    # parametrized class inherits the parametrization exactly as it inherits pytest's marks.
    class_cases = _parametrization(cls)
    if class_cases is not None:
        outer_cases = (
            class_cases if outer_cases is None else _cross_product_cases(outer_cases, class_cases)
        )
    # ...and the same for `indirect=`, which rides on the same decorator and therefore lands
    # on the same object.
    outer_indirect = outer_indirect | _indirect_names(cls)
    entries: list[CollectedTestDict] = []
    for member_name, member in _mro_ordered_members(cls):
        entries.extend(
            _make_items(
                member,
                member_name,
                child_parts,
                cls,
                class_marks,
                class_registry,
                context,
                outer_cases,
                outer_indirect,
            )
        )
    return entries


def _make_items(
    obj: object,
    name: str,
    parts: tuple[str, ...],
    owner: type | None,
    outer_marks: list[MarkSpec],
    registry: FixtureRegistry,
    context: _CollectContext,
    outer_cases: list[_Case] | None = None,
    outer_indirect: frozenset[str] = frozenset(),
) -> list[CollectedTestDict]:
    """Dispatch one namespace entry, mirroring pytest's ``pytest_pycollect_makeitem`` hooks.

    The unittest branch comes first because the hook is ``firstresult`` and
    `_pytest/python.py::pytest_pycollect_makeitem` carries ``@hookimpl(trylast=True)``
    (l. 209) while `_pytest/unittest.py::pytest_pycollect_makeitem` (l. 51) carries no
    hookimpl marker at all — so python's implementation is deliberately sorted to the
    end and every other implementation, unittest's included, is consulted before it.
    That ordering is by decorator, not by registration order, which is why a
    ``TestCase`` subclass is handled before the ``python_classes`` name filter ever
    applies.

    *owner* is the class whose ``__dict__`` produced *obj*, or ``None`` at module
    level; it is what `_fixture_names` needs for pytest's ``getattr_static``
    staticmethod test.
    """
    if _is_unittest_case(obj):
        return _collect_unittest_class(cast(type, obj), name, parts, outer_marks, registry, context)
    if inspect.isclass(obj):
        if _is_test_class(obj, name, context.naming):
            return _collect_class(
                obj, name, parts, outer_marks, registry, context, outer_cases, outer_indirect
            )
        return []
    if _is_test_function(obj, name, context.naming):
        return _collect_function(
            obj, name, parts, owner, outer_marks, registry, context, outer_cases, outer_indirect
        )
    return []


def collect_module(
    module: types.ModuleType,
    path: Path,
    rootdir: Path,
    naming: Naming,
    registry: FixtureRegistry | None = None,
    asyncio_config: AsyncioConfig = _DEFAULT_ASYNCIO,
) -> tuple[list[CollectedTestDict], list[ExecutionPlan]]:
    """Enumerate *module* into ``CollectedTest`` dicts **and** their execution plans.

    Port of `_pytest/python.py::PyCollector.collect` for a ``Module``: iterate the
    module ``__dict__`` (definition-ordered), which is exactly why a function nested
    inside another function is invisible (corpus ``collection/nested-function``).

    **Imported test functions are collected**, matching pytest 8.4.2's default:
    ``collect_imported_tests`` defaults to ``True``
    (`_pytest/main.py::pytest_addoption`), and the ``__module__`` filter in
    `PyCollector.collect` runs only when that ini is ``False``.  Probed:
    ``from helpers import test_shared`` collects as ``test_imports.py::test_shared``.
    The ini is not part of the ``init`` message, so the default is hard-coded here;
    honouring a non-default value needs a protocol field.

    **Module-level ``pytestmark`` applies to every test in the file.**  pytest reads
    it into ``Module.own_markers`` (`_pytest/python.py` l. 284:
    ``self.own_markers.extend(get_unpacked_marks(self.obj))``) and every item inherits
    it by walking its parents.  Mark order on each entry is pytest's own, taken from
    `_pytest/nodes.py::Node.iter_markers_with_node` (which iterates
    ``iter_parents()``, i.e. **closest first**) and confirmed by probe — for a method
    of a marked subclass of a marked base in a module with ``pytestmark``, pytest
    yields ``['own', 'base', 'derived', 'modA', 'modB']``: function marks, then the
    class chain base-first, then module marks.  Emitting the reverse would silently
    invert ``get_closest_marker`` for any 1b.2 consumer that takes the first match.

    *registry* defaults to one built from the builtins plus *module* itself
    (:func:`build_registry` without the conftest walk is not offered — the default here
    deliberately skips conftests so a caller that only has a module in hand gets a
    predictable, self-contained registry; :func:`collect_file` always passes the real one).

    **Known limitation, and it is a behavioural one, not a cosmetic one.**  pytest reorders
    collected items so that tests sharing a higher-scoped parametrized fixture run
    consecutively (`_pytest/fixtures.py::reorder_items`, invoked from
    ``pytest_collection_modifyitems``).  That pass is a no-op for ``function`` scope
    (``reorder_items_atscope`` returns early on ``scope is Scope.Function``) and operates on
    the whole session's item list, which a per-file worker does not have.

    The visible consequence is **how many times a fixture is set up**, not just what order
    the ids come in.  Probed: two tests sharing a module-scoped ``params=["a","b"]`` fixture
    run ``2`` setups under pytest (grouped ``a a b b``) and ``4`` here (interleaved
    ``a b a b``, each switch re-creating the fixture).  For an expensive fixture that is a
    performance and a semantics difference — anything the fixture accumulates is reset twice
    as often.  No corpus case has one; closing it needs the reorder in the orchestrator's
    manifest assembly, where the whole run's items exist.
    """
    if _safe_getattr(module, "__test__", True) is False:
        return [], []

    if registry is None:
        registry = FixtureRegistry()
        _register_builtin_fixtures(registry)
        # xunit module/function hooks, as autouse fixtures, registered **before** the
        # module's own — `Module.collect` calls `_register_setup_module_fixture` and
        # `_register_setup_function_fixture` (l. 554-555) and only then `parsefactories`
        # (l. 556). Autouse order is registration order, so the reverse would make
        # `setup_function` run *after* a user's autouse fixture.
        module_baseid = _relative_posix(path, rootdir)
        for kind in ("module", "function"):
            for fixturedef in _xunit_fixturedefs(module, module_baseid, kind=kind):
                registry.register(fixturedef)
        registry.parse_factories(module, module_baseid)

    context = _CollectContext(
        module=module,
        path=path,
        rel_path=_relative_posix(path, rootdir),
        naming=naming,
        plans=[],
        asyncio=asyncio_config,
    )
    module_marks = _mark_specs(module)
    entries: list[CollectedTestDict] = []
    for name, obj in list(vars(module).items()):
        if name in IGNORED_ATTRIBUTES:
            continue
        entries.extend(_make_items(obj, name, (), None, module_marks, registry, context))
    return entries, context.plans


def enumerate_module(
    module: types.ModuleType,
    path: Path,
    rootdir: Path,
    naming: Naming,
) -> list[CollectedTestDict]:
    """:func:`collect_module`'s manifest half — the wire entries, without the plans."""
    entries, _execution_plans = collect_module(module, path, rootdir, naming)
    return entries


# ---------------------------------------------------------------------------
# markdown code blocks — rustest's own tier, ported from v1
# ---------------------------------------------------------------------------

#: HTML comments that mark the *next* fenced block as "do not execute".  All three spellings
#: are v1's (`src/discovery.rs::extract_python_code_blocks`): rustest's own, pytest's, and
#: pytest-codeblocks', because a project migrating from either already has them in its docs.
MARKDOWN_SKIP_MARKERS: Final = (
    "<!--rustest.mark.skip-->",
    "<!--pytest.mark.skip-->",
    "<!--pytest-codeblocks:skip-->",
)

#: What a skipped block reports as its reason — v1's wording, kept so the two engines'
#: output reads the same.
MARKDOWN_SKIP_REASON: Final = "Skipped via HTML comment marker"


def extract_python_code_blocks(content: str) -> list[tuple[str, int, bool]]:
    """``(code, opening-fence line number, skip?)`` for every ```` ```python ```` block.

    Line-for-line port of `src/discovery.rs::extract_python_code_blocks` (v1), including the
    two rules that are easy to get subtly different:

    * the language is the **lowercased** text after the fence, so ```` ```Python ```` counts
      and ```` ```pycon ```` does not;
    * a skip marker survives only across *comment* lines — the `last_line_was_comment` latch
      — so `<!--pytest.mark.skip-->` followed by a paragraph and then a fence does **not**
      skip that fence.  Reproducing the latch matters: getting it wrong either executes
      blocks the author disabled (loud, wrong failures) or silently stops testing the docs.

    The line number is 1-based and points at the opening fence, which is what v1 puts in the
    test id and in the compiled block's filename.
    """
    blocks: list[tuple[str, int, bool]] = []
    in_block = False
    current: list[str] = []
    language = ""
    start_line = 0
    skip_marker = False
    previous_was_comment = False

    for index, line in enumerate(content.splitlines()):
        stripped = line.strip()

        if not in_block and any(marker in stripped for marker in MARKDOWN_SKIP_MARKERS):
            skip_marker = True
            previous_was_comment = True
            continue

        if stripped.startswith("```"):
            if in_block:
                if language == "python":
                    blocks.append(("\n".join(current), start_line, skip_marker))
                current = []
                language = ""
                in_block = False
                skip_marker = False
            else:
                in_block = True
                start_line = index + 1
                language = stripped[3:].strip().lower()
        elif in_block:
            current.append(line)
        elif not previous_was_comment:
            skip_marker = False

        previous_was_comment = False

    return blocks


def _codeblock_callable(code: str, path: Path, line_number: int) -> Callable[[], object]:
    """Compile one block into a zero-argument callable, as v1 does.

    Port of `src/discovery.rs::create_codeblock_callable`: the block is indented into a
    ``def`` and compiled with the filename ``<file>:L<line>``, so a traceback points at the
    markdown source rather than at a nameless ``<string>``.  Each block gets its **own**
    namespace — v1 execs into a fresh dict per block — so one block cannot see another's
    names, and a doc example is therefore forced to be self-contained exactly as the
    documentation guidelines say it must be.
    """
    body = "\n".join(f"    {line}" for line in code.splitlines()) or "    pass"
    source = f"# Code block from {path} (line {line_number})\ndef run_codeblock():\n{body}\n"
    namespace: dict[str, object] = {}
    exec(compile(source, f"{path}:L{line_number}", "exec"), namespace)  # noqa: S102
    return cast(Callable[[], object], namespace["run_codeblock"])


def collect_markdown(
    path: Path,
    rootdir: Path,
    registry: FixtureRegistry | None = None,
) -> tuple[list[CollectedTestDict], list[ExecutionPlan]]:
    """Enumerate a ``.md`` file's python fences as tests.

    **This tier has no pytest counterpart** — it is rustest's, shipped since v1
    (`src/discovery.rs::collect_from_markdown`) and switched off by ``--no-codeblocks``.  It
    is here rather than left behind at the flip because rustest's own README and guide are
    tested this way, and so are its users'.

    Two shapes differ from v1 and both are cosmetic, recorded rather than discovered later:

    * the node id is ``docs/guide/fixtures.md::codeblock_0_line_15`` — v1's *test name*
      verbatim — where v1 *displayed* ``...::codeblock_0::line_15``.  v2 ids are
      rootdir-relative posix and a second ``::`` would claim the block is a class member;
    * a block marked skipped carries a real ``skip`` mark instead of v1's out-of-band
      ``skip_reason``, so it travels on the wire and is evaluated by the same
      :func:`evaluate_skip_marks` every other skip goes through.

    Every block also carries the ``codeblock`` mark v1 attaches, so ``-m codeblock`` and
    ``-m "not codeblock"`` keep working.

    A code block requests no fixtures, so the closure is the registry's autouse set and
    nothing else; *registry* is accepted (and the conftest chain is loaded for it in
    :func:`collect_file`) so an autouse conftest fixture still applies to a doc example,
    which is what v1's ``merge_conftest_fixtures`` did for markdown too.
    """
    if registry is None:
        registry = FixtureRegistry()
        _register_builtin_fixtures(registry)

    rel_path = _relative_posix(path, rootdir)
    # A synthetic module, never imported and never in `sys.modules`: `ExecutionPlan` needs a
    # module object for the string-condition namespace, and a markdown file has none.
    module = types.ModuleType(f"rustest_codeblocks_{abs(hash(rel_path))}")
    module.__file__ = str(path)

    content = path.read_text(encoding="utf-8")
    closure = build_closure(registry, ())
    entries: list[CollectedTestDict] = []
    plans: list[ExecutionPlan] = []
    for index, (code, line_number, skipped) in enumerate(extract_python_code_blocks(content)):
        marks = [MarkSpec(name="codeblock")]
        if skipped:
            marks.append(MarkSpec(name="skip", kwargs={"reason": MARKDOWN_SKIP_REASON}))
        parts = (f"codeblock_{index}_line_{line_number}",)
        entry = _build_entry(rel_path, parts, None, marks, [])
        entries.append(entry)
        plans.append(
            ExecutionPlan(
                id=entry["id"],
                path=path,
                module=module,
                parts=parts,
                # A skipped block is **not compiled**.  v1 compiled every block up front, so
                # a `<!--rustest.mark.skip-->` fence containing pseudo-code turned the whole
                # file into a collection error — which defeats the purpose of the marker, and
                # the project's own documentation guidelines tell authors to use it for
                # "pseudo-code or incomplete snippets".  A skip mark short-circuits before
                # the body under any engine, so there is nothing to compile it *for*.
                func=(lambda: None) if skipped else _codeblock_callable(code, path, line_number),
                owner=None,
                closure=closure,
                fixture_params={},
                direct_params={},
                argnames=(),
                marks=tuple(marks),
            )
        )
    return entries, plans


# ---------------------------------------------------------------------------
# core 4: execution — outcomes by type, mark semantics, unittest translation
# ---------------------------------------------------------------------------

#: pytest's three test phases, in the order `_pytest/runner.py::runtestprotocol` runs them.
#: The phase a failure happened in is what separates ``"failed"`` from ``"error"`` on the
#: wire — see :attr:`PhaseReport.status`.
PHASES: Final = ("setup", "call", "teardown")

#: The closed set of wire statuses (``src/v2/protocol.rs``, `TestResult::status`).  These
#: are pytest's *reporting categories* (its `.`/`F`/`s`/`x`/`X`/`E` letters), not
#: ``TestReport.outcome``, which only ever holds three of them.
STATUSES: Final = ("passed", "failed", "skipped", "xfailed", "xpassed", "error")

#: The phases whose failures are reported as ``error`` rather than ``failed`` — derived from
#: :data:`PHASES` so the two cannot drift: every phase that is not the call is "the test never
#: properly ran".  `_pytest/runner.py` l. 214-223 and `_pytest/terminal.py` l. 333-335.
_ERROR_PHASES: Final = tuple(phase for phase in PHASES if phase != "call")


#: Exceptions that **abort the run** instead of being classified as a test outcome.
#:
#: Port of `_pytest/runner.py::call_runtest_hook` l. 242-244, which is the authority and is
#: narrower than it looks::
#:
#:     reraise: tuple[type[BaseException], ...] = (Exit,)
#:     if not item.config.getoption("usepdb", False):
#:         reraise += (KeyboardInterrupt,)
#:
#: **``SystemExit`` is deliberately not here.**  The obvious guess is that both "process-
#: ending" exceptions abort; probed, they do not — pytest reports ``raise SystemExit`` in a
#: body as an ordinary ``FAILED`` and **runs the next test**, because ``CallInfo.from_call``
#: catches ``BaseException`` and only the ``reraise`` set escapes.  ``SystemExit`` reraises
#: during *collection* only (l. 392).  Treating it as an abort would let one ``sys.exit()``
#: deep in a library silently truncate a run.
#:
#: ``Exit`` is pytest's own ``pytest.exit()`` signal; rustest has no equivalent, so
def _import_exit_class() -> type[BaseException]:
    """``pytest.exit()``'s exception — ``rustest.compat.pytest.Exit``.

    Its own function for the same reason as :func:`_import_outcome_classes`: the class is
    defined in the compat shim, and naming the import site makes it obvious that *this* is
    the one exception in :data:`ABORT_EXCEPTIONS` a user's test can raise on purpose.
    """
    from rustest.compat.pytest import Exit

    return Exit


#: Exceptions that end the **session** rather than the test: they are re-raised out of every
#: phase, propagate to :func:`main`, and stop the worker — classifying one would turn it into
#: a "failed" test and then calmly run the next one.
#:
#: * ``KeyboardInterrupt`` — Ctrl-C.
#: * ``Exit`` — ``pytest.exit()``.  `_pytest/main.py::wrap_session` catches it *outside* the
#:   run loop, so nothing between the test body and the session boundary may swallow it;
#:   listing it here is how a plain ``Exception`` subclass (which is what pytest's ``Exit``
#:   is) gets that treatment through this worker's own ``except Exception`` handlers.
#:
#: ``SystemExit`` is deliberately **absent**: pytest continues past one (1b.2 Task 3's
#: oracle-corrected finding), so it is classified as an ordinary failure.
_Exit: Final[type[BaseException]] = _import_exit_class()

ABORT_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (KeyboardInterrupt, _Exit)

#: The worker's exit code when a test called ``pytest.exit()``.
#:
#: Distinct from 0 (clean), 2 (protocol drift) and :data:`SHUTDOWN_TEARDOWN_EXIT` (3, a
#: broken teardown), because the orchestrator has to tell "the user ended the session" from
#: "the worker broke" — the first keeps the results already produced and exits 2, the second
#: is an orchestration failure and exits 3.  ``src/v2/execute.rs::SESSION_EXIT_EXIT``
#: mirrors this constant and **must be changed in the same commit**.
#:
#: The exit code is the whole channel: it carries the *fact* of a session exit and nothing
#: else, which is why ``pytest.exit(returncode=N)``'s N is not honoured (see
#: ``compat/pytest.py::exit``).  A payload would need a wire op.
SESSION_EXIT_EXIT: Final = 4


def _import_outcome_classes() -> tuple[type[BaseException], ...]:
    """The outcome exception classes this worker classifies **by type**.

    Returns ``(Skipped, StubSkipped, XFailed, Failed, StubFailed)``.  Two sources, because a
    real suite reaches rustest's outcomes by two different import paths and both must mean
    the same thing:

    * ``rustest.decorators`` — what ``pytest.skip()`` / ``pytest.fail()`` / ``pytest.xfail()``
      raise through the compat shim (``compat/pytest.py`` l. 649-654 rebinds them verbatim);
    * ``rustest._pytest_stub.outcomes`` — what ``from _pytest.outcomes import Skipped``
      gives a suite that reaches into pytest's internals, which
      :func:`install_pytest_shim` installs as ``sys.modules["_pytest.outcomes"]``.  They are
      **different classes**, so an ``isinstance`` against only one of them would silently
      reclassify half the ways a suite can skip.

    The stub warns on import by design (it is a deprecation shim); the worker imports it for
    its *types*, not to use it, so the warning is suppressed here — it is emitted anyway the
    moment :func:`install_pytest_shim` runs.
    """
    from rustest.decorators import Failed, Skipped, XFailed

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from rustest._pytest_stub.outcomes import Failed as StubFailed
        from rustest._pytest_stub.outcomes import Skipped as StubSkipped

    return (Skipped, StubSkipped, XFailed, Failed, StubFailed)


_Skipped, _StubSkipped, _XFailed, _Failed, _StubFailed = _import_outcome_classes()

#: "The test asked not to run."  Port of `_pytest/outcomes.py::Skipped` semantics.
#:
#: ``unittest.SkipTest`` is in here because pytest converts it explicitly —
#: `_pytest/unittest.py::pytest_runtest_makereport` l. 377-387, *"Convert unittest.SkipTest
#: to pytest.skip"* — and probed: a plain function raising ``unittest.SkipTest`` reports
#: SKIPPED under pytest 8.4.2, not FAILED.
SKIPPED_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (
    _Skipped,
    _StubSkipped,
    unittest.SkipTest,
)

#: What :func:`collect_file` treats as a *module-level* skip request, i.e. what pytest's
#: ``except skip.Exception`` arm in `_pytest/python.py::importtestmodule` (l. 534-542)
#: catches around the module import.
#:
#: **Deliberately narrower than :data:`SKIPPED_EXCEPTIONS`**: ``unittest.SkipTest`` is
#: absent.  pytest converts that class to a skip only in ``pytest_runtest_makereport``,
#: which is a *runtest* hook; nothing converts it during collection, so a module raising
#: ``unittest.SkipTest`` at import is an ordinary collection error under pytest, and adding
#: it here would turn one of pytest's errors into a silent non-collection.
MODULE_SKIP_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (_Skipped, _StubSkipped)

#: "The test declared itself an expected failure while running."  Port of
#: `_pytest/outcomes.py::XFailed`, consumed by `_pytest/skipping.py` l. 279-282, which turns
#: it into ``outcome="skipped"`` plus ``wasxfail`` — i.e. the ``xfailed`` category —
#: **whatever phase raised it**.
#:
#: Checked *before* :data:`FAILED_EXCEPTIONS`: pytest declares ``class XFailed(Failed)``, so
#: on real pytest the order is load-bearing.  rustest's ``XFailed`` happens to derive
#: straight from ``Exception``, which makes the order look irrelevant — it is not, and
#: relying on that accident would break the day the hierarchy is aligned.
XFAILED_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (_XFailed,)

#: "The test failed on purpose" — ``pytest.fail()``.  Listed for documentation and for the
#: unittest translation (an unexpected success becomes one of these); classification does
#: not branch on it, because :func:`report_for_phase`'s **default** is already ``failed``,
#: which is also where a plain ``AssertionError`` and every other exception land.
FAILED_EXCEPTIONS: Final[tuple[type[BaseException], ...]] = (_Failed, _StubFailed)


def _visible_frames(exc: BaseException) -> list[bool]:
    """Which of *exc*'s traceback frames belong in a user-facing message.

    Port of the two filters pytest applies before printing a traceback, both of which key on
    the **frame**, never on the text of the message:

    * `_pytest/unittest.py::TestCaseFunction._traceback_filter` (l. 355-364) drops frames
      whose globals carry ``__unittest`` — the marker ``unittest.case`` sets on itself — so a
      failed ``assertEqual`` points at the assertion, not at four frames of
      ``testPartExecutor``/``_callTestMethod`` plumbing;
    * `_pytest/_code/code.py::TracebackEntry.ishidden` drops frames whose locals set
      ``__tracebackhide__``, which is how ``rustest.fail()`` and every user-written assertion
      helper keep themselves out of the report.

    This worker's own frames go too: ``_run_phases`` and ``_run_call`` are between the
    protocol loop and the test, and naming them in a failure message would send an operator
    reading it into the runner instead of into their test.
    """
    visible: list[bool] = []
    tb = exc.__traceback__
    while tb is not None:
        frame_globals = tb.tb_frame.f_globals
        hidden = (
            frame_globals.get("__name__") == __name__
            or bool(frame_globals.get("__unittest"))
            or bool(tb.tb_frame.f_locals.get("__tracebackhide__"))
        )
        visible.append(not hidden)
        tb = tb.tb_next
    return visible


def _format_exception(exc: BaseException) -> str:
    """A failure message: the exception's traceback, runner and plumbing frames removed.

    pytest's own fallback is ported with the filter: *"if not ntraceback: ntraceback =
    traceback"* — a traceback filtered down to nothing is shown unfiltered, because an
    exception with no visible frame at all is less useful than a noisy one.
    """
    rendered = traceback.TracebackException.from_exception(exc)
    visible = _visible_frames(exc)
    if any(visible) and len(visible) == len(rendered.stack):
        rendered.stack = traceback.StackSummary.from_list(
            [frame for frame, keep in zip(rendered.stack, visible, strict=True) if keep]
        )
    return "".join(rendered.format()).rstrip()


# -- marks at execution -----------------------------------------------------


@dataclass(frozen=True)
class Skip:
    """The result of :func:`evaluate_skip_marks`.  Port of `_pytest/skipping.py::Skip`."""

    reason: str = "unconditional skip"


@dataclass(frozen=True)
class Xfail:
    """The result of :func:`evaluate_xfail_marks`.  Port of `_pytest/skipping.py::Xfail`."""

    reason: str
    run: bool
    strict: bool
    raises: tuple[type[BaseException], ...] | None


def _fail(message: str) -> NoReturn:
    """Raise rustest's ``Failed`` — the worker's stand-in for ``fail(..., pytrace=False)``."""
    raise _Failed(message)


def evaluate_condition(
    mark: MarkSpec, condition: object, namespace: Mapping[str, object]
) -> tuple[bool, str]:
    """Port of `_pytest/skipping.py::evaluate_condition` (l. 88-158).

    A **string** condition is compiled and ``eval``'d against ``os``/``sys``/``platform``
    plus the test function's own globals — pytest's ``globals_`` dict, minus ``config`` and
    the ``pytest_markeval_namespace`` hook, neither of which exists on this wire.  Anything
    else is ``bool()``-ed.

    Evaluating strings is not optional even though ``decorators.py::_normalize_args``
    already reduces *skipif* strings at decoration time: it does **not** touch ``xfail``, so
    ``@pytest.mark.xfail("1 == 1", reason=...)`` would otherwise be judged by the truthiness
    of the source text — always true, for every condition, including the ones meant to be
    false.  Probed: pytest reports that test XFAIL, and reports the same test with a
    would-be-false string condition FAILED.

    Both failure shapes pytest turns into ``fail(..., pytrace=False)`` are ported, message
    included: a broken condition, and a **boolean condition with no ``reason=``** — probed,
    pytest reports ``@pytest.mark.skipif(True)`` as an ERROR at setup, not as a skip.
    Swallowing that would skip a test whose author never said why.
    """
    result: object
    if isinstance(condition, str):
        globals_: dict[str, object] = {"os": os, "sys": sys, "platform": platform}
        globals_.update(namespace)
        try:
            condition_code = compile(condition, f"<{mark.name} condition>", "eval")
            # `eval` is the ported behaviour, not a shortcut: the string is an expression the
            # user wrote in their own test file, which this process is already importing and
            # executing, so it grants no capability the test module does not already have.
            # `ast.literal_eval` cannot express `sys.platform == "win32"`, which is the whole
            # point of a string condition.  Source: `_pytest/skipping.py` l. 115-118.
            result = eval(condition_code, globals_)  # noqa: S307 - pytest's own semantics
        except SyntaxError as exc:
            _fail(
                "\n".join(
                    [
                        f"Error evaluating {mark.name!r} condition",
                        "    " + condition,
                        "    " + " " * (exc.offset or 0) + "^",
                        "SyntaxError: invalid syntax",
                    ]
                )
            )
        except Exception as exc:  # noqa: BLE001 - pytest reports, never propagates
            _fail(
                "\n".join(
                    [
                        f"Error evaluating {mark.name!r} condition",
                        "    " + condition,
                        *traceback.format_exception_only(type(exc), exc),
                    ]
                )
            )
    else:
        try:
            result = bool(condition)
        except Exception as exc:  # noqa: BLE001 - pytest reports, never propagates
            _fail(
                "\n".join(
                    [
                        f"Error evaluating {mark.name!r} condition as a boolean",
                        *traceback.format_exception_only(type(exc), exc),
                    ]
                )
            )

    reason = mark.kwargs.get("reason", None)
    if reason is None:
        if isinstance(condition, str):
            return bool(result), "condition: " + condition
        _fail(
            f"Error evaluating {mark.name!r}: "
            + "you need to specify reason=STRING when using booleans as conditions."
        )
    return bool(result), str(reason)


def _reason(mark: MarkSpec) -> str:
    """A mark's ``reason=`` as a string, treating an explicit ``None`` as absent.

    pytest reads ``mark.kwargs.get("reason", "")`` and never sees a ``None``, because
    ``pytest.mark.xfail`` stores only the keywords the user actually passed.  rustest's
    ``MarkGenerator`` normalises instead: ``@pytest.mark.xfail()`` arrives carrying
    ``reason=None``, ``raises=None``, ``run=True``, ``strict=False`` in full.  Without this,
    ``str(None)`` would put the literal word ``"None"`` in the report where pytest puts
    nothing.  (The *missing-reason-with-a-boolean-condition* error in
    :func:`evaluate_condition` is a different rule and still fires — there, ``None`` means
    the user genuinely did not say why.)
    """
    reason = mark.kwargs.get("reason")
    return "" if reason is None else str(reason)


def _conditions(mark: MarkSpec) -> tuple[object, ...]:
    """``mark.kwargs["condition"]`` if given, else the positional args.

    Port of the identical four lines in `evaluate_skip_marks` (l. 171-174) and
    `evaluate_xfail_marks` (l. 219-222): the keyword form takes precedence and collapses to
    a single condition, the positional form is a disjunction.
    """
    if "condition" in mark.kwargs:
        return (mark.kwargs["condition"],)
    return mark.args


def evaluate_skip_marks(marks: Sequence[MarkSpec], namespace: Mapping[str, object]) -> Skip | None:
    """Port of `_pytest/skipping.py::evaluate_skip_marks` (l. 168-193) — the #131 root fix.

    **Every ``skipif`` is considered before any ``skip``**, which is pytest's structure (two
    separate loops), not an accident of ordering: a test carrying both is skipped for the
    *condition's* reason when the condition holds.  Within each loop, *marks* is closest-first
    — the order :func:`collect_module` records and pytest's ``iter_markers`` yields — so the
    nearest mark wins.

    An unconditional ``skipif`` (no args at all) skips with ``reason=`` as-is, including the
    empty string; that is pytest's l. 177-179 and it is why the empty-reason case is not an
    error here while the *boolean-condition-without-reason* case is.
    """
    for mark in marks:
        if mark.name != "skipif":
            continue
        conditions = _conditions(mark)
        if not conditions:
            return Skip(_reason(mark))
        for condition in conditions:
            result, reason = evaluate_condition(mark, condition, namespace)
            if result:
                return Skip(reason)

    for mark in marks:
        if mark.name != "skip":
            continue
        try:
            return Skip(*(str(arg) for arg in mark.args), **_skip_kwargs(mark))
        except TypeError as exc:
            raise TypeError(str(exc) + " - maybe you meant pytest.mark.skipif?") from None
    return None


def _skip_kwargs(mark: MarkSpec) -> dict[str, str]:
    """``Skip``'s keyword arguments, stringified.

    ``@pytest.mark.skip`` reaches this worker through ``decorators.py::skip_decorator``,
    which stores ``__rustest_skip__`` as whatever it was handed.  That is a string for every
    shape the shim now produces — the bare (uncalled) form used to hand it the *test
    function*, which is defect #136, fixed by ``decorators.py::BareOrFactoryMark``.  The
    coercion is kept as a boundary guard rather than removed with the defect: this attribute
    is public and a user may set it directly, ``Skip.reason`` is a ``str``, and a reason that
    is quietly not one would reach the wire and the report.
    """
    return {str(key): str(value) for key, value in mark.kwargs.items()}


def _as_raises(raw: object) -> tuple[type[BaseException], ...] | None:
    """``xfail(raises=...)`` as a tuple of exception classes, or ``None`` for "any".

    pytest passes the value straight to ``isinstance`` (`skipping.py` l. 286-289); a value
    that is not an exception class would therefore raise ``TypeError`` from deep inside the
    reporting path, where it has nowhere to go.  This validates up front and reports it as a
    setup failure naming the mark instead — the same class of "loud beats mysterious" choice
    the rest of this worker makes.

    (pytest's ``AbstractRaises`` branch — ``raises=pytest.RaisesGroup(...)`` — is not ported;
    rustest has no such object.)
    """
    if raw is None:
        return None
    candidates: tuple[object, ...] = (
        cast(tuple[object, ...], raw) if isinstance(raw, tuple) else (raw,)
    )
    for candidate in candidates:
        if not (isinstance(candidate, type) and issubclass(candidate, BaseException)):
            _fail(
                "Error evaluating 'xfail': raises= must be an exception class or a tuple of "
                + f"them, got {raw!r}"
            )
    return cast(tuple[type[BaseException], ...], candidates)


def evaluate_xfail_marks(
    marks: Sequence[MarkSpec], namespace: Mapping[str, object]
) -> Xfail | None:
    """Port of `_pytest/skipping.py::evaluate_xfail_marks` (l. 213-235).

    ``strict`` defaults to pytest's ``xfail_strict`` ini, which is ``False``
    (`skipping.py::pytest_addoption` l. 40-46).  The ini is **not on this wire**, so a suite
    that sets ``xfail_strict = true`` gets non-strict behaviour here; closing that needs a
    protocol field, and is recorded as such rather than guessed.
    """
    for mark in marks:
        if mark.name != "xfail":
            continue
        run = bool(mark.kwargs.get("run", True))
        strict = bool(mark.kwargs.get("strict", False))
        raises = _as_raises(mark.kwargs.get("raises", None))
        conditions = _conditions(mark)
        if not conditions:
            return Xfail(_reason(mark), run, strict, raises)
        for condition in conditions:
            result, reason = evaluate_condition(mark, condition, namespace)
            if result:
                return Xfail(reason, run, strict, raises)
    return None


# -- outcome classification, by type ----------------------------------------


@dataclass(frozen=True)
class PhaseReport:
    """One phase's outcome — this worker's ``TestReport`` for ``setup``/``call``/``teardown``.

    ``outcome`` holds only pytest's three ``TestReport.outcome`` values
    (`_pytest/reports.py` l. 64); the six-value wire *category* is :attr:`status`, derived
    exactly as pytest's ``pytest_report_teststatus`` chain derives its letters.
    """

    phase: str
    outcome: str
    message: str | None = None
    #: Set (possibly to ``""``) when this report was promoted by an xfail — pytest's
    #: ``rep.wasxfail``.  Presence is the signal, **not** truthiness: a
    #: ``@unittest.expectedFailure`` carries an empty reason and is still an xfail.
    wasxfail: str | None = None

    @property
    def plain_pass(self) -> bool:
        """A pass with nothing to report — pytest's ``.``, the only status that is not news."""
        return self.outcome == "passed" and self.wasxfail is None

    @property
    def status(self) -> str:
        """The wire category.  Port of the ``pytest_report_teststatus`` hook chain:

        * `_pytest/skipping.py` l. 310-316 runs first and owns ``wasxfail``: a *skipped*
          report becomes ``xfailed``, a *passed* one ``xpassed``;
        * `_pytest/runner.py` l. 214-223 turns a failed ``setup``/``teardown`` into
          ``error``;
        * `_pytest/terminal.py` l. 325-337 restates the same rule as the fallback
          (``if report.when in ("collect", "setup", "teardown") and outcome == "failed"``).

        So ``failed`` means "the body ran and disagreed" and ``error`` means "the body never
        properly ran".  A *strict* xpass is deliberately not in the xfail branch: pytest
        rewrites it to ``rep.outcome = "failed"`` before this hook sees it (l. 300-303), so
        it arrives here already carrying no ``wasxfail``.
        """
        if self.wasxfail is not None:
            if self.outcome == "skipped":
                return "xfailed"
            if self.outcome == "passed":
                return "xpassed"
        if self.outcome == "skipped":
            return "skipped"
        if self.outcome == "passed":
            return "passed"
        return "error" if self.phase in _ERROR_PHASES else "failed"


def report_for_phase(phase: str, exc: BaseException | None, xfailed: Xfail | None) -> PhaseReport:
    """Classify one phase **by exception type**, then apply pytest's xfail promotion.

    Two stages, both ports, and neither one does any string matching — the type *is* the
    classification (`_pytest/outcomes.py`: ``Skipped``, ``Failed``, ``XFailed`` are classes
    precisely so that this decision never has to read a message):

    1. *Raw outcome.*  :data:`XFAILED_EXCEPTIONS` -> ``skipped`` **with** ``wasxfail``
       (`skipping.py` l. 279-282, which fires for any phase);
       :data:`SKIPPED_EXCEPTIONS` -> ``skipped``; no exception -> ``passed``; **everything
       else** -> ``failed``, which is where ``AssertionError``, ``pytest.fail()``'s
       ``Failed`` and an arbitrary ``ValueError`` all land.
    2. *The xfail mark's promotion* (`skipping.py` l. 283-306), applied only to a report that
       is not already skipped — pytest's ``elif not rep.skipped and xfailed``, which is why a
       ``skip`` mark beats an ``xfail`` mark (probed: SKIPPED):

       * an exception matching ``raises=`` (or no ``raises=`` at all) -> ``skipped`` +
         ``wasxfail``, i.e. ``xfailed`` — **including in setup and teardown**, probed: a test
         with a broken fixture and an ``xfail`` mark reports XFAIL, not ERROR;
       * an exception *not* matching ``raises=`` -> ``failed``;
       * no exception, and only in the ``call`` phase -> ``xpassed``, or ``failed`` with
         pytest's ``"[XPASS(strict)] "`` prefix when ``strict=True`` (l. 300-303).
    """
    if exc is None:
        outcome, message, wasxfail = "passed", None, None
    elif isinstance(exc, XFAILED_EXCEPTIONS):
        outcome, message, wasxfail = "skipped", None, str(exc)
    elif isinstance(exc, SKIPPED_EXCEPTIONS):
        outcome, message, wasxfail = "skipped", str(exc), None
    else:
        outcome, message, wasxfail = "failed", _format_exception(exc), None

    if wasxfail is None and outcome != "skipped" and xfailed is not None:
        if exc is not None:
            if xfailed.raises is None or isinstance(exc, xfailed.raises):
                outcome, message, wasxfail = "skipped", None, xfailed.reason
            else:
                outcome = "failed"
        elif phase == "call":
            if xfailed.strict:
                outcome, message = "failed", "[XPASS(strict)] " + xfailed.reason
            else:
                outcome, wasxfail = "passed", xfailed.reason

    if wasxfail is not None and not message:
        message = wasxfail or None
    return PhaseReport(phase=phase, outcome=outcome, message=message, wasxfail=wasxfail)


def reduce_reports(reports: Sequence[PhaseReport]) -> PhaseReport:
    """Collapse pytest's up-to-three reports into the one the wire carries.

    **The earliest phase that is not a plain pass wins.**  pytest prints one line per report
    and lets the terminal sum them; ``WorkerResponse::TestResult`` carries a single status,
    and its docs put that reduction on the worker precisely so the orchestrator never has to
    guess how many phases there were.

    The rule is the reading of pytest's own output, checked against every probed shape:
    a broken fixture is ``ERROR`` (setup); a passing body with a broken teardown is
    ``PASSED`` + ``ERROR`` and reduces to ``error``; a *failing* body with a broken teardown
    is ``FAILED`` + ``ERROR`` and reduces to ``failed``, which is the headline pytest itself
    puts in its short summary.  It also gets the xfail corners right for free: an ``xpassed``
    call is not a plain pass, so it wins over a later teardown report.
    """
    for report in reports:
        if not report.plain_pass:
            return report
    return reports[-1]


# -- unittest, via an explicit TestResult -----------------------------------

_SysExcInfo = (
    tuple[type[BaseException], BaseException, types.TracebackType] | tuple[None, None, None]
)


class _UnittestOutcomeRecorder(unittest.TestResult):
    """The ``unittest.TestResult`` this worker hands to ``TestCase(name)(result)``.

    **This is the #129 fix, and it is a translation, not a reinterpretation.**  pytest does
    not read ``result.failures`` / ``result.errors`` either — its ``TestCaseFunction`` *is*
    the result object, and every callback converts back into the exception the ordinary
    report machinery already understands (`_pytest/unittest.py` l. 267-314):

    | ``TestResult`` callback  | pytest raises        | classified as | wire status |
    | ------------------------ | -------------------- | ------------- | ----------- |
    | ``addSuccess``           | *nothing*            | passed        | ``passed``  |
    | ``addFailure``           | the raw exception    | failed        | ``failed``  |
    | ``addError``             | the raw exception    | failed        | ``failed``  |
    | ``addSkip``              | ``skip.Exception``   | skipped       | ``skipped`` |
    | ``addExpectedFailure``   | ``xfail.Exception``  | xfail         | ``xfailed`` |
    | ``addUnexpectedSuccess`` | ``fail.Exception``   | failed        | ``failed``  |

    Two rows are worth stating because the obvious guess is wrong, and both were **probed
    against pytest 8.4.2** rather than assumed:

    * ``addError`` is ``failed``, not ``error``.  pytest drives the whole ``TestCase`` inside
      the *call* phase, so a ``ValueError`` in the body — or in ``setUp`` — is a call-phase
      failure (``F``).  ``error`` is reserved for what breaks *outside* the test, which for a
      ``TestCase`` means ``setUpClass`` (a class-scoped fixture, hence the setup phase).
    * ``addUnexpectedSuccess`` is ``failed``, not ``xpassed``.  pytest says so in a comment
      at l. 307 — *"Preserve unittest behaviour - fail the test. Explicitly not an XPASS."* —
      and probed, ``@unittest.expectedFailure`` over a passing test reports FAILED.

    Recording an ordered *list* is also pytest's shape: ``_addexcinfo`` appends and
    ``pytest_runtest_makereport`` pops ``[0]`` (l. 370-371), so when a body fails **and**
    ``tearDown`` raises, the body's failure is what the test is reported as.  Probed: pytest
    prints ``FAILED`` then a separate teardown ``ERROR`` for exactly that shape.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        descriptions: bool | None = None,
        verbosity: int | None = None,
    ) -> None:
        super().__init__(stream, descriptions, verbosity)
        #: Callbacks converted to exceptions, in the order unittest reported them.
        self.outcomes: list[BaseException] = []

    def first_outcome(self) -> BaseException | None:
        """The exception the call phase is reported as — pytest's ``_excinfo.pop(0)``."""
        return self.outcomes[0] if self.outcomes else None

    def _record_raw(self, err: _SysExcInfo) -> None:
        value = err[1]
        if value is None:  # pragma: no cover - unittest never passes the empty triple here
            value = RuntimeError("unittest reported a failure with no exception")
        self.outcomes.append(value)

    def addError(self, test: unittest.TestCase, err: _SysExcInfo) -> None:
        self._record_raw(err)

    def addFailure(self, test: unittest.TestCase, err: _SysExcInfo) -> None:
        self._record_raw(err)

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        self.outcomes.append(_Skipped(reason))

    def addExpectedFailure(self, test: unittest.TestCase, err: _SysExcInfo) -> None:
        self.outcomes.append(_XFailed(""))

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        self.outcomes.append(_Failed("Unexpected success"))

    def addSuccess(self, test: unittest.TestCase) -> None:
        """Nothing to record — pytest's ``addSuccess`` is a ``pass`` too (l. 313-314)."""


def _is_unittest_skipped(obj: object) -> bool:
    """Port of `_pytest/unittest.py::_is_skipped` (l. 390-392) — ``@unittest.skip`` applied."""
    return bool(_safe_getattr(obj, "__unittest_skip__", False))


def _unittest_class_fixture(cls: type) -> Callable[..., Iterator[None]] | None:
    """``setUpClass``/``tearDownClass`` as a class-scoped autouse fixture, or ``None``.

    Port of `_pytest/unittest.py::UnitTestCase._register_unittest_setup_class_fixture`
    (l. 116-170).  It has to be a *fixture* rather than something the call phase does,
    because ``unittest.TestCase.run`` does not invoke ``setUpClass`` at all — ``TestSuite``
    does — so a worker that only calls ``TestCase(name)(result)`` would run every method of a
    class whose class-level setup never happened.  That is silently wrong for a mainstream
    shape, and it is also what puts a ``setUpClass`` failure in the **setup** phase, where
    probed pytest reports it as ERROR rather than FAILED.

    ``doClassCleanups`` and the ``tearDown_exceptions`` drain are ported with it (l. 123-161),
    including unittest's own rule that cleanups run for ``Exception`` but not for a bare
    ``BaseException``.

    Not ported: ``parsefactories`` over the instance — an autouse ``@pytest.fixture``
    written inside a ``TestCase`` body is not picked up.  A recorded gap, not a silent one.
    (``_register_unittest_setup_method_fixture`` used to be listed here too; Phase 4 Task 1c
    ported it as :func:`_unittest_setup_method_fixture`.)
    """
    setup = _safe_getattr(cls, "setUpClass", None)
    teardown = _safe_getattr(cls, "tearDownClass", None)
    if setup is None and teardown is None:
        return None
    cleanup = cast(Callable[[], object], _safe_getattr(cls, "doClassCleanups", lambda: None))

    def process_teardown_exceptions() -> None:
        exc_infos = _safe_getattr(cls, "tearDown_exceptions", None)
        if not exc_infos:
            return
        exceptions: list[Any] = [exc for (_type, exc, _tb) in cast(Sequence[Any], exc_infos)]
        if len(exceptions) == 1:
            raise cast(BaseException, exceptions[0])
        raise BaseExceptionGroup("Unittest class cleanup errors", exceptions)

    def unittest_setup_class_fixture() -> Iterator[None]:
        if setup is not None:
            try:
                _ = cast(Callable[[], object], setup)()
            except Exception:
                _ = cleanup()
                process_teardown_exceptions()
                raise
        yield
        try:
            if teardown is not None:
                _ = cast(Callable[[], object], teardown)()
        finally:
            _ = cleanup()
            process_teardown_exceptions()

    return unittest_setup_class_fixture


# -- capture ----------------------------------------------------------------


class _Capture:
    """Per-test ``stdout``/``stderr``, captured at the **file-descriptor** level.

    Phase 3 Task 2 replaced the stream-level redirect this used to be.  The old version was
    v1's ``capsys`` semantics — a ``print()`` was captured and an ``os.write(1, ...)`` was
    not — and the gap was not merely a missing feature: fd 1 *was the protocol channel*, so a
    test writing to it emitted JSON-adjacent bytes into the orchestrator's parser.  That
    hazard is closed twice over now.  :func:`_detach_protocol_stream` moves the protocol off
    fd 1 before any test module is imported, and this class redirects fd 1 into a temporary
    file for the duration of every test, so the write becomes **captured output attributed to
    the test that made it**.

    Structure is pytest's default capture mode, `--capture=fd`
    (`_pytest/capture.py::CaptureManager.start_global_capturing` -> ``MultiCapture`` of
    ``FDCapture``), reduced to out and err: fd 0 is the worker's request channel and nothing
    here may touch it.  ``rustest._v2_builtins.FdCapture`` also redirects ``sys.stdout`` /
    ``sys.stderr`` onto the same temporary file, which is what keeps a ``print()`` and a raw
    fd write in the order they happened.

    **One capture per worker, suspended between tests.**  The temporary files and the saved
    fds are made once and reused (``snap()`` truncates), because creating two temporary files
    per test would put a filesystem round trip on the per-test overhead this engine exists to
    minimise.  Between tests the capture is *suspended* — the fds go back to the worker's own
    stderr — which is what keeps boundary teardown output out of the next test's capture
    (:func:`drain_boundaries`).
    """

    def __init__(self) -> None:
        super().__init__()
        self._out: _builtins.FdCapture | None = None
        self._err: _builtins.FdCapture | None = None

    def _ensure(self) -> tuple[_builtins.FdCapture, _builtins.FdCapture]:
        """Build the two fd captures on first use, started and immediately suspended.

        Lazy rather than built at ``init`` so a worker that only ever collects — and every
        unit test that imports this module — pays nothing and, more importantly, does not
        have two live ``dup``s of its stdio for its whole life.
        """
        if self._out is None or self._err is None:
            self._out = _builtins.FdCapture(1)
            self._err = _builtins.FdCapture(2)
            self._out.start()
            self._err.start()
            self._out.suspend()
            self._err.suspend()
        return self._out, self._err

    @contextlib.contextmanager
    def window(self) -> Iterator[None]:
        """Capture everything written to fd 1 / fd 2 inside the block.

        Under ``-s`` / ``--no-capture`` this is a no-op, so a test's ``print`` lands on
        whatever ``sys.stdout`` is, which :func:`main` has already rebound to the worker's
        **stderr**.  The orchestrator forwards that stderr verbatim through
        ``RunReport::worker_stderr``.

        So `-s` under v2 means "not captured, not attributed to a test, forwarded live-ordered
        per worker" rather than pytest's "written straight to the terminal".  Documented
        divergence; the alternative — an extra inherited fd per worker — is plumbing for a
        flag whose whole purpose is `pdb`-style debugging, where `-n 1` is the sane setting
        anyway.
        """
        if not capture_enabled():
            yield
            return
        out, err = self._ensure()
        out.resume()
        err.resume()
        try:
            yield
        finally:
            out.suspend()
            err.suspend()

    @contextlib.contextmanager
    def disabled(self) -> Iterator[None]:
        """Suspend the capture for a block — ``capsys.disabled()``'s global half.

        Guarded on :attr:`_builtins.FdCapture.started` rather than assuming the capture is
        live, because ``suspend`` is idempotent and ``resume`` is not *conditional*: called
        while the capture was already suspended — between tests, or under ``-s`` — the
        ``finally`` would **start** it, leaving the next boundary drain captured and
        attributed to whichever test ran next.  pytest's ``global_and_fixture_disabled``
        makes the same check (l. 840-843, ``do_global = ... and is_started()``).
        """
        out, err = self._out, self._err
        if out is None or err is None or not (out.started and err.started):
            yield
            return
        out.suspend()
        err.suspend()
        try:
            yield
        finally:
            out.resume()
            err.resume()

    @property
    def broken(self) -> bool:
        """True once a test has closed one of the streams out from under the capture.

        ``sys.stdout.close()`` in a test body closes **the capture's temporary file** — the
        redirect makes them the same object — and every later read raises ``ValueError: I/O
        operation on closed file``.  Left unguarded that exception escapes
        :func:`execute_test`, kills the worker with exit 1 mid-stream, and leaves every
        queued test unanswered; worse, exit 1 is indistinguishable from an uncaught
        traceback, i.e. from protocol drift.

        pytest has the same failure and reports it as a **test failure**, because reading the
        capture is part of its per-phase protocol (`_pytest/capture.py::CaptureFixture.snap`
        raising out of the item's capture context).  Probed: a test doing
        ``sys.stdout.close()`` reports ``FAILED`` for the call phase and ``ERROR`` for
        teardown.  :func:`_run_phases` reproduces that pair by consulting this flag after
        each phase.
        """
        return any(capture is not None and capture.broken for capture in (self._out, self._err))

    def drain(self) -> tuple[str, str]:
        """This test's ``(stdout, stderr)``, or markers if the test closed one of them.

        Never raises: this runs after the phases are over, so an exception here would
        destroy a result that has already been computed.  A capture the test broke is torn
        down and forgotten, so the **next** test gets a working one — strictly kinder than
        pytest, whose global capture stays broken, and invisible to the test that broke it.
        """
        if self._out is None or self._err is None:
            return "", ""
        try:
            out = self._out.snap()
        except ValueError:
            out = CAPTURE_CLOSED_MESSAGE
        try:
            err = self._err.snap()
        except ValueError:
            err = CAPTURE_CLOSED_MESSAGE
        if self.broken:
            self.close()
        return out, err

    def close(self) -> None:
        """Restore the real fds and drop the temporary files. Never raises."""
        for capture in (self._out, self._err):
            if capture is None:
                continue
            try:
                capture.done()
            except (OSError, ValueError):  # pragma: no cover - best effort at teardown
                pass
        self._out = None
        self._err = None


#: What a test that closed its own stream gets instead of captured output — and, when the
#: closure is what broke an otherwise-passing phase, that phase's failure message.
CAPTURE_CLOSED_MESSAGE: Final = "<capture closed by the test>"

#: Set to ``no`` by the orchestrator under ``-s`` / ``--no-capture``
#: (`src/v2/collect.rs::CAPTURE_ENV`, which **must be renamed in the same commit** as this).
#:
#: An environment variable rather than a protocol field because capture is *spawn*
#: configuration: it is constant for a worker's whole life and identical for every worker in
#: the pool, exactly like the interpreter path and the argv.  Putting it on the wire would
#: mean a `PROTOCOL_VERSION` bump for a value that can never vary between two messages.
CAPTURE_ENV: Final = "RUSTEST_V2_CAPTURE"


def capture_enabled() -> bool:
    """Whether a test's ``sys.stdout``/``sys.stderr`` are redirected into :class:`_Capture`.

    Read per call rather than cached in a module constant so a test of this worker can set
    the variable and observe the effect without reloading the module.
    """
    return os.environ.get(CAPTURE_ENV, "").lower() not in ("no", "0", "false")


#: The worker's one capture, created on first use by :func:`_worker_capture`.
#:
#: Worker-lived rather than per test for the reason :class:`_Capture` gives: two temporary
#: files and four ``dup``s per test would be a filesystem round trip on the per-test overhead
#: budget.  Reset to ``None`` only when a test breaks it.
_capture: _Capture | None = None


def _worker_capture() -> _Capture:
    global _capture
    if _capture is None:
        _capture = _Capture()
        _builtins.set_global_capture_control(_capture.disabled)
    return _capture


def reset_capture() -> None:
    """Tear the worker's capture down, so the next test rebuilds it. **For in-process drivers.**

    A real worker never calls this: ``sys.stdout``/``sys.stderr`` are rebound once in
    :func:`main` and stay put, so one capture is correct for the process's whole life and
    :class:`_Capture` caches the streams it must restore accordingly.

    Anything that drives :func:`execute_test` *inside another test runner* breaks that
    assumption, because that runner swaps ``sys.stderr`` per test — a ``capsys`` fixture in
    this repo's own suite does exactly that.  A capture built during test A would then
    restore test A's stream at the end of test B, and B's output would surface under A's
    capture: measured, as a shutdown-drain message landing in pytest's global capture instead
    of in the ``capsys`` that was asserting on it.
    """
    global _capture
    capture, _capture = _capture, None
    if capture is not None:
        capture.close()
    _builtins.set_global_capture_control(None)


# -- the execute op ---------------------------------------------------------


def _condition_namespace(plan: ExecutionPlan) -> Mapping[str, object]:
    """The globals a string ``skipif``/``xfail`` condition is evaluated against.

    pytest uses ``item.obj.__globals__`` (`skipping.py` l. 113-114).  For a ``TestCase``
    method — where the plan carries no function — the module's namespace is the same dict.
    """
    globals_ = _safe_getattr(plan.func, "__globals__", None)
    if isinstance(globals_, dict):
        return cast(Mapping[str, object], globals_)
    return vars(plan.module)


def _run_call(plan: ExecutionPlan, runner: FixtureRunner, kwargs: Mapping[str, object]) -> None:
    """Run the test body: a ``TestCase`` through ``unittest``, anything else directly."""
    if plan.unittest_case is not None:
        recorder = _UnittestOutcomeRecorder()
        # The instance the SETUP phase built (`FixtureRunner._build_instance`), never a
        # fresh one: `setup_method` and any instance-touching fixture have already run
        # against it, and constructing a second here would silently discard everything they
        # did. pytest caches the same object on the item for the same reason.
        built = runner.instance
        case = (
            built
            if isinstance(built, unittest.TestCase)
            else cast(Callable[[str], unittest.TestCase], plan.unittest_case)(
                plan.unittest_method or "runTest"
            )
        )
        case(result=recorder)
        outcome = recorder.first_outcome()
        if outcome is not None:
            raise outcome
        return

    func = plan.func
    if func is None:  # pragma: no cover - collection always supplies one of the two
        raise RuntimeError(f"collected test {plan.id!r} has no function and no unittest case")
    if runner.instance is not None:
        result = func(runner.instance, **kwargs)
    else:
        result = func(**kwargs)
    _consume_test_result(plan, runner, result)


def _consume_test_result(plan: ExecutionPlan, runner: FixtureRunner, result: object) -> None:
    """Finish a test body whose call has returned — the **false-green guard**.

    An `async def` test called like a sync one returns a coroutine and raises nothing, so the
    run reports PASSED for a body that never executed. Measured before this existed:
    ``async def test(): assert 1 == 2`` printed ``1 passed`` under v2 while both pytest and
    rustest v1 printed ``1 failed``.

    The shape of the check is **pytest's**, `_pytest/python.py::pytest_pyfunc_call`
    (pytest 8.4.2, l. 150-168)::

        result = testfunction(**testargs)
        if hasattr(result, "__await__") or hasattr(result, "__aiter__"):
            async_fail(pyfuncitem.nodeid)
        elif result is not None:
            warnings.warn(PytestReturnNotNoneWarning(...))

    ...with one substitution, and it is **conditional on the asyncio mode**: where pytest
    calls ``async_fail`` for an awaitable ("async def functions are not natively supported.
    You need to install a suitable plugin"), rustest is that plugin *when it has been asked to
    be*. :meth:`FixtureRunner.test_loop_scope` answers that — a scope when the test is an
    asyncio test, ``None`` when ``asyncio_mode = strict`` and nothing marked it — and ``None``
    routes to pytest's own failure. Probed on pytest 8.4.2 + pytest-asyncio 1.2.0 in strict
    mode: an unmarked ``async def test_x`` reports **failed** with exactly that message.

    The duck-typed ``__await__`` test rather than ``inspect.iscoroutine`` is the point of
    porting it: a custom awaitable — an object with ``__await__``, a ``Future``, an ``anyio``
    task wrapper — is not a coroutine, and the narrower check dropped it silently, which is
    the same false-green one layer along.

    ``__aiter__`` without ``__await__`` is an **async generator object**, which nothing can
    run as a test: pytest fails it and pytest-asyncio refuses it at collection
    (:func:`_async_generator_xfail`). A plain function that *returns* one lands here, and
    gets pytest's own failure with pytest's own message.

    The third branch — a test that ``return``s a non-``None`` value — is pytest's
    ``PytestReturnNotNoneWarning``. v2 has no warnings channel, so it is silently ignored,
    exactly as before; the outcome is identical either way.

    **A fresh ``contextvars.Context`` per test body, and it is isolation, not tidiness.**
    pytest-asyncio builds one per item — ``context = contextvars.copy_context()`` in
    `PytestAsyncioFunction.runtest` (l. 465-473), handed to `_synchronize_coroutine`
    (l. 708-723) as ``runner.run(coro, context=context)``. Without it every test sharing a
    runner shares **one** context, which is the runner's own, so a ``ContextVar`` set in one
    test is still set in the next. Measured under ``asyncio_mode = auto`` with both loop
    scopes ``session`` — the acceptance shape, and Member Designer's config — where pytest
    printed ``unset`` for the second test and rustest printed ``from-test-one``. Anything
    that stores request state, a DB session or a trace id in a ``ContextVar`` (structlog,
    SQLAlchemy's async session, OpenTelemetry) leaks across tests on that shape, and leaks
    *forward*, so the symptom lands on a test that did nothing wrong.

    The **fixture** call sites deliberately pass no context and run in the runner's own,
    which is where a fixture's ``ContextVar`` writes have to end up for a test to see them:
    the oracle achieves the same reachability the long way round, copying a context per
    fixture and then replaying the changes into the caller's with
    ``_apply_contextvar_changes`` (l. 385-414). Both routes make a session fixture's
    ``ContextVar`` visible to the tests it serves — probed, ``from-fixture`` on both runners,
    before and after this change. What the oracle additionally buys with the long route is a
    *reset* finalizer at the end of the fixture's scope, which this does not have; see the
    named gaps in the Task 1 report.
    """
    if hasattr(result, "__await__"):
        scope = runner.test_loop_scope(plan)
        if scope is None:
            _fail(ASYNC_NOT_SUPPORTED_MESSAGE)
        import contextvars

        _ = runner.run_coroutine(
            result,
            scope,
            _asyncio_timeout(_asyncio_mark(plan.marks)),
            contextvars.copy_context(),
        )
    elif hasattr(result, "__aiter__"):
        _fail(ASYNC_NOT_SUPPORTED_MESSAGE)


def drain_boundaries(plan: ExecutionPlan, runner: FixtureRunner) -> BaseException | None:
    """Unwind the scopes the incoming test does not share, **outside its capture window**.

    Placement is the whole point.  Class- and module-scoped teardown belongs to the tests
    that just *finished*, so running it inside the next test's ``redirect_stdout`` prefixes
    the previous module's teardown output onto an unrelated test's ``stdout`` — reviewer's
    probe: ``TEARDOWN-CLASS``/``TEARDOWN-MODULE`` appearing in ``test_two``'s capture.  That
    is not a cosmetic mix-up; it attributes one test's output to another on the wire.

    **Documented 1b.2 divergence.**  pytest prints this output under the *previous* test's
    teardown section, because ``runtestprotocol`` is handed ``nextitem`` and can unwind at
    the right moment.  The execute wire has no lookahead, so boundary-teardown output goes to
    the worker's stderr instead of onto any test's ``stdout``.  Attributing it to the test
    that owns it needs per-boundary capture and a place on the wire to put it — 1c, with the
    session-scope channel.

    :meth:`FixtureRunner.note_test_boundary` is called here as well as (idempotently) from
    ``setup``, so the class drain happens out here too rather than half in and half out.

    Returns the exception instead of raising it, so :func:`_run_phases` can report it as the
    incoming test's *setup* failure — the same attribution as before, just with the output
    no longer landing in that test's capture.
    """
    try:
        runner.note_module_boundary(plan.path)
        runner.note_test_boundary(plan.class_name)
    except ABORT_EXCEPTIONS:
        raise
    except BaseException as exc:  # noqa: BLE001 - returned, classified by the caller
        return exc
    return None


def _log_capture_for(plan: ExecutionPlan) -> _builtins.LogCapture | None:
    """The logging capture this test needs, or ``None``.

    pytest installs its capture handler for **every** item (`_pytest/logging.py::
    LoggingPlugin.pytest_runtest_setup`), because the same handler also feeds the
    ``add_report_section(when, "log", ...)`` block the terminal prints under a failure.  The
    v2 wire has no report sections, so the only reader is the ``caplog`` fixture and the
    handler is installed only when a test's **closure** contains it.

    The closure rather than the signature: an autouse fixture, a ``usefixtures`` mark or a
    fixture-of-a-fixture can pull ``caplog`` in without the test naming it, and all three are
    tests whose records pytest would capture.

    Installing it here — before the setup phase — rather than from the fixture body is the
    point of the whole function: a *fixture* that logs during setup is in
    ``caplog.get_records("setup")`` under pytest, and a handler added when the fixture body
    ran would have missed every record logged before it.
    """
    if "caplog" not in plan.closure.names:
        return None
    return _builtins.LogCapture()


def _run_phases(
    plan: ExecutionPlan,
    runner: FixtureRunner,
    capture: _Capture,
    boundary_exc: BaseException | None = None,
) -> list[PhaseReport]:
    """setup -> call -> teardown, with per-phase exception capture.

    Port of `_pytest/runner.py::runtestprotocol` (l. 122-147), including the two structural
    rules that are easy to lose:

    * **the call phase runs only if setup passed** (``if rep.passed:``), and ``passed`` there
      means *after* the xfail promotion, so an ``xfail``-marked test whose fixture blew up
      never executes its body;
    * **teardown runs regardless**, so a fixture that was half set up is still unwound and a
      teardown failure is still reported.

    Marks are evaluated inside the setup phase because that is where pytest evaluates them
    (`skipping.py::pytest_runtest_setup`, ``tryfirst``): a ``skip`` mark therefore short-
    circuits **before any fixture is built** — probed, a skipped test whose fixture raises
    reports SKIPPED — and a mark that cannot be evaluated at all is a setup *error*.

    *boundary_exc* is a scope teardown that failed in :func:`drain_boundaries`, before the
    capture window opened; it is reported as this test's setup failure because that is the
    only phase left to hang it on.

    After each phase the capture is checked for having been **closed by the test**
    (:attr:`_Capture.broken`).  A phase that otherwise passed becomes a failure, which is
    what reproduces pytest's probed ``FAILED`` (call) + ``ERROR`` (teardown) pair for
    ``sys.stdout.close()`` — see :attr:`_Capture.broken`.

    Each phase also runs inside its **own** logging window when the test asks for ``caplog``
    (:func:`_log_capture_for`), which is pytest's ``_runtest_for(item, when)``: the handler is
    reset per phase, so ``caplog.records`` inside the body holds the call phase's records only
    and a teardown fixture does not see the body's.
    """
    log_capture = _log_capture_for(plan)
    _builtins.set_log_capture(log_capture)

    @contextlib.contextmanager
    def phase(when: str) -> Iterator[None]:
        if log_capture is None:
            yield
            return
        with log_capture.phase(when):
            yield

    try:
        return _phases(plan, runner, capture, boundary_exc, phase)
    finally:
        _builtins.set_log_capture(None)


def _phases(
    plan: ExecutionPlan,
    runner: FixtureRunner,
    capture: _Capture,
    boundary_exc: BaseException | None,
    phase: Callable[[str], AbstractContextManager[None]],
) -> list[PhaseReport]:
    """:func:`_run_phases`' body, with the logging window as an argument.

    Split out so the ``caplog`` plumbing is set up and torn down in exactly one place: a
    ``return`` from inside the ``try`` above would otherwise have to remember to clear the
    module-level slot on every path.
    """
    namespace = _condition_namespace(plan)
    xfailed: Xfail | None = None
    setup_exc: BaseException | None = boundary_exc
    kwargs: Mapping[str, object] = {}
    if setup_exc is None:
        try:
            with phase("setup"):
                skipped = evaluate_skip_marks(plan.marks, namespace)
                if skipped is not None:
                    raise _Skipped(skipped.reason)
                xfailed = evaluate_xfail_marks(plan.marks, namespace)
                if xfailed is not None and not xfailed.run:
                    raise _XFailed("[NOTRUN] " + xfailed.reason)
                kwargs = runner.setup(plan)
        except ABORT_EXCEPTIONS:
            raise
        except BaseException as exc:  # noqa: BLE001 - classified below, never dropped
            setup_exc = exc

    reports = [_checked(report_for_phase("setup", setup_exc, xfailed), capture)]

    if reports[0].outcome == "passed":
        call_exc: BaseException | None = None
        try:
            with phase("call"):
                _run_call(plan, runner, kwargs)
        except ABORT_EXCEPTIONS:
            raise
        except BaseException as exc:  # noqa: BLE001 - classified below, never dropped
            call_exc = exc
        reports.append(_checked(report_for_phase("call", call_exc, xfailed), capture))

    teardown_exc: BaseException | None = None
    try:
        with phase("teardown"):
            runner.teardown("function")
    except ABORT_EXCEPTIONS:
        raise
    except BaseException as exc:  # noqa: BLE001 - classified below, never dropped
        teardown_exc = exc
    reports.append(_checked(report_for_phase("teardown", teardown_exc, xfailed), capture))
    return reports


def _checked(report: PhaseReport, capture: _Capture) -> PhaseReport:
    """Turn a phase that closed the capture into a failure, as pytest does.

    Only a report that is otherwise a *plain pass* is rewritten: a test that both failed and
    closed its stream keeps its real failure, which is the more useful message and is also
    what pytest reports (its own ``snap`` runs after the body's exception is recorded).
    """
    if not capture.broken or not report.plain_pass:
        return report
    return PhaseReport(report.phase, "failed", CAPTURE_CLOSED_MESSAGE)


def execute_test(test_id: str) -> ResultResponse:
    """Run one collected test and build its ``test_result`` response.

    The plan comes from this worker's own collection index, so the module is warm and the
    function object is the one enumeration saw; an id that is not in the index is
    :class:`UnknownTestError` — protocol drift, handled in :func:`main`, never answered with
    a fabricated result.

    ``duration_s`` is ``time.perf_counter`` around all three phases — a monotonic clock, so
    it cannot go backwards over an NTP step — and covers fixture setup and teardown as well
    as the body, which is what makes the orchestrator's sum comparable to wall time.  The
    boundary drain is deliberately **outside** both the capture window and the clock: it is
    the previous tests' teardown, and charging its cost and its output to this test would
    misattribute both (:func:`drain_boundaries`).

    Nothing in here may raise on behalf of a *test*: a result that has been computed must
    reach the wire, or the worker dies mid-stream and every queued test goes unanswered.
    That is why the capture read goes through :meth:`_Capture.drain`.
    """
    try:
        plan = execution_plan(test_id)
    except KeyError as exc:
        raise UnknownTestError(str(exc.args[0]) if exc.args else test_id) from None

    runner = _execution_runner()
    boundary_exc = drain_boundaries(plan, runner)

    capture = _worker_capture()
    started = time.perf_counter()
    with capture.window():
        reports = _run_phases(plan, runner, capture, boundary_exc)
    duration = time.perf_counter() - started

    report = reduce_reports(reports)
    result: ResultResponse = {
        "op": "test_result",
        "id": plan.id,
        "status": report.status,
        "duration_s": duration,
    }
    if report.message:
        result["message"] = report.message
    captured_out, captured_err = capture.drain()
    if captured_out:
        result["stdout"] = captured_out
    if captured_err:
        result["stderr"] = captured_err
    return result


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerState:
    """What ``init`` established, needed by every later ``collect_file``."""

    rootdir: Path
    naming: Naming
    #: The three ``asyncio_*`` ini values (protocol v4).  Defaulted so every test in this
    #: repo that builds a state by hand keeps working and states, by omission, that it is
    #: not exercising the mode.
    asyncio: AsyncioConfig = _DEFAULT_ASYNCIO
    #: ``Config.invocation_params.dir`` — the directory the run was started from, which is
    #: **not** rootdir whenever a run is started below its config file.  Read by
    #: ``request.config.invocation_params.dir``; defaulted to rootdir for the hand-built
    #: states in this repo's own tests, which never exercise the distinction.
    invocation_dir: Path | None = None


_state: WorkerState | None = None

#: Whether ``init`` carried a ``coverage`` object and :func:`rustest._v2_coverage.start`
#: succeeded.
#:
#: A plain `bool` rather than a reference to the monitor, so that :func:`main`'s ``finally``
#: can decide whether to write coverage data **without importing anything**: a run with no
#: ``--cov`` must not pay an import on its shutdown path, and `False` here is the whole check.
_coverage_started: bool = False

#: Execution plans for every test this worker has collected, keyed by manifest id.
#: Worker-local by design: the orchestrator routes an ``execute_test`` back to the worker
#: that collected the file (``src/v2/collect.rs`` stem-hash routing), so the plan — and the
#: warm module object it points at — is always here.  An id that is not is a routing bug,
#: and :func:`execution_plan` says so loudly rather than returning ``None``.
_execution_plans: dict[str, ExecutionPlan] = {}

#: The one :class:`FixtureRunner` for this worker's whole lifetime.  It has to outlive a
#: single ``execute_test``, or module- and session-scoped fixtures would be rebuilt per test
#: and the scope caching would mean nothing.
_runner: FixtureRunner | None = None


def _execution_runner() -> FixtureRunner:
    """This worker's runner, created on first use.

    The asyncio config comes from :data:`_state`, i.e. from ``init``.  It is read **here**
    rather than stored at ``init`` time because the runner outlives no state and the state
    outlives no runner: reading it at construction keeps one source of truth and makes a
    runner built before ``init`` (only this module's own tests do that) fall back to the
    documented defaults instead of to whatever a previous test left behind.
    """
    global _runner
    if _runner is None:
        _runner = FixtureRunner(_state.asyncio if _state is not None else _DEFAULT_ASYNCIO)
    return _runner


def execution_plan(test_id: str) -> ExecutionPlan:
    """The execution plan for *test_id*, or ``KeyError`` naming the routing failure."""
    try:
        return _execution_plans[test_id]
    except KeyError:
        raise KeyError(
            f"no collected test with id {test_id!r} in this worker "
            + "(the orchestrator must execute a test in the worker that collected it)"
        ) from None


def _asyncio_config_from_init(message: Mapping[str, object]) -> AsyncioConfig:
    """Read the three ``asyncio_*`` fields off an ``init`` line.

    ``asyncio_default_fixture_loop_scope`` is **absent from the wire when unset**
    (`src/v2/protocol.rs`, `skip_serializing_if`), so ``message.get`` returning ``None`` is
    the option's real third state and is stored as such -- see
    :meth:`FixtureRunner.fixture_loop_scope` for what it means.

    No value is re-validated here.  ``src/v2/config.rs`` already rejected a bad mode or scope
    with pytest-asyncio's own message and exit 4, before this process existed; a second
    implementation of the rules would be a second thing free to disagree with the first.
    """
    fixture_scope = message.get("asyncio_default_fixture_loop_scope")
    return AsyncioConfig(
        mode=str(message.get("asyncio_mode", DEFAULT_ASYNCIO_MODE)),
        default_fixture_loop_scope=None if fixture_scope is None else str(fixture_scope),
        default_test_loop_scope=str(
            message.get("asyncio_default_test_loop_scope", DEFAULT_ASYNCIO_TEST_LOOP_SCOPE)
        ),
    )


def _pattern_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in cast(Sequence[object], value))
    return default


def _apply_pythonpath(raw: object) -> list[str]:
    """Prepend the ``pythonpath`` ini to ``sys.path``, in pytest's order.

    Port of ``Config._configure_python_path`` (`_pytest/config/__init__.py` l. 1316-1319)::

        for path in reversed(self.getini("pythonpath")):
            sys.path.insert(0, str(path))

    Reversed-then-insert-at-0 is what makes ``pythonpath = a b`` produce
    ``sys.path == [a, b, ...]`` rather than ``[b, a, ...]``, so the *first* entry wins a
    name collision.  The entries arrive absolute — ``type="paths"`` resolved them against
    the config file's directory in ``config::resolve_config`` — so nothing is joined here.

    Unlike pytest there is no ``_unconfigure_python_path`` counterpart: a worker process
    exists for one run and then exits, so there is no later run to keep clean.  Returned for
    the tests rather than for any caller.

    This is a **regression closed**, not a new feature: v1 applied the same ini through
    ``src/python_support.rs::read_pythonpath_from_pyproject``, and it applied more besides —
    it also injected the project root and an auto-detected ``src/``.  Those two are *not*
    reproduced: `_pytest/config/__init__.py` does neither, and adding directories pytest
    would not add makes an import succeed under rustest that fails under pytest, which is a
    worse failure than the one being fixed.
    """
    if not raw:
        return []
    # `str(Path(...))`, not the wire string verbatim. The entries travel as **posix** like
    # every other path on the protocol, and inserting `C:/repo/src` on Windows works for the
    # import itself but leaves every module found through it with a forward-slash
    # `__file__` -- so `Path(mod.__file__).relative_to(rootdir)` and every string comparison
    # against a native path silently disagrees with the same run under pytest, which inserts
    # `str(Path)`. Native-separator normalisation is a no-op on posix.
    entries = [str(Path(str(entry))) for entry in cast("Sequence[object]", raw)]
    for entry in reversed(entries):
        sys.path.insert(0, entry)
    return entries


def handle_init(message: Mapping[str, object]) -> ReadyResponse:
    """Handle ``init``; reply ``ready``.

    The reply states :data:`PROTOCOL_VERSION` — the version this worker *speaks*.
    It is never an echo of ``message["protocol_version"]``: an echo would make the
    handshake agree with any orchestrator and detect no skew at all.  Deciding what
    to do about a mismatch is the orchestrator's job (``src/v2/protocol.rs``).

    ``invocation_dir`` **is** stored as of Phase 3 Task 2: ``request.config`` answers with it
    through ``Config.invocation_params.dir``, which is the value pytest itself resolves a
    test's relative paths against.  Nothing in the collection half uses it (paths arrive
    absolute, nodeids are rootdir-relative).

    ``pythonpath`` is applied **first**, before the rewrite hook and before any import, which
    is where pytest applies it: ``Config._configure_python_path`` runs from
    ``pytest_load_initial_conftests``, i.e. ahead of conftest collection.
    """
    global _state
    rootdir = Path(str(message["rootdir"]))
    raw_invocation_dir = message.get("invocation_dir")
    invocation_dir = rootdir if raw_invocation_dir is None else Path(str(raw_invocation_dir))

    pythonpath = _apply_pythonpath(message.get("pythonpath"))

    # The assertion-rewriting hook goes on `sys.meta_path` here rather than in `main`,
    # because its bytecode cache lives under the *rootdir* and `init` is the first moment
    # the worker knows one.  It is installed unconditionally and rewrites nothing until a
    # `collect_file` registers a path, so a run with no Tier S files pays one extra
    # `find_spec` delegation per import and nothing else.
    from . import _assertion_rewrite

    _ = _assertion_rewrite.install_hook(str(rootdir / ".rustest_cache" / "v2-assert"))

    # Coverage starts **here**, before the first `collect_file`, because a module's
    # import-time lines are lines coverage.py counts: `coverage run -m pytest` starts before
    # pytest imports anything.  The import is inside the branch so a run without `--cov` never
    # loads `_v2_coverage` — or, behind it, `coverage` — at all.
    coverage_wire = message.get("coverage")
    if coverage_wire is not None:
        global _coverage_started
        from . import _v2_coverage

        _ = _v2_coverage.start(cast("Mapping[str, object]", coverage_wire))
        _coverage_started = True

    naming = Naming(
        python_files=_pattern_tuple(message.get("python_files"), DEFAULT_PYTHON_FILES),
        python_classes=_pattern_tuple(message.get("python_classes"), DEFAULT_PYTHON_CLASSES),
        python_functions=_pattern_tuple(message.get("python_functions"), DEFAULT_PYTHON_FUNCTIONS),
    )
    asyncio_config = _asyncio_config_from_init(message)
    _state = WorkerState(
        rootdir=rootdir,
        naming=naming,
        asyncio=asyncio_config,
        invocation_dir=invocation_dir,
    )
    _builtins.configure(rootdir, invocation_dir, _ini_values(naming, asyncio_config, pythonpath))
    return {"op": "ready", "protocol_version": PROTOCOL_VERSION}


def _ini_values(
    naming: Naming, asyncio_config: AsyncioConfig, pythonpath: Sequence[str] = ()
) -> dict[str, object]:
    """The ini values ``request.config.getini`` can answer — exactly what ``init`` carries.

    Seven names, and the list is deliberately not padded.  ``markers``, ``xfail_strict``,
    ``filterwarnings`` and the rest are real pytest inis whose values this worker does not
    have; :class:`rustest._v2_builtins.Config` refuses them by name rather than returning a
    plausible default, because a suite branching on a fabricated ``getini`` result reports a
    green run about a mode it never ran in.

    ``asyncio_default_fixture_loop_scope`` is included **even when it is ``None``**, since
    ``None`` is the option's real third state (see :meth:`FixtureRunner.fixture_loop_scope`)
    and pytest-asyncio's own ``getini`` answers ``""`` for it — a caller has to be able to
    tell "unset" from "not carried".
    """
    return {
        "python_files": list(naming.python_files),
        "python_classes": list(naming.python_classes),
        "python_functions": list(naming.python_functions),
        "asyncio_mode": asyncio_config.mode,
        "asyncio_default_fixture_loop_scope": asyncio_config.default_fixture_loop_scope,
        "asyncio_default_test_loop_scope": asyncio_config.default_test_loop_scope,
        # `type="paths"`, so pytest's own `getini` answers a list of `Path`s, not strings
        # (`Config._getini` l. 1659-1666 returns `[dp / x for x in ...]`). A suite that does
        # `str(p)` on the result is fine either way; one that does `p.name` is not, so the
        # type is reproduced rather than approximated.
        "pythonpath": [Path(entry) for entry in pythonpath],
    }


def drain_at_shutdown() -> BaseException | None:
    """Unwind every scope still open, returning the failure rather than raising it.

    Paired with :func:`handle_shutdown`, and the pairing is the contract: this unwinds the
    fixtures, that answers the protocol, and :func:`main` sequences them so ``bye`` is only
    written once the drain has actually finished.  A caller that answers ``shutdown`` without
    calling this leaks every module- and session-scoped fixture — a container never stopped, a
    directory never removed.

    The failure is **returned, not swallowed**: :func:`main` turns it into
    :data:`SHUTDOWN_TEARDOWN_EXIT`.  See that constant for why it cannot be exit 0.
    """
    global _runner
    runner, _runner = _runner, None
    if runner is None:
        return None
    try:
        runner.teardown_all()
    except ABORT_EXCEPTIONS:
        raise
    except BaseException as exc:  # noqa: BLE001 - returned to main, never dropped
        print(
            "rustest v2 worker: errors while tearing down fixtures at shutdown:\n"
            + _format_exception(exc),
            file=sys.stderr,
        )
        return exc
    return None


def handle_shutdown() -> ByeResponse:
    """Handle ``shutdown``; reply ``bye`` — the last line the worker writes.

    Answering the protocol only.  :func:`drain_at_shutdown` does the unwinding, and
    :func:`main` calls it first.
    """
    return {"op": "bye"}


def collect_file(path: str, assert_key: str | None = None) -> CollectedResponse:
    """Collect one file and build its ``collected`` response.

    ``path`` is echoed verbatim (absolute posix, as sent); the nested entries carry
    rootdir-relative paths per the manifest contract.  Exactly one of ``tests`` /
    ``error`` is present — and neither appears as an empty value: a file that
    legitimately collects nothing is ``{"op":"collected","path":...}`` alone.

    ``assert_key`` is the Tier S manifest cache key when the orchestrator certified this
    file as statically analysable, and ``None`` otherwise.  When it is present the file is
    **registered for assertion rewriting** before the import below, so the module the worker
    ends up holding is the rewritten one — registration has to happen first, because the
    rewrite hook makes its decision inside ``importlib.import_module`` and there is no second
    chance once the module is in ``sys.modules``.  When it is absent nothing is registered
    and the ordinary machinery imports the file unchanged, which is the plan's "Tier D files
    keep plain asserts".

    Any import-time exception becomes an error entry rather than killing the worker,
    since one unimportable file must not lose the whole run.  ``BaseException`` is
    deliberately *not* caught: a ``SystemExit`` or ``KeyboardInterrupt`` raised at
    import time should end the process, exactly as it would under pytest.
    """
    if _state is None:
        raise NotInitializedError("collect_file received before init")
    state = _state

    if assert_key is not None:
        from . import _assertion_rewrite

        _assertion_rewrite.register(path, assert_key)

    file_path = Path(path)
    response: CollectedResponse = {"op": "collected", "path": path}
    try:
        if file_path.suffix == ".md":
            # The markdown tier: no module to import, but the conftest chain is still loaded
            # so an autouse fixture reaches a doc example exactly as v1's
            # `merge_conftest_fixtures` made it.  The orchestrator only ever sends a `.md`
            # path when code blocks are enabled (`src/v2/collect.rs::is_markdown`).
            tests, plans = collect_markdown(
                file_path, state.rootdir, _markdown_registry(file_path, state.rootdir)
            )
        else:
            module, registry = build_registry(file_path, state.rootdir)
            tests, plans = collect_module(
                module, file_path, state.rootdir, state.naming, registry, state.asyncio
            )
        for plan in plans:
            _execution_plans[plan.id] = plan
    except CollectionRefusal as exc:
        response["error"] = {
            "path": _relative_posix(file_path, state.rootdir),
            "message": str(exc),
        }
        return response
    except ABORT_EXCEPTIONS:
        # A `pytest.exit()` at import time ends the **session**, not this file's collection.
        # `Exit` is an ordinary `Exception` (pytest's own base class), so without this arm the
        # handler below would file it as "this module failed to import" and the run would
        # calmly continue — the silent no-op this fix exists to remove, moved one phase
        # earlier. Ctrl-C reaches the same arm for the same reason.
        raise
    except MODULE_SKIP_EXCEPTIONS as exc:
        # Port of `_pytest/python.py::importtestmodule` l. 534-542, the `except
        # skip.Exception` arm.  The flag decides between two entirely different answers, and
        # the message for the unset case is pytest's, word for word — it is the only thing
        # that tells an author *why* their module-scope `pytest.skip()` was refused.
        if _safe_getattr(exc, "allow_module_level", False):
            reason = _safe_getattr(exc, "msg", None)
            response["skipped"] = reason if isinstance(reason, str) else str(exc)
            return response
        response["error"] = {
            "path": _relative_posix(file_path, state.rootdir),
            "message": (
                "Using pytest.skip outside of a test will skip the entire module. "
                "If that's your intention, pass `allow_module_level=True`. "
                "If you want to skip a specific test or an entire class, "
                "use the @pytest.mark.skip or @pytest.mark.skipif decorators."
            ),
        }
        return response
    except Exception as exc:
        response["error"] = {
            "path": _relative_posix(file_path, state.rootdir),
            "message": "".join(traceback.format_exception(exc)).rstrip(),
        }
        return response

    if tests:
        response["tests"] = tests
    return response


def execute_batch(
    ids: Sequence[str],
    stop_on_failure: bool,
    emit: Callable[[Mapping[str, object]], None],
    max_fail: int | None = None,
) -> BatchDoneResponse:
    """Run a whole file's tests, emitting one ``test_result`` each, then ``batch_done``.

    The results are *streamed* — :func:`main`'s ``emit`` is called per test rather than a
    list being returned — so the orchestrator can start reading before the batch finishes,
    and so a batch of a thousand parametrized cases does not build a thousand-line string in
    memory before anything is written. The saving that motivates the op comes from the
    **request** side (one write and one blocking read per file instead of per test) and from
    flushing once at the end, not from withholding the results.

    ``stop_on_failure`` is ``-x`` reaching inside the batch. Checked *after* each result is
    emitted, because pytest reports the failing test and then stops: the failure is data the
    user needs, and only what comes after it is cancelled. See
    ``src/v2/protocol.rs::WorkerRequest::ExecuteBatch``.

    ``max_fail`` is ``--maxfail``'s **remaining budget** for this batch (protocol v7), which
    is why it is a count and not the configured ``N``: the orchestrator has already
    subtracted what the rest of the pool failed. Same "emit, then decide" rule as
    ``stop_on_failure``, so the Nth failure is still reported.

    :class:`UnknownTestError` propagates rather than being answered, exactly as it does for a
    single ``execute_test``: an id this worker never collected is a routing bug, and the
    results already emitted stay on the wire for the orchestrator to keep.
    """
    executed = 0
    stopped = False
    failures = 0
    for test_id in ids:
        result = execute_test(test_id)
        emit(result)
        executed += 1
        if result["status"] in ("failed", "error"):
            failures += 1
            if stop_on_failure or (max_fail is not None and failures >= max_fail):
                stopped = True
                break
    return {"op": "batch_done", "executed": executed, "stopped": stopped}


def encode_response(response: Mapping[str, object]) -> str:
    """Encode one response as a protocol line (no trailing newline).

    ``separators`` is compact because serde_json is compact and the golden strings in
    ``src/v2/protocol.rs`` are byte contracts.  ``ensure_ascii`` stays on so the line
    is pure ASCII whatever the console encoding happens to be on Windows; serde
    decodes ``\\uXXXX`` escapes back to the same string.
    """
    return json.dumps(response, separators=(",", ":"))


def install_pytest_shim() -> None:
    """Point ``import pytest`` at rustest's compat layer, process-wide.

    Mirrors ``src/discovery.rs::inject_pytest_compat_shim``.  Two reasons this is not
    optional: the worker must not pull real pytest into the process, and the
    enumeration reads the ``__rustest_*`` metadata that only rustest's decorators
    attach — real pytest's ``@pytest.mark.parametrize`` would leave none.

    The ``_pytest.*`` stubs go in too, mirroring what v1's CLI does alongside the
    shim (``python/rustest/core.py`` l. 97-102 -> ``compat/pytest.py::install_pytest_stubs``).
    Test modules and conftests that import pytest's *internal* API — ``from
    _pytest.outcomes import Failed``, ``from _pytest.monkeypatch import MonkeyPatch``
    — are common enough in real suites, and without the stubs those files would fail
    to import and be reported as collection errors instead of collecting.
    ``install_pytest_stubs`` no-ops when real pytest is already loaded, so this is
    safe even if something upstream imported it.

    Process-global by nature, so it is called from :func:`main` only, never from the
    collection helpers (a unit test importing this module must not have its own
    ``pytest`` swapped out from under it).
    """
    from rustest.compat import pytest as compat_pytest
    from rustest.compat import pytest_asyncio as compat_pytest_asyncio

    sys.modules["pytest"] = compat_pytest
    sys.modules["pytest_asyncio"] = compat_pytest_asyncio
    compat_pytest.install_pytest_stubs()


def _reconfigure(stream: object) -> None:
    """Force UTF-8 and ``\\n`` line endings on a protocol stream.

    Windows would otherwise encode with the console codepage and translate ``\\n`` to
    ``\\r\\n``, corrupting the framing for any orchestrator that does not strip it.
    """
    if isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(encoding="utf-8", newline="\n")


def _detach_protocol_stream(stream: TextIO) -> TextIO:
    """Move the protocol channel off **fd 1**, and point fd 1 at the worker's stderr.

    This is the structural half of closing the raw-fd-write hazard, and it has to happen
    before anything else can write.  Until Phase 3 Task 2 the protocol was ``sys.stdout``
    itself: rebinding the *name* stopped a stray ``print``, and stopped nothing else.  A test
    doing ``os.write(1, b"...")`` — or a C extension, or a subprocess inheriting the fd —
    wrote straight into the JSON-lines stream the orchestrator was parsing.

    Both halves are needed:

    * ``os.dup`` gives the protocol a **private** descriptor, so :class:`_Capture`'s
      ``dup2`` onto fd 1 cannot redirect the protocol into a test's capture file;
    * ``os.dup2(stderr, 1)`` makes fd 1 harmless *outside* a capture window too — during
      collection, between tests, and under ``-s``.  Without it the window would be the only
      protection and an import-time ``os.write(1, ...)`` would still corrupt the stream.

    Returns the original *stream* unchanged when it has no file descriptor at all — a
    ``StringIO`` under a unit test, a pipe replacement under an embedded run.  Detaching is an
    optimisation for those callers and a correctness property only for a real worker, so a
    failure to do it is not worth refusing to start over.
    """
    try:
        fd = stream.fileno()
        err_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):  # pragma: no cover - not a real worker
        return stream
    stream.flush()
    private = os.dup(fd)
    _ = os.dup2(err_fd, fd)
    detached = cast(TextIO, os.fdopen(private, "w", encoding="utf-8", newline="\n"))
    return detached


def main() -> int:
    """Run the protocol loop: read a request per line, write a response per line.

    stdout is reserved for protocol traffic, and as of Phase 3 Task 2 it is reserved at the
    **descriptor** level: :func:`_detach_protocol_stream` dups the real stdout onto a private
    fd and repoints fd 1 at stderr, so neither a stray ``print`` nor a raw ``os.write(1, …)``
    can reach the JSON-lines stream.  ``sys.stdout`` is rebound to stderr on top of that,
    before any test module is imported, so import-time output is forwarded rather than lost.

    **Exit codes**, and they are three distinct diagnoses:

    * **0** — the run completed and every scope unwound cleanly.
    * **2** — *protocol drift*: an unparseable line, an unknown ``op``, a ``collect_file``
      with no ``path``, a ``collect_file`` before ``init``, an ``execute_test`` with no
      ``id``, or an ``execute_test`` for an id this worker never collected.  The protocol is
      internal, so drift means a bug and must be loud (``src/v2/protocol.rs`` module docs) —
      and it must be *distinguishable*, which an uncaught traceback (exit 1, no framing) is
      not.  A file that merely fails to import is NOT drift: it is data, and it comes back as
      a ``collected`` error entry.
    * **3** (:data:`SHUTDOWN_TEARDOWN_EXIT`) — every response was written, ``bye`` included,
      but a fixture teardown failed in :func:`drain_at_shutdown`.  Not drift, and not green:
      see the constant.
    * **4** (:data:`SESSION_EXIT_EXIT`) — a test called ``pytest.exit()``.  The session is
      over *by request*; the orchestrator keeps every result already reported and exits 2,
      which is pytest's answer (probed: ``1 passed``, exit 2, the exiting test unreported and
      the tests after it never run).

    A ``KeyboardInterrupt`` from a test body still propagates out of this loop uncaught and
    ends the process, which is pytest aborting the session.  In every abort case the test
    that raised gets no ``test_result``, on purpose: the run did not finish, and reporting it
    as a failure would imply the rest ran.
    """
    install_pytest_shim()

    _reconfigure(sys.stdout)
    _reconfigure(sys.stdin)
    protocol_out = _detach_protocol_stream(sys.stdout)
    sys.stdout = sys.stderr

    def emit(response: Mapping[str, object]) -> None:
        """Write one response line and flush, so the orchestrator sees it immediately."""
        _ = protocol_out.write(encode_response(response) + "\n")
        protocol_out.flush()

    def emit_buffered(response: Mapping[str, object]) -> None:
        """Write one response line **without** flushing.

        Used inside a batch only, and it is where the batch op's saving actually lands: N
        flushes become one, so a 10-test file costs one pipe write-through instead of ten.
        The line still reaches the pipe when the wrapper's buffer fills, and
        :func:`_protocol_loop` flushes unconditionally after ``batch_done`` — so a batch
        larger than the buffer streams as it goes and a small one arrives in a single write.

        There is no deadlock in the "worker fills the pipe" case: the orchestrator is
        already blocked reading this batch's results and drains as they arrive.
        """
        _ = protocol_out.write(encode_response(response) + "\n")

    try:
        try:
            return _protocol_loop(emit, emit_buffered, protocol_out.flush)
        except _Exit as exc:
            # `pytest.exit()`.  The banner is pytest's own wording
            # (`_pytest/main.py::wrap_session`:
            # `sys.stderr.write(f"{type(exc).__name__}: {exc}")`), and the orchestrator forwards
            # this worker's stderr, so the user sees the reason they wrote.  No `test_result` is
            # emitted for the test that called it — the run did not finish, and a fabricated
            # outcome is exactly the false green this replaces.
            print(f"Exit: {exc}", file=sys.stderr)
            return SESSION_EXIT_EXIT
    finally:
        # Coverage is stopped and written on **every** exit path, for the same reason the
        # flush below covers every exit path: `pytest.exit()` and a propagating
        # `KeyboardInterrupt` both leave through here, and a worker that measured a thousand
        # tests and wrote nothing is indistinguishable from one that never ran.  Guarded on a
        # plain flag so a run without `--cov` imports nothing here.
        #
        # It runs **before** the flush, and after `drain_at_shutdown` on the ordinary path, so
        # module- and session-scoped fixture teardowns are measured — as they are under pytest.
        if _coverage_started:
            from . import _v2_coverage

            try:
                _ = _v2_coverage.stop_and_write()
            except Exception as exc:  # noqa: BLE001 - a broken write must not hide the results
                print(f"rustest v2 worker: could not write coverage data: {exc}", file=sys.stderr)
        # **Every** exit path flushes, including the two that leave through an exception:
        # `pytest.exit()` above and a `KeyboardInterrupt` that propagates out of `main`.  A
        # batch writes its results with `emit_buffered`, so an abort mid-batch can be holding
        # results the orchestrator has *earned* — tests that ran and reported — in an 8 KB
        # buffer.  Until Phase 3 Task 2 this was covered by accident: `protocol_out` was
        # `sys.stdout`, which CPython flushes during interpreter shutdown.  Moving the protocol
        # to a private descriptor (:func:`_detach_protocol_stream`) took that accident away and
        # the loss was immediate and silent — probed: a file whose first test fails and whose
        # second calls `pytest.exit()` reported `no tests ran` instead of `1 failed`.
        with contextlib.suppress(ValueError, OSError):
            protocol_out.flush()


def _protocol_loop(
    emit: Callable[[Mapping[str, object]], None],
    emit_buffered: Callable[[Mapping[str, object]], None],
    flush: Callable[[], object],
) -> int:
    """The request/response loop.  Split out of :func:`main` so the session-exit handler
    there wraps every op — collection included — rather than only the execute arm.

    The three writers are separate arguments rather than one object because they are used at
    different granularities and confusing them is silent: ``emit`` flushes (every op outside
    a batch), ``emit_buffered`` does not (results inside a batch), and ``flush`` closes a
    batch.  A batch that used ``emit`` would work and buy nothing; one that never called
    ``flush`` would hang."""
    while True:
        line = sys.stdin.readline()
        if not line:
            return 0
        if not line.strip():
            continue

        try:
            request = cast(Mapping[str, object], json.loads(line))
        except ValueError as exc:
            print(f"rustest v2 worker: undecodable request line: {exc}: {line!r}", file=sys.stderr)
            return 2

        op = request.get("op")
        if op == "init":
            emit(handle_init(request))
        elif op == "collect_file":
            path = request.get("path")
            if not isinstance(path, str):
                print(
                    f"rustest v2 worker: collect_file without a path: {line!r}",
                    file=sys.stderr,
                )
                return 2
            assert_key = request.get("assert_key")
            if assert_key is not None and not isinstance(assert_key, str):
                print(
                    f"rustest v2 worker: collect_file with a non-string assert_key: {line!r}",
                    file=sys.stderr,
                )
                return 2
            try:
                response = collect_file(path, assert_key)
            except NotInitializedError as exc:
                print(f"rustest v2 worker: {exc}", file=sys.stderr)
                return 2
            emit(response)
        elif op == "execute_test":
            test_id = request.get("id")
            if not isinstance(test_id, str):
                print(
                    f"rustest v2 worker: execute_test without an id: {line!r}",
                    file=sys.stderr,
                )
                return 2
            try:
                emit(execute_test(test_id))
            except UnknownTestError as exc:
                print(f"rustest v2 worker: {exc}", file=sys.stderr)
                return 2
        elif op == "execute_batch":
            ids = request.get("ids")
            stop_on_failure = request.get("stop_on_failure")
            if not isinstance(ids, list) or not all(
                isinstance(item, str) for item in cast("list[object]", ids)
            ):
                print(
                    f"rustest v2 worker: execute_batch without a list of ids: {line!r}",
                    file=sys.stderr,
                )
                return 2
            if not isinstance(stop_on_failure, bool):
                # Deliberately not defaulted to False: a missing flag would silently turn
                # `-x` off inside every batch, and "kept going after a failure" is exactly
                # the behaviour `-x` exists to prevent.
                print(
                    f"rustest v2 worker: execute_batch without stop_on_failure: {line!r}",
                    file=sys.stderr,
                )
                return 2
            try:
                raw_max_fail = request.get("max_fail")
                done = execute_batch(
                    cast("list[str]", ids),
                    stop_on_failure,
                    emit_buffered,
                    int(cast(int, raw_max_fail)) if raw_max_fail is not None else None,
                )
            except UnknownTestError as exc:
                # Whatever the batch already wrote is flushed before the worker dies, so the
                # orchestrator keeps the results it earned and reports the drift against the
                # right test instead of losing the whole file.
                _ = flush()
                print(f"rustest v2 worker: {exc}", file=sys.stderr)
                return 2
            emit_buffered(done)
            _ = flush()
        elif op == "shutdown":
            # Drain first, answer second: `bye` must not claim the worker is finished while
            # a session fixture is still open.  The exit code carries the drain's verdict.
            shutdown_failure = drain_at_shutdown()
            emit(handle_shutdown())
            return SHUTDOWN_TEARDOWN_EXIT if shutdown_failure is not None else 0
        else:
            print(f"rustest v2 worker: unknown op {op!r} in line: {line!r}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main())
