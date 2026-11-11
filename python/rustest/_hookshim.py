"""Run a conftest's own hooks against a strict shim of pytest's objects, or say why not.

The rule that makes this safe is the one every shim object here follows: **anything outside the
supported surface records what was touched, then raises** :class:`HookUnsupported`.  It is a
``BaseException`` so that a hook's own ``except Exception`` (pillow's conftest has one around
``config.addinivalue_line``) cannot swallow it, and the violation is recorded *before* it is
raised, so even a bare ``except:`` leaves the evidence behind.  A recorded violation is a
hand-off reason: the session goes to pytest.  A gap in this module therefore costs speed, never
a wrong answer.

Standalone on purpose: no ``pytest`` import, nothing from the worker at import time.  The worker
builds an :class:`HookRunner`, registers each conftest or plugin module it imported, and calls the
hooks at the points listed below.  What is supported, and where each piece comes from, is in the
table in ``python/tests/test_hookshim.py``.

What runs, and what does not
============================

Natively (:data:`NATIVE_HOOKS`): ``pytest_addoption``, ``pytest_configure``,
``pytest_sessionstart`` and ``pytest_collection_modifyitems`` -- the hooks whose every effect
lands **before any test has executed**.  That is the whole reason for the set: a violation is
answered by handing the session to pytest, which is only free while nothing has run yet.

Every other ``pytest_*`` name (``pytest_runtest_setup``, ``pytest_sessionfinish``,
``pytest_generate_tests``, ...) is ignored here: the worker names it as a hook rustest does not
run, which is itself a hand-off reason, and the hooks above still run beside it (so
``--no-pytest-fallback`` keeps them).  Refused, as violations: a native hook as a hook wrapper,
generator or coroutine, through ``specname=``, or asking for an argument outside its
specification (pluggy raises ``PluginValidationError`` for that).  Hooks are called exactly as
pluggy calls them: only the parameters *without* defaults, positionally.

What the shim objects are
=========================

``config``: ``getoption`` / ``option`` for the options conftests declared in ``pytest_addoption``
(with their declared defaults) or the worker supplied, ``getini`` for ``addini`` declarations
and ``markers``, ``addinivalue_line("markers", ...)``, ``rootpath``, ``invocation_params.dir``,
``hook.pytest_deselected(items=...)``, and plain user attributes (networkx's ``config.backend``).
Every public name of pytest's ``Config`` is reserved: assigning to one violates.  ``workerinput``,
``workeroutput`` and ``slaveinput`` are plain ``AttributeError`` (the "am I an xdist worker"
idiom).  ``parser``: ``addoption``, ``getgroup().addoption``, ``addini``, with pytest's
validation (malformed and lowercase short options, duplicate names or ``dest``, ``required=``,
unknown ``add_argument`` keywords and ``type`` given as a string all violate).  ``session``:
``config``.  ``item``: ``nodeid``, ``name``, ``path``, ``config``, ``keywords`` (membership
only), ``get_closest_marker``, ``iter_markers``, ``add_marker``; a real instance of the classes
``pytest.Function`` / ``pytest.Item`` / ``pytest.Node`` resolve to under rustest, named
``Function`` with pytest's ``repr``.  Everything else on any of them violates.

**Lifetime.**  A shim is valid only for the phase that made it (a registration, ``configure``,
``sessionstart`` or one ``modifyitems`` stage; ``config`` and ``session`` are the same objects in
every hook).  Using one from an earlier phase inside a later hook is a violation.  A hook that
*keeps* a shim -- ``CFG = config`` in ``pytest_configure``, a closure, an attribute, an item or
``item.keywords`` in a list, a parser group -- is a **hand-off before any test runs**: after every
hook returns the runner compares the reference count of everything it handed out with what it was
before the call (collecting garbage first, so a dead reference cycle is not a retention), and
checks that nothing created during the call is still alive.  That is CPython's reference counting
(verified on 3.13 and 3.14, with and without the GIL; 3.12 is the same mechanism but was not
available here), so it is exact for frames, closures, default arguments, attributes, stored
exceptions and unexhausted generators.  It cannot see a reference kept without holding the object
(there is none a later *use* could rely on) and would be wrong on an interpreter without reference
counts (PyPy), where ``_kept`` must be replaced by hand-off-on-any-hook.

The backstop, for anything that slips through: a shim used after its phase raises
:class:`HookUnsupported` inside the user and is listed in :attr:`HookRunner.late_reasons`.  A user
can swallow that exception (``except BaseException``, a bare ``except:``, a ``__del__``), so the
worker **must compare** ``len(runner.late_reasons)`` before and after each test, each teardown and
each finalizer, and error the one during which it grew -- even though the test saw no exception;
growth outside any test is an internal error.

What *survives* a hook is data, exported by the runner and applied by the worker to **its own**
objects: :attr:`~HookRunner.user_attributes` (``config.backend = ...``; copy each onto the
``Config`` the fixtures' ``request.config`` / ``pytestconfig`` returns, after every call) and
:attr:`~HookRunner.markers`.  ``request.config`` is the worker's own config object, never a shim,
and for a *declared* option it must answer exactly as pytest and the shim do: with the declared
value, **ignoring a call-site ``default=``** (:meth:`HookRunner.lookup_option`;
:meth:`HookRunner.lookup_ini` converts ini values as pytest does).  Marks added to a record's
items are returned as :class:`MarkAdded` effects for the worker to apply to its plan.

What the caller must guarantee
==============================

1. **The record** (:class:`ItemRecord`).  Built from the worker's plan exactly as pytest builds the
   item; ``python/tests/test_hookshim.py::record_from_pytest_item`` is the executable statement
   of that mapping, compared against real pytest.
2. **Environment.**  Before importing any conftest: ``os.environ["PYTEST_VERSION"]`` set (pytest
   sets it for the whole run) and ``sys.argv`` as pytest's would be (``--runslow`` in
   ``sys.argv`` is a real pattern).  Neither is something a shim can supply.
3. **Options.**  ``builtin_options`` holds the *effective* values after ``addopts`` and
   ``PYTEST_ADDOPTS``, keyed by flag and by ``dest``.  ``plugins`` names the loaded ``pytest11``
   plugins, because a conftest declaring an option string pytest or a plugin already registers
   (``--lf``, ``--cov``, ``-n``, ``--asyncio-mode``, ...) must be refused here as argparse refuses
   it: pytest's own set is frozen in :data:`_PYTEST_CORE_FLAGS`, each allowed plugin's in
   :data:`_PLUGIN_FLAGS`.  ``ini`` already includes
   ``-o`` overrides.
4. **Registration order.**  Conftests in pytest's order: the initial ones (the argument paths'
   conftests and ``anchor/test*/conftest.py``, which decides LIFO order) with ``initial=True``,
   ``pytest_plugins`` modules after their conftest, later conftests (the collection walk) with
   ``initial=False``.  Call :meth:`HookRunner.configure` once the initial ones are registered.
5. **The hook set.**  The Rust side's "this session needs the hook runtime" trigger must cover
   ``NATIVE_HOOKS`` -- and **every other ``pytest_*`` name must stay a direct hand-off trigger**
   (``pytest_generate_tests``, ``pytest_sessionfinish``, ...).  The runner ignores names it does
   not know, so a name no side acts on would be silently ignored by both.
6. **After every call** check :attr:`HookRunner.reasons`; a non-empty list means hand the session
   to pytest, and the effects returned alongside it are empty.
7. **Once per what.**  ``configure`` and ``sessionstart`` run in *every worker process*, before
   that worker's first test, because what they leave behind lives in that process (attrs'
   hypothesis profile, a user attribute, an option default).  A hook with an *external* effect
   (creates a database, writes a file, ``atexit.register``) therefore does it once per worker
   where plain ``pytest`` does it once; pytest-xdist does exactly that too, and the oracle here is
   a serial run.  ``modify_collection`` produces data (an order, deselections, marks): run it
   once per session wherever the collection list is formed; a hook that also sets process state
   in ``modifyitems`` must run in each worker, over the same list, and must be deterministic.

The order contract
==================

pluggy orders every hook as: ``tryfirst`` implementations, then plain ones, then ``trylast``
ones, each group last-registered first.  pytest's own selection (``-k``, ``-m``,
``--deselect``) is a *plain* ``pytest_collection_modifyitems`` implementation registered before
any conftest.  ``--lf`` acts in **two** places:

* a **collection filter** (``LFPluginCollWrapper``), before any hook, that only switches on when a
  last-failed file has a failing *module-level* test: it then skips files without a known failure
  and keeps a last-failed file's failures *and every collector* (class-level tests survive it);
* a **deselection** (``LFPlugin``, a ``tryfirst`` wrapper) that runs after every implementation,
  ``trylast`` included, and drops everything that did not fail.  With a class-only failure the
  filter never switches on, every hook sees every test, and pytest ends with ``N deselected``.

``--ff`` / ``--nf`` are wrappers too, reordering after every implementation.  So the order is,
each step proved against real pytest by ``test_modifyitems_runs_around_selection_as_pytest_does``
and ``test_lf_acts_twice_...``:

1. ``pytest_addoption`` at registration, ``pytest_configure``, ``pytest_sessionstart``.
2. The ``--lf`` collection filter (when switched on; it keeps collectors).
3. :meth:`HookRunner.modify_collection_before_selection`: the ``tryfirst`` and plain hooks, over
   what step 2 left.  A mark they add is visible to ``-m`` **and to ``-k``** (the keyword matcher
   reads marker names).
4. ``-k`` / ``-m`` / ``--deselect`` (rustest's selection engine).
5. :meth:`HookRunner.modify_collection_after_selection`: the ``trylast`` hooks, over what step 4
   left (member-designer's ``shards.py`` is one: it shards the *filtered* files).  Marks added in
   step 3 are applied to these items by the runner; marks added here reach neither ``-m`` nor
   ``-k``.
6. The ``--lf`` deselection of every test that did not fail last time.
7. ``--ff`` / ``--nf`` reordering.

One runner per session, each stage once per session: the marks stage one added are remembered for
stage two, and a second stage one starts from nothing.

Where this deliberately differs from pytest, always towards handing off: an option or ini key
nobody declared is *not* answered with its ``default=`` (pytest answers that only for options
it does not have, and this module cannot know which those are); ``config.option`` raises
instead of ``AttributeError``; an option declared by a conftest the walk reached after
argparse ran, with no explicit default, has no value in pytest and so none here.  Residual, not
fixable here: ``_pytest.*`` stubs accept writes silently, so a monkeypatch of pytest's internals
has no effect.
"""

# The shim classes set their slots with `object.__setattr__` because their own `__setattr__`
# is what refuses a hook's writes, so the checker cannot see the initialisation.
# pyright: reportUninitializedInstanceVariable=false

from __future__ import annotations

import functools
import gc
import inspect
import shlex
import sys
import warnings
import weakref
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, NoReturn, cast

__all__ = [
    "NATIVE_HOOKS",
    "CollectionEffects",
    "HookRunner",
    "HookUnsupported",
    "ItemRecord",
    "Mark",
    "MarkAdded",
]


class HookUnsupported(BaseException):
    """A hook touched something the shim does not provide.

    ``BaseException`` by design (see the module docstring).  ``reason`` is the hand-off text.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason: str = reason


@dataclass(frozen=True)
class Mark:
    """pytest's ``Mark``: what ``get_closest_marker`` returns and ``add_marker`` takes."""

    name: str
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class ItemRecord:
    """What the worker knows about one collected test: pytest's item, as data.

    ``markers`` is ``item.iter_markers()`` in pytest's order, closest first: the function's own
    decorators bottom-up *including* the ``parametrize`` mark itself, then the callspec's
    ``pytest.param(..., marks=)``, then each enclosing class's marks (with its MRO), then the
    module's ``pytestmark``.  ``own_marker_count`` is how many of the leading entries are the
    item's own marks (``len(item.own_markers)``); without it ``add_marker`` cannot place a new
    mark and hands off unless there is nothing to place it among.

    The rest builds ``item.keywords`` exactly as pytest's ``NodeKeywords`` chain does:
    ``name`` (``test_p[a-1]``), ``param_id`` (``a-1``, the callspec id), ``function_attrs`` (the
    keys of the test function's ``__dict__`` -- including ``pytestmark`` when it has marks),
    ``class_names`` (outermost first), ``module_name`` (``test_k.py``), ``dir_names`` (the
    directories between the rootdir and the module, outermost first), ``rootdir_name`` (the
    rootdir's basename), and the names of every mark in ``markers``.  ``kind`` is ``"function"``
    or ``"unittest"``; any other kind of item hands off.  ``path`` is the absolute path of the
    test file.
    """

    nodeid: str
    name: str
    path: str
    markers: tuple[Mark, ...] = ()
    own_marker_count: int | None = None
    rootdir_name: str = ""
    dir_names: tuple[str, ...] = ()
    module_name: str = ""
    class_names: tuple[str, ...] = ()
    function_attrs: frozenset[str] = frozenset()
    param_id: str | None = None
    kind: str = "function"
    #: The test is parametrized.  pytest's ``iter_markers`` then holds a ``parametrize`` mark at
    #: the position of each decorator, which the worker cannot rebuild: reading every marker, or
    #: the ``parametrize`` one, from such an item is a violation.
    parametrized: bool = False
    #: The test draws a parameter of a fixture wider than function scope, so pytest's
    #: ``FixtureManager`` (a plain hook registered at ``pytest_sessionstart``) may reorder it
    #: before later-run hooks see the list.  ponytail: shown to such a hook it is a violation;
    #: porting ``_pytest/fixtures.py::reorder_items`` between those impls is the upgrade.
    reordered: bool = False

    def to_wire(self) -> dict[str, Any]:
        """The JSON-able form the worker sends and receives (see :func:`wire_exact`)."""
        return {
            "nodeid": self.nodeid,
            "name": self.name,
            "path": self.path,
            "markers": [[m.name, list(m.args), dict(m.kwargs)] for m in self.markers],
            "own_marker_count": self.own_marker_count,
            "rootdir_name": self.rootdir_name,
            "dir_names": list(self.dir_names),
            "module_name": self.module_name,
            "class_names": list(self.class_names),
            "function_attrs": sorted(self.function_attrs),
            "param_id": self.param_id,
            "kind": self.kind,
            "parametrized": self.parametrized,
            "reordered": self.reordered,
        }

    @staticmethod
    def from_wire(data: Mapping[str, Any]) -> ItemRecord:
        return ItemRecord(
            nodeid=str(data["nodeid"]),
            name=str(data["name"]),
            path=str(data["path"]),
            markers=tuple(Mark(str(n), tuple(a), dict(k)) for n, a, k in data["markers"]),
            own_marker_count=data["own_marker_count"],
            rootdir_name=str(data["rootdir_name"]),
            dir_names=tuple(data["dir_names"]),
            module_name=str(data["module_name"]),
            class_names=tuple(data["class_names"]),
            function_attrs=frozenset(data["function_attrs"]),
            param_id=data["param_id"],
            kind=str(data["kind"]),
            parametrized=bool(data["parametrized"]),
            reordered=bool(data["reordered"]),
        )


def wire_exact(value: object) -> bool:
    """Does *value* survive JSON unchanged?  ``None``, bool, int, float, str, and lists and dicts
    (string keys) of those.  A tuple, a class or anything else would come back as something
    else, which a hook must not be shown: such a mark makes the record unusable."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(wire_exact(v) for v in cast("list[object]", value))
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and wire_exact(v)
            for k, v in cast("dict[object, object]", value).items()
        )
    return False


def mark_is_wire_exact(mark: Mark) -> bool:
    """A mark whose arguments reach a hook in another process exactly as they are here."""
    return all(wire_exact(a) for a in mark.args) and wire_exact(dict(mark.kwargs))


@dataclass(frozen=True)
class MarkAdded:
    """One ``item.add_marker(...)`` a hook made."""

    nodeid: str
    mark: Mark
    append: bool = True


@dataclass(frozen=True)
class CollectionEffects:
    """What one ``pytest_collection_modifyitems`` stage did to the list it was given.

    ``order`` is the surviving node ids in their final order.  ``deselected`` were removed and
    reported through ``config.hook.pytest_deselected`` (pytest counts them as deselected, once per
    report, so an item reported twice appears twice); ``vanished`` were removed without it (pytest
    does not count those at all).
    """

    order: list[str]
    deselected: list[str]
    vanished: list[str]
    marks: list[MarkAdded]


_NOTSET: Final = object()
_MISSING: Final = object()

#: The hooks this module runs, and the arguments each is offered (pluggy's specification).
_HOOKS: Final = {
    "pytest_addoption": ("parser", "pluginmanager"),
    "pytest_configure": ("config",),
    "pytest_sessionstart": ("session",),
    "pytest_collection_modifyitems": ("session", "config", "items"),
}
NATIVE_HOOKS: Final = frozenset(_HOOKS)

#: Absent on an xdist controller and on a serial run, which is what a conftest checks for.
_ABSENT_CONFIG_ATTRS: Final = frozenset({"workerinput", "workeroutput", "slaveinput"})

#: Every public name of pytest 8.4.2's ``Config`` class and of a configured instance (``vars``),
#: plus ``cache`` (set by the cache plugin).  Assigning to one must violate, not be stored as a
#: user attribute: ``config.args = [...]`` changes what pytest collects.  Taken once from a real
#: pytest; do not import pytest here.
_RESERVED_CONFIG: Final = frozenset(
    {
        "ArgsSource",
        "InvocationParams",
        "VERBOSITY_ASSERTIONS",
        "VERBOSITY_TEST_CASES",
        "add_cleanup",
        "addinivalue_line",
        "cwd_relative_nodeid",
        "fromdictargs",
        "get_terminal_writer",
        "get_verbosity",
        "getini",
        "getoption",
        "getvalue",
        "getvalueorskip",
        "inifile",
        "inipath",
        "invocation_dir",
        "issue_config_time_warning",
        "notify_exception",
        "parse",
        "pytest_cmdline_parse",
        "pytest_collection",
        "pytest_load_initial_conftests",
        "rootdir",
        "rootpath",
        "args",
        "args_source",
        "hook",
        "inicfg",
        "invocation_params",
        "known_args_namespace",
        "option",
        "pluginmanager",
        "stash",
        "trace",
        "cache",
    }
)

#: Every option string pytest 8.4.2's core (plugins autoload disabled) registers: a conftest
#: declaring one is an argparse conflict in pytest.  Plugins add their own: the worker puts
#: those in ``builtin_options``.  Frozen from a real pytest; do not import pytest here.
_PYTEST_CORE_FLAGS: Final = frozenset(
    {
        "--assert",
        "--basetemp",
        "--cache-clear",
        "--cache-show",
        "--capture",
        "--co",
        "--code-highlight",
        "--collect-in-virtualenv",
        "--collect-only",
        "--collectonly",
        "--color",
        "--confcutdir",
        "--config-file",
        "--continue-on-collection-errors",
        "--debug",
        "--deselect",
        "--disable-plugin-autoload",
        "--disable-pytest-warnings",
        "--disable-warnings",
        "--doctest-continue-on-failure",
        "--doctest-glob",
        "--doctest-ignore-import-errors",
        "--doctest-modules",
        "--doctest-report",
        "--durations",
        "--durations-min",
        "--exitfirst",
        "--failed-first",
        "--ff",
        "--fixtures",
        "--fixtures-per-test",
        "--force-short-summary",
        "--full-trace",
        "--fulltrace",
        "--funcargs",
        "--help",
        "--ignore",
        "--ignore-glob",
        "--import-mode",
        "--junit-prefix",
        "--junit-xml",
        "--junitprefix",
        "--junitxml",
        "--keep-duplicates",
        "--keepduplicates",
        "--last-failed",
        "--last-failed-no-failures",
        "--lf",
        "--lfnf",
        "--log-auto-indent",
        "--log-cli-date-format",
        "--log-cli-format",
        "--log-cli-level",
        "--log-date-format",
        "--log-disable",
        "--log-file",
        "--log-file-date-format",
        "--log-file-format",
        "--log-file-level",
        "--log-file-mode",
        "--log-format",
        "--log-level",
        "--markers",
        "--maxfail",
        "--new-first",
        "--nf",
        "--no-fold-skipped",
        "--no-header",
        "--no-showlocals",
        "--no-summary",
        "--noconftest",
        "--override-ini",
        "--pastebin",
        "--pdb",
        "--pdbcls",
        "--pyargs",
        "--pythonwarnings",
        "--quiet",
        "--rootdir",
        "--runxfail",
        "--setup-only",
        "--setup-plan",
        "--setup-show",
        "--setuponly",
        "--setupplan",
        "--setupshow",
        "--show-capture",
        "--showlocals",
        "--stepwise",
        "--stepwise-reset",
        "--stepwise-skip",
        "--strict",
        "--strict-config",
        "--strict-markers",
        "--sw",
        "--sw-reset",
        "--sw-skip",
        "--tb",
        "--trace",
        "--trace-config",
        "--traceconfig",
        "--verbose",
        "--verbosity",
        "--version",
        "--xfail-tb",
        "-V",
        "-W",
        "-c",
        "-h",
        "-k",
        "-l",
        "-m",
        "-o",
        "-p",
        "-q",
        "-r",
        "-s",
        "-v",
        "-x",
    }
)

#: The option strings each ``pytest11`` plugin a native session can have loaded registers (the
#: hand-off allowlist, ``rustest._fallback.SAFE_PLUGINS``): a conftest declaring one is the same
#: argparse conflict.  Frozen from the installed versions (pytest-xdist 3.8, pytest-cov,
#: pytest-asyncio 1.2, pytest-icdiff, pytest-rich, pytest-codeblocks 0.17, pytest-mock: none);
#: ``test_every_option_string_an_allowed_plugin_registers_is_a_conflict`` re-checks what is
#: installed.
_PLUGIN_FLAGS: Final[Mapping[str, frozenset[str]]] = {
    "xdist": frozenset(
        {
            "--dist",
            "--loadscope-reorder",
            "--max-worker-restart",
            "--maxprocesses",
            "--maxschedchunk",
            "--no-loadscope-reorder",
            "--numprocesses",
            "--px",
            "--rsyncdir",
            "--rsyncignore",
            "--testrunuid",
            "--tx",
            "-d",
            "-n",
        }
    ),
    "xdist.looponfail": frozenset({"--looponfail", "-f"}),
    "pytest_cov": frozenset(
        {
            "--cov",
            "--cov-append",
            "--cov-branch",
            "--cov-config",
            "--cov-context",
            "--cov-fail-under",
            "--cov-precision",
            "--cov-report",
            "--cov-reset",
            "--no-cov",
            "--no-cov-on-fail",
        }
    ),
    "asyncio": frozenset({"--asyncio-debug", "--asyncio-mode"}),
    "icdiff": frozenset(
        {
            "--icdiff-cols",
            "--icdiff-highlight",
            "--icdiff-line-numbers",
            "--icdiff-show-all-spaces",
            "--icdiff-strip-trailing-cr",
            "--icdiff-tabsize",
            "--icdiff-truncate",
        }
    ),
    "rich": frozenset({"--rich", "--rich-capture"}),
    "codeblocks": frozenset({"--codeblocks"}),
    "pytest_mock": frozenset(),
}

#: The ``markers`` lines pytest 8.4.2's own plugins add in their ``pytest_configure``, in the
#: order ``getini("markers")`` lists them (after the ini's, a conftest's and the plugins').
_PYTEST_MARKER_LINES: Final = (
    "filterwarnings(warning): add a warning filter to the given test. see "
    + "https://docs.pytest.org/en/stable/how-to/capture-warnings.html#pytest-mark-filterwarnings ",
    "skip(reason=None): skip the given test function with an optional reason. Example: "
    + 'skip(reason="no way of currently testing this") skips the test.',
    "skipif(condition, ..., *, reason=...): skip the given test function if any of the conditions"
    + " evaluate to True. Example: skipif(sys.platform == 'win32') skips the test if we are on the"
    + " win32 platform. See https://docs.pytest.org/en/stable/reference/reference.html"
    + "#pytest-mark-skipif",
    "xfail(condition, ..., *, reason=..., run=True, raises=None, strict=xfail_strict): mark the"
    + " test function as an expected failure if any of the conditions evaluate to True. Optionally"
    + " specify a reason for better reporting and run=False if you don't even want to execute the"
    + " test function. If only specific exception(s) are expected, you can list them in raises,"
    + " and if the test fails in other ways, it will be reported as a true failure. See"
    + " https://docs.pytest.org/en/stable/reference/reference.html#pytest-mark-xfail",
    "parametrize(argnames, argvalues): call a test function multiple times passing in different"
    + " arguments in turn. argvalues generally needs to be a list of values if argnames specifies"
    + " only one name or a list of tuples of values if argnames specifies multiple names. Example:"
    + " @parametrize('arg1', [1,2]) would lead to two calls of the decorated test function, one"
    + " with arg1=1 and another with arg1=2.see"
    + " https://docs.pytest.org/en/stable/how-to/parametrize.html for more info and examples.",
    "usefixtures(fixturename1, fixturename2, ...): mark tests as needing all of the specified"
    + " fixtures. see https://docs.pytest.org/en/stable/explanation/fixtures.html#usefixtures ",
    "tryfirst: mark a hook implementation function such that the plugin machinery will try to"
    + " call it first/as early as possible. DEPRECATED, use @pytest.hookimpl(tryfirst=True)"
    + " instead.",
    "trylast: mark a hook implementation function such that the plugin machinery will try to"
    + " call it last/as late as possible. DEPRECATED, use @pytest.hookimpl(trylast=True) instead.",
)

#: The ``markers`` line each allowed plugin adds (the rest add none).  With two of them loaded,
#: the order between them is pluggy's registration order, which is not known here.
_PLUGIN_MARKER_LINES: Final[Mapping[str, str]] = {
    "xdist": "xdist_group: specify group for tests should run in same session.in relation to one"
    + " another. Provided by pytest-xdist.",
    "pytest_cov": "no_cover: disable coverage for this test.",
    "asyncio": "asyncio: mark the test as a coroutine, it will be run using an asyncio event loop",
}

#: The ``dest`` of each of pytest's core options (:data:`_PYTEST_CORE_FLAGS`).
_PYTEST_CORE_DESTS: Final = frozenset(
    {
        "assertmode",
        "basetemp",
        "cacheclear",
        "cacheshow",
        "capture",
        "code_highlight",
        "collect_in_virtualenv",
        "collectonly",
        "color",
        "confcutdir",
        "continue_on_collection_errors",
        "debug",
        "deselect",
        "disable_plugin_autoload",
        "disable_warnings",
        "doctest_continue_on_failure",
        "doctest_ignore_import_errors",
        "doctestglob",
        "doctestmodules",
        "doctestreport",
        "durations",
        "durations_min",
        "failedfirst",
        "fold_skipped",
        "force_short_summary",
        "fulltrace",
        "help",
        "ignore",
        "ignore_glob",
        "importmode",
        "inifilename",
        "junitprefix",
        "keepduplicates",
        "keyword",
        "last_failed_no_failures",
        "lf",
        "log_auto_indent",
        "log_cli_date_format",
        "log_cli_format",
        "log_cli_level",
        "log_date_format",
        "log_file",
        "log_file_date_format",
        "log_file_format",
        "log_file_level",
        "log_file_mode",
        "log_format",
        "log_level",
        "logger_disable",
        "markers",
        "markexpr",
        "maxfail",
        "newfirst",
        "no_header",
        "no_summary",
        "noconftest",
        "override_ini",
        "pastebin",
        "plugins",
        "pyargs",
        "pythonwarnings",
        "reportchars",
        "rootdir",
        "runxfail",
        "setuponly",
        "setupplan",
        "setupshow",
        "show_fixtures_per_test",
        "showcapture",
        "showfixtures",
        "showlocals",
        "stepwise",
        "stepwise_reset",
        "stepwise_skip",
        "strict",
        "strict_config",
        "strict_markers",
        "tbstyle",
        "trace",
        "traceconfig",
        "usepdb",
        "usepdb_cls",
        "verbose",
        "version",
        "xfail_tb",
        "xmlpath",
    }
)

#: Markers pytest knows without an ini declaration.
_BUILTIN_MARKS: Final = frozenset(
    {
        "skip",
        "skipif",
        "xfail",
        "parametrize",
        "usefixtures",
        "filterwarnings",
        "tryfirst",
        "trylast",
    }
)

#: What ``pytest.mark.x`` is, by class name: pytest's ``MarkDecorator``, and rustest's own
#: stand-ins for it (its ``pytest.mark.skip`` / ``xfail`` / ... is a ``BareOrFactoryMark``).
_DECORATOR_CLASSES: Final = frozenset({"MarkDecorator", "BareOrFactoryMark"})

#: ``parser.addoption`` keywords that are argparse's and understood; anything else hands off.
_OPTION_KEYS: Final = frozenset(
    {"action", "dest", "default", "type", "help", "metavar", "choices", "nargs", "const"}
)

_INI_TYPES: Final = frozenset(
    {None, "string", "paths", "pathlist", "args", "linelist", "bool", "int", "float"}
)

#: argparse's default for a dest whose ``add_argument`` gave none, by ``action``.
_ACTION_DEFAULTS: Final[dict[str, object]] = {
    "store": None,
    "store_true": False,
    "store_false": True,
    "store_const": None,
    "append": None,
    "append_const": None,
    "count": None,
}

_TRUE: Final = frozenset({"y", "yes", "t", "true", "on", "1"})
_FALSE: Final = frozenset({"n", "no", "f", "false", "off", "0"})

_getattribute: Final = object.__getattribute__


# --------------------------------------------------------------------------------------
# shared state and the strict base
# --------------------------------------------------------------------------------------


@dataclass
class _Option:
    names: tuple[str, ...]
    dest: str
    value: object  # `_MISSING` for an option declared late with no default


@dataclass
class _State:
    """Everything the shim objects share for one runner."""

    rootpath: Path
    ini: Mapping[str, object]
    builtin: Mapping[str, object]
    invocation_dir: Path | None = None
    reserved: frozenset[str] = _PYTEST_CORE_FLAGS  # option strings pytest and its plugins own
    where: str = ""  # "<label>::<hook>" of the call in progress, or the last one
    active: bool = False  # a hook is running: the shims are valid only now
    violations: list[str] = field(default_factory=list[str])
    late: list[str] = field(default_factory=list[str])  # uses of a shim after its hook returned
    markers: list[str] = field(default_factory=list[str])
    options: dict[str, _Option] = field(default_factory=dict[str, _Option])  # by dest
    opt2dest: dict[str, str] = field(default_factory=dict[str, str])
    ini_decl: dict[str, tuple[str | None, object]] = field(
        default_factory=dict[str, tuple[str | None, object]]
    )
    user: dict[str, object] = field(default_factory=dict[str, object])
    deselected: list[object] = field(default_factory=list[object])
    marks: list[MarkAdded] = field(default_factory=list[MarkAdded])
    added: dict[str, list[MarkAdded]] = field(default_factory=dict[str, list[MarkAdded]])
    duplicates: set[str] = field(default_factory=set[str])  # node ids the stage's list repeats
    markers_unknown: bool = False  # the plugins' `markers` lines cannot be ordered
    after_argparse: bool = False  # options declared now were declared after argparse ran
    phase: int = 0  # bumped per registration / configure / sessionstart / modifyitems stage
    born: list[weakref.ref[_Shim]] = field(default_factory=list[weakref.ref["_Shim"]])

    def violate(self, what: str) -> NoReturn:
        """Record a use the shim does not support and raise.  Outside a hook call it is a stale
        use: too late to hand off, so it is listed apart and raised inside whatever used it."""
        if not self.active:
            reason = (
                f"{self.where} {what} after the hook returned: the objects a hook receives are "
                "valid only while it runs, which rustest's hook runtime requires"
            )
            self.late.append(reason)
            raise HookUnsupported(reason)
        reason = f"{self.where} {what}, which rustest's hook runtime does not support"
        self.violations.append(reason)
        raise HookUnsupported(reason)

    def registered_marks(self) -> set[str]:
        names = set(_BUILTIN_MARKS)
        for line in self.markers:
            names.add(line.split(":", 1)[0].split("(", 1)[0].strip())
        return names


class _Shim:
    """Base of every object a hook can reach: unknown attributes record and raise."""

    __slots__ = ("__weakref__", "_path", "_phase", "_st")
    _st: _State
    _path: str
    _phase: int

    def __init__(self, st: _State, path: str, *, persistent: bool = False) -> None:
        super().__init__()
        object.__setattr__(self, "_st", st)
        object.__setattr__(self, "_path", path)
        # a persistent shim (config, session) is the same object for every hook; the rest are
        # valid for the phase that made them
        object.__setattr__(self, "_phase", -1 if persistent else st.phase)
        if st.active:
            st.born.append(weakref.ref(self))

    def __getattribute__(self, name: str) -> object:
        if name == "_st" or name == "_path" or name == "_phase":
            return _getattribute(self, name)
        st = cast(_State, _getattribute(self, "_st"))
        if not st.active or name == "__dict__":
            st.violate(f"uses {_getattribute(self, '_path')}.{name}")
        phase = cast(int, _getattribute(self, "_phase"))
        if phase != -1 and phase != st.phase:
            st.violate(f"uses {_getattribute(self, '_path')}.{name} from an earlier hook")
        return _getattribute(self, name)

    def __getattr__(self, name: str) -> object:
        if name in ("_st", "_path", "_phase"):  # a shim built without __init__ (copy.copy)
            raise AttributeError(name)
        self._st.violate(f"uses {self._path}.{name}")

    def __setattr__(self, name: str, value: object) -> None:
        self._st.violate(f"sets {self._path}.{name}")

    def __delattr__(self, name: str) -> None:
        self._st.violate(f"deletes {self._path}.{name}")

    # Implicit special-method lookups bypass `__getattr__`, and the `TypeError` Python would
    # raise instead is an `Exception` a hook may swallow.
    def __iter__(self) -> NoReturn:
        self._st.violate(f"iterates {self._path}")

    def __getitem__(self, key: object) -> NoReturn:
        self._st.violate(f"subscripts {self._path}")

    def __contains__(self, key: object) -> bool:
        self._st.violate(f"tests membership in {self._path}")

    def __call__(self, *args: object, **kwargs: object) -> NoReturn:
        self._st.violate(f"calls {self._path}")


# --------------------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------------------


def _dest_of(names: Sequence[str], attrs: Mapping[str, Any]) -> str:
    """argparse's rule, which pytest inherits: ``dest=``, else the first long option, else the
    first short one, with dashes turned into underscores."""
    if "dest" in attrs:
        return str(attrs["dest"])
    long_names = [n for n in names if n.startswith("--")]
    return (long_names or list(names))[0].lstrip("-").replace("-", "_")


def _bad_option_name(name: object) -> str | None:
    """Why pytest's ``Argument`` / ``OptionGroup.addoption`` would reject this name, if it would."""
    if not isinstance(name, str) or len(name) < 2:
        return "a name that is not a long or short option"
    if len(name) == 2:
        if name[0] != "-" or name[1] == "-":
            return f"the invalid short option {name!r}"
        if name[1].islower():
            return f"the lowercase short option {name!r}, which pytest reserves"
    elif name[:2] != "--" or name[2] == "-":
        return f"the invalid long option {name!r}"
    return None


def _declare(st: _State, names: Sequence[str], attrs: Mapping[str, Any]) -> None:
    """``parser.addoption``: validate as pytest does, record the option and its default value."""
    if not names:
        st.violate("declares an option with no name")
    for name in names:
        problem = _bad_option_name(name)
        if problem:
            st.violate(f"declares {problem}")
        if name in st.opt2dest or name in st.builtin or name in st.reserved:
            st.violate(f"declares {name}, which is already an option")
    unknown = sorted(set(attrs) - _OPTION_KEYS)
    if unknown:
        st.violate(f"declares an option with {unknown[0]}=")
    if isinstance(attrs.get("type"), str):
        st.violate(f"declares {names[0]} with a string type")
    action = attrs.get("action", "store")
    if action not in _ACTION_DEFAULTS:
        st.violate(f"declares an option with action={action!r}")
    dest = _dest_of(names, attrs)
    if dest in st.options or dest in st.builtin or dest in _PYTEST_CORE_DESTS:
        st.violate(f"declares {names[0]} with dest {dest!r}, which another option already has")
    if "default" in attrs:
        value: object = attrs["default"]
        kind = attrs.get("type")
        if isinstance(value, str) and kind is not None:
            # argparse runs a string default through `type`, as if it came from the command line
            if kind not in (int, float, str):
                st.violate(f"declares {names[0]} with a custom type and a string default")
            value = kind(value)
    elif st.after_argparse:
        value = _MISSING
    else:
        value = _ACTION_DEFAULTS[action]
    st.options[dest] = _Option(tuple(names), dest, value)
    for name in names:
        st.opt2dest[name] = dest


class _Group(_Shim):
    __slots__ = ()

    def addoption(self, *names: str, **attrs: Any) -> None:
        _declare(self._st, names, attrs)


class _Parser(_Shim):
    """``parser``: the three calls conftests make."""

    __slots__ = ()

    def addoption(self, *names: str, **attrs: Any) -> None:
        _declare(self._st, names, attrs)

    def getgroup(self, name: str, description: str = "", after: str | None = None) -> _Group:
        return _Group(self._st, "parser.getgroup()")

    def addini(
        self, name: str, help: str, type: str | None = None, default: object = _NOTSET
    ) -> None:
        if type not in _INI_TYPES:
            self._st.violate(f"declares the ini option {name} with type {type!r}")
        if default is _NOTSET:
            default = _ini_default(type)
        self._st.ini_decl[name] = (type, default)


def _ini_default(kind: str | None) -> object:
    """`_pytest/config/argparsing.py::get_ini_default_for_type`."""
    if kind in ("paths", "pathlist", "args", "linelist"):
        return []
    return {"bool": False, "int": 0, "float": 0.0}.get(kind or "", "")


def _coerce_ini(st: _State, name: str, kind: str | None, value: object) -> object:
    """The conversions of `_pytest/config/__init__.py::Config._getini` for the supported types."""
    if kind == "args":
        return shlex.split(value) if isinstance(value, str) else value
    if kind == "linelist":
        if isinstance(value, str):
            return [t for t in (line.strip() for line in value.split("\n")) if t]
        return value
    if kind == "bool":
        word = str(value).strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        raise ValueError(f"invalid truth value {word!r}")
    if kind in ("string", None):
        return value
    if kind in ("int", "float"):
        if not isinstance(value, str):
            raise TypeError(f"Expected a {kind} string for option {name}, but got: {value!r}")
        return int(value) if kind == "int" else float(value)
    st.violate(f"declares the ini option {name} with type {kind!r}")


# --------------------------------------------------------------------------------------
# config and session
# --------------------------------------------------------------------------------------


class _Options(_Shim):
    """``config.option``: declared options only."""

    __slots__ = ()

    def __getattr__(self, name: str) -> object:
        st = self._st
        option = st.options.get(name)
        if option is not None and option.value is not _MISSING:
            return option.value
        if option is None and name in st.builtin:
            return st.builtin[name]
        st.violate(f"reads config.option.{name}")


class _HookProxy(_Shim):
    """``config.hook``: only ``pytest_deselected``, which is how a hook reports a removal."""

    __slots__ = ()

    def pytest_deselected(self, items: Iterable[object]) -> None:
        for item in items:
            if not issubclass(type(item), _Item):
                self._st.violate(
                    "calls config.hook.pytest_deselected with something that is not an item"
                )
            self._st.deselected.append(item)


class _InvocationParams(_Shim):
    """``config.invocation_params``: its ``dir``."""

    __slots__ = ("dir",)
    dir: Path

    def __init__(self, st: _State, directory: Path) -> None:
        super().__init__(st, "config.invocation_params", persistent=True)
        object.__setattr__(self, "dir", directory)


class _Config(_Shim):
    """``config``."""

    __slots__ = ("hook", "invocation", "option", "pluginmanager")
    hook: _HookProxy
    invocation: _InvocationParams | None
    option: _Options
    pluginmanager: _Shim

    def __init__(self, st: _State) -> None:
        super().__init__(st, "config", persistent=True)
        object.__setattr__(self, "option", _Options(st, "config.option", persistent=True))
        object.__setattr__(self, "hook", _HookProxy(st, "config.hook", persistent=True))
        object.__setattr__(
            self, "pluginmanager", _Shim(st, "config.pluginmanager", persistent=True)
        )
        object.__setattr__(
            self,
            "invocation",
            None if st.invocation_dir is None else _InvocationParams(st, st.invocation_dir),
        )

    @property
    def rootpath(self) -> Path:
        return self._st.rootpath

    @property
    def invocation_params(self) -> _InvocationParams:
        if self.invocation is None:
            self._st.violate("uses config.invocation_params")
        return self.invocation

    def getoption(self, name: str, default: object = _NOTSET, skip: bool = False) -> object:
        st = self._st
        dest = st.opt2dest.get(name, name)
        option = st.options.get(dest)
        if option is not None and option.value is not _MISSING:
            value = option.value
        elif option is None and dest in st.builtin:
            value = st.builtin[dest]
        elif option is None and name in st.builtin:
            value = st.builtin[name]
        else:
            st.violate(f"asks config.getoption({name!r}) for an option with no declared value")
        if value is None and skip:
            st.violate(f"calls config.getoption({name!r}, skip=True) on an unset option")
        return value

    def getini(self, name: str) -> object:
        st = self._st
        if name == "markers":
            if st.markers_unknown:
                st.violate("reads config.getini('markers') with two plugins adding markers")
            return st.markers
        if name not in st.ini_decl:
            st.violate(f"asks config.getini({name!r}) for an ini key no conftest declared")
        kind, default = st.ini_decl[name]
        if name not in st.ini:
            return default
        return _coerce_ini(st, name, kind, st.ini[name])

    def addinivalue_line(self, name: str, line: str) -> None:
        if name != "markers":
            self._st.violate(f"calls config.addinivalue_line({name!r}, ...)")
        self._st.markers.append(line)

    def __getattr__(self, name: str) -> object:
        if name == "_st" or name == "_path":
            raise AttributeError(name)
        st = self._st
        if name in st.user:
            return st.user[name]
        if name in _ABSENT_CONFIG_ATTRS:
            raise AttributeError(name)
        st.violate(f"uses config.{name}")

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("_") or name in _RESERVED_CONFIG:
            self._st.violate(f"sets config.{name}")
        self._st.user[name] = value


class _Session(_Shim):
    """``session``: its ``config``, which is all the supported hooks read from it."""

    __slots__ = ("config",)
    config: _Config

    def __init__(self, st: _State, config: _Config) -> None:
        super().__init__(st, "session", persistent=True)
        object.__setattr__(self, "config", config)


# --------------------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------------------


class _Keywords(_Shim):
    """``item.keywords``: ``"slow" in item.keywords``, and nothing else."""

    __slots__ = ("_rs_names",)
    _rs_names: set[str]

    def __init__(self, st: _State, names: set[str]) -> None:
        super().__init__(st, "item.keywords")
        object.__setattr__(self, "_rs_names", names)

    def __contains__(self, name: object) -> bool:
        st = self._st
        phase = cast(int, _getattribute(self, "_phase"))
        if not st.active or (phase != -1 and phase != st.phase):
            st.violate("tests membership in item.keywords")
        return name in self._rs_names

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> NoReturn:
        self._st.violate("takes the length of item.keywords")


class _Kwargs(Mapping[str, Any]):
    """A mark's ``kwargs``: readable like a dict, and a write violates (a write to pytest's
    persists, and nothing here would carry it to the worker)."""

    __slots__ = ("_data", "_st")

    def __init__(self, st: _State, data: Mapping[str, Any]) -> None:
        super().__init__()
        self._st = st
        self._data = dict(data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return repr(self._data)

    def _write(self, *args: object, **kwargs: object) -> NoReturn:
        self._st.violate("modifies the kwargs of a mark")

    __setitem__ = __delitem__ = __ior__ = _write
    update = pop = popitem = clear = setdefault = _write


class _MarkView:
    """What ``get_closest_marker`` / ``iter_markers`` return: pytest's ``Mark``, read-only."""

    __slots__ = ("args", "kwargs", "name")

    def __init__(self, st: _State, mark: Mark) -> None:
        super().__init__()
        object.__setattr__(self, "name", mark.name)
        object.__setattr__(self, "args", tuple(mark.args))
        object.__setattr__(self, "kwargs", _Kwargs(st, mark.kwargs))

    def __getattr__(self, name: str) -> object:
        self.kwargs._st.violate(f"uses a mark's .{name}")

    def __setattr__(self, name: str, value: object) -> None:
        self.kwargs._st.violate(f"sets a mark's .{name}")

    def __delattr__(self, name: str) -> None:
        self.kwargs._st.violate(f"deletes a mark's .{name}")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _MarkView):
            return NotImplemented
        return (self.name, self.args, dict(self.kwargs)) == (
            other.name,
            other.args,
            dict(other.kwargs),
        )

    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __repr__(self) -> str:
        return f"Mark(name={self.name!r}, args={self.args!r}, kwargs={dict(self.kwargs)!r})"


def _strict_markers(st: _State) -> bool:
    """Whether an unknown ``pytest.mark.x`` is an error in pytest here (``--strict-markers``, or
    a ``filterwarnings`` line that turns ``PytestUnknownMarkWarning`` into one)."""
    for key in ("strict_markers", "--strict-markers", "strict"):
        if st.builtin.get(key) or str(st.ini.get(key, "")).strip().lower() in ("1", "true", "yes"):
            return True
    lines = st.ini.get("filterwarnings", [])
    if isinstance(lines, str):
        lines = lines.splitlines()
    return any(str(line).strip().startswith("error") for line in cast("Iterable[object]", lines))


def _mark_of(st: _State, marker: object) -> Mark:
    """The ``Mark`` ``Node.add_marker`` would store, or a violation where pytest raises or warns:
    a string must be a registered marker (an unregistered one warns, or fails under
    ``--strict-markers``), and anything but a string or a ``MarkDecorator`` is a ``ValueError``
    (a ``Mark`` read from another item included)."""
    if isinstance(marker, str):
        if marker not in st.registered_marks():
            st.violate(f"passes item.add_marker the unregistered marker {marker!r}")
        return Mark(marker)
    if any(cls.__name__ in _DECORATOR_CLASSES for cls in type(marker).__mro__):
        name = cast(Any, marker).name
        if isinstance(name, str) and _strict_markers(st) and name not in st.registered_marks():
            st.violate(
                f"passes item.add_marker the unregistered marker {name!r} under strict markers"
            )
        args = cast(Any, marker).args
        kwargs = cast(Any, marker).kwargs
        if isinstance(name, str) and isinstance(args, tuple) and isinstance(kwargs, Mapping):
            return Mark(
                name, cast("tuple[Any, ...]", args), dict(cast("Mapping[str, Any]", kwargs))
            )
    st.violate("passes item.add_marker something that is not a string or a mark decorator")


def _with_mark(
    st: _State, markers: list[Mark], own: int | None, mark: Mark, append: bool
) -> int | None:
    """Place ``mark`` as ``Node.add_marker`` does (``own_markers.append`` / ``insert(0, ...)``,
    both ahead of every enclosing node's marks); returns the new own-marker count."""
    if not append:
        markers.insert(0, mark)
        return None if own is None else own + 1
    if own is None:
        if markers:
            st.violate("calls item.add_marker with no own_marker_count to place the mark by")
        markers.append(mark)
        return None
    markers.insert(own, mark)
    return own + 1


class _Item(_Shim):
    """One collected test, as far as a hook may look at it.  Never instantiated directly: see
    :func:`_item_class`."""

    __slots__ = (
        "_rs_names",
        "_rs_own",
        "_rs_marks",
        "_rs_param",
        "_rs_views",
        "config",
        "keywords",
        "name",
        "nodeid",
        "path",
    )
    _rs_names: set[str]
    _rs_own: int | None
    _rs_param: bool
    _rs_marks: list[Mark]
    _rs_views: list[_MarkView]
    config: _Config
    keywords: _Keywords
    name: str
    nodeid: str
    path: Path

    def iter_markers(self, name: str | None = None) -> Iterator[_MarkView]:
        if self._rs_param and (name is None or name == "parametrize"):
            self._st.violate(
                "reads the markers of a parametrized test, including its parametrize mark"
            )
        for view in list(self._rs_views):
            if name is None or view.name == name:
                yield view

    def get_closest_marker(self, name: str, default: object = None) -> object:
        return next(self.iter_markers(name), default)

    def add_marker(self, marker: object, append: bool = True) -> None:
        st = self._st
        if self.nodeid in st.duplicates:  # the worker keeps one plan per node id
            st.violate(f"adds a mark to {self.nodeid}, which the item list holds more than once")
        mark = _mark_of(st, marker)
        index = (
            0 if not append else (self._rs_own if self._rs_own is not None else len(self._rs_marks))
        )
        own = _with_mark(st, self._rs_marks, self._rs_own, mark, append)
        object.__setattr__(self, "_rs_own", own)
        self._rs_views.insert(index, _MarkView(st, mark))
        self._rs_names.add(mark.name)
        st.marks.append(MarkAdded(self.nodeid, mark, append))

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"


@functools.cache
def _item_class(kind: str) -> type[_Item]:
    """A real instance of the classes ``pytest.Function`` / ``pytest.Item`` / ``pytest.Node``
    resolve to under rustest (``isinstance`` never reaches ``__getattr__``, so a look-alike would
    answer False silently), named as pytest names it (``type(item).__name__``)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # `_pytest_stub` warns that it is private API
        from rustest._pytest_stub import nodes
        from rustest.compat import pytest as compat
    names = (
        ("Function", "Item", "Node")
        if kind == "function"
        else ("TestCaseFunction", "Function", "Item", "Node")
    )
    bases = (_Item, *(cast(type, getattr(compat, n)) for n in names), nodes.Item)
    return cast("type[_Item]", type(names[0], bases, {"__module__": __name__}))


def _new_item(st: _State, config: _Config, record: ItemRecord) -> _Item:
    item = _item_class(record.kind)(st, "item")
    names = {
        "",
        record.rootdir_name,
        *record.dir_names,
        record.module_name,
        *record.class_names,
        record.name,
        *record.function_attrs,
        *(mark.name for mark in record.markers),
    }
    if record.param_id is not None:
        names.add(record.param_id)
    if record.parametrized:  # the `parametrize` mark the record leaves out
        names.add("parametrize")
    markers = list(record.markers)
    for slot, value in (
        ("nodeid", record.nodeid),
        ("name", record.name),
        ("path", Path(record.path)),
        ("config", config),
        ("keywords", _Keywords(st, names)),
        ("_rs_names", names),
        ("_rs_own", record.own_marker_count),
        ("_rs_marks", markers),
        ("_rs_param", record.parametrized),
        ("_rs_views", [_MarkView(st, mark) for mark in markers]),
    ):
        object.__setattr__(item, slot, value)
    return item


# --------------------------------------------------------------------------------------
# the runner
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Impl:
    label: str
    hook: str
    func: Callable[..., object]
    argnames: tuple[str, ...]
    tryfirst: bool
    trylast: bool
    initial: bool = True  # registered before `pytest_sessionstart` (so before FixtureManager)


@dataclass
class _Plugin:
    label: str
    impls: dict[str, _Impl]


def _impl_options(value: object) -> dict[str, object]:
    """The ``pytest_impl`` dict ``pytest.hookimpl`` left on a function, or ``{}``."""
    try:
        options = getattr(value, "pytest_impl", None) if callable(value) else None
    except Exception:  # noqa: BLE001 - a module-level object with a hostile __getattr__
        return {}
    return dict(options) if isinstance(options, dict) else {}  # pyright: ignore[reportUnknownArgumentType]


def _pluggy_argnames(func: Callable[..., object]) -> tuple[str, ...]:
    """pluggy's ``varnames``: the positional parameters *without* defaults; ``*args``,
    ``**kwargs`` and keyword-only parameters are not offered an argument at all."""
    positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return tuple(
        p.name
        for p in inspect.signature(func).parameters.values()
        if p.kind in positional and p.default is p.empty
    )


def _insert(chain: list[_Impl], impl: _Impl) -> None:
    """pluggy's ``HookCaller._add_hookimpl`` without wrappers; the chain is called reversed."""
    if impl.trylast:
        chain.insert(0, impl)
    elif impl.tryfirst:
        chain.append(impl)
    else:
        i = len(chain) - 1
        while i >= 0 and chain[i].tryfirst:
            i -= 1
        chain.insert(i + 1, impl)


class HookRunner:
    """Registers conftest / plugin modules and calls their supported hooks.

    Once a violation exists every method does nothing: the session is being handed to pytest,
    and a hook with side effects must not keep running for nothing.  ``reasons`` is that list.
    """

    def __init__(
        self,
        *,
        rootpath: str | Path,
        ini: Mapping[str, object] | None = None,
        builtin_options: Mapping[str, object] | None = None,
        invocation_dir: str | Path | None = None,
        plugins: Iterable[str] = (),
    ) -> None:
        """*plugins* are the ``pytest11`` entry-point names pytest would load: their option
        strings (:data:`_PLUGIN_FLAGS`) are as taken as pytest's own, and their ``markers``
        lines follow the conftests' in ``getini("markers")``."""
        super().__init__()
        plugins = tuple(plugins)
        self._st = _State(
            Path(rootpath),
            ini or {},
            builtin_options or {},
            None if invocation_dir is None else Path(invocation_dir),
            _PYTEST_CORE_FLAGS.union(*(_PLUGIN_FLAGS.get(name, ()) for name in plugins)),
        )
        plugin_lines = [_PLUGIN_MARKER_LINES[n] for n in plugins if n in _PLUGIN_MARKER_LINES]
        self._marker_tail: tuple[str, ...] | None = (
            (*plugin_lines, *_PYTEST_MARKER_LINES)
            if len(plugin_lines) <= 1 and all(n in _PLUGIN_FLAGS for n in plugins)
            else None
        )
        if not getattr(sys, "_is_gil_enabled", lambda: True)():
            # ponytail: `_kept` and `_items_released` compare `sys.getrefcount`, which deferred
            # and biased reference counting make unreliable without the GIL; decline rather
            # than guess. Upgrade: a retention check that does not count references.
            self._st.violations.append(
                "this is a free-threaded build, where rustest's hook runtime cannot tell what a"
                + " hook keeps"
            )
        lines = _coerce_ini(self._st, "markers", "linelist", (ini or {}).get("markers", []))
        if isinstance(lines, list):
            self._st.markers.extend(str(line) for line in cast("list[object]", lines))
        self._config = _Config(self._st)
        self._session = _Session(self._st, self._config)
        self._plugins: list[_Plugin] = []
        self._configured = False

    # -- results ------------------------------------------------------------------------

    @property
    def reasons(self) -> list[str]:
        """Why the session must go to pytest; empty while every hook ran inside the shim."""
        return list(self._st.violations)

    @property
    def late_reasons(self) -> list[str]:
        """Uses of a shim after its hook returned.  Each was also raised, as
        :class:`HookUnsupported`, inside whatever made it (usually a test)."""
        return list(self._st.late)

    @property
    def markers(self) -> list[str]:
        """The ini ``markers`` lines plus those added with ``config.addinivalue_line``."""
        return list(self._st.markers)

    @property
    def user_attributes(self) -> dict[str, object]:
        """The attributes hooks stored on ``config``; copy them onto the worker's own config."""
        return dict(self._st.user)

    def lookup_option(self, name: str) -> tuple[bool, object]:
        """``(declared, value)`` for a flag or ``dest``.  A declared option answers with its value
        and **ignores a call-site ``default=``**, exactly as pytest (and the shim) does; the
        worker's ``getoption`` must do the same and use its own logic only when ``declared`` is
        false."""
        st = self._st
        option = st.options.get(st.opt2dest.get(name, name))
        if option is not None and option.value is not _MISSING:
            return True, option.value
        return False, None

    def lookup_ini(self, name: str) -> tuple[bool, object]:
        """``(declared, value)`` for an ``addini`` key (or ``markers``), converted as pytest
        converts it.  A type this runtime does not model raises :class:`HookUnsupported` (late)."""
        st = self._st
        if name == "markers":
            if st.markers_unknown:
                st.violate("reads config.getini('markers') with two plugins adding markers")
            return True, list(st.markers)
        if name not in st.ini_decl:
            return False, None
        kind, default = st.ini_decl[name]
        if name not in st.ini:
            return True, default
        return True, _coerce_ini(st, name, kind, st.ini[name])

    def note_violation(self, what: str) -> None:
        """Record a reason the session must go to pytest (for a problem found outside a hook)."""
        self._violate_quietly(what)

    def implements(self, hook: str) -> bool:
        """Does any registered module define *hook* (one of :data:`NATIVE_HOOKS`)?"""
        return any(hook in plugin.impls for plugin in self._plugins)

    # -- registration -------------------------------------------------------------------

    def register(self, label: str, module: object, *, initial: bool = True) -> None:
        """Register one conftest or plugin module, in pytest's registration order.

        ``initial`` is true for what pytest loads before it has parsed the command line (the
        argument paths' conftests and their ``pytest_plugins``) and false for a conftest the
        collection walk reaches later.  Like pluggy's historic hooks, ``pytest_addoption``
        runs at registration, and ``pytest_configure`` too if ``configure`` already ran.
        """
        if self._st.violations:
            return
        self._st.phase += 1
        self._st.after_argparse = not initial
        impls: dict[str, _Impl] = {}
        for name, value in vars(module).items():
            options = _impl_options(value)
            if options.get("specname"):
                self._refuse(label, name, "implements a hook through specname=")
                return
            if name not in _HOOKS or not callable(value):
                continue
            if options.get("hookwrapper") or options.get("wrapper"):
                self._refuse(label, name, "is a hook wrapper")
                return
            if inspect.isgeneratorfunction(value) or inspect.iscoroutinefunction(value):
                self._refuse(label, name, "is a generator or coroutine function")
                return
            try:
                argnames = _pluggy_argnames(value)
            except (TypeError, ValueError):
                self._refuse(label, name, "has a signature that cannot be read")
                return
            unknown = [a for a in argnames if a not in _HOOKS[name]]
            if unknown:
                self._refuse(label, name, f"asks for the argument {unknown[0]!r}")
                return
            impls[name] = _Impl(
                label,
                name,
                value,
                argnames,
                bool(options.get("tryfirst")),
                bool(options.get("trylast")),
                initial,
            )
        plugin = _Plugin(label, impls)
        self._plugins.append(plugin)
        if "pytest_addoption" in impls:
            self._call(
                impls["pytest_addoption"],
                parser=_Parser(self._st, "parser"),
                pluginmanager=_Shim(self._st, "pluginmanager"),
            )
        if self._configured and "pytest_configure" in impls:
            self._call(impls["pytest_configure"], config=self._config)

    def _refuse(self, label: str, hook: str, why: str) -> None:
        self._st.where = f"{label}::{hook}"
        reason = f"{self._st.where} {why}, which rustest's hook runtime does not support"
        self._st.violations.append(reason)

    # -- the calls ----------------------------------------------------------------------

    def configure(self) -> None:
        """``pytest_configure`` over everything registered so far."""
        if self._st.violations or self._configured:
            return
        self._configured = True
        self._st.phase += 1
        self._run_all("pytest_configure", config=self._config)
        # The plugins' and pytest's own `pytest_configure` run after every conftest's (LIFO).
        if self._marker_tail is None:
            self._st.markers_unknown = True
        else:
            self._st.markers.extend(self._marker_tail)

    def sessionstart(self) -> None:
        self._st.phase += 1
        self._run_all("pytest_sessionstart", session=self._session)

    def modify_collection_before_selection(
        self, records: Sequence[ItemRecord]
    ) -> CollectionEffects:
        """The ``tryfirst`` and plain ``pytest_collection_modifyitems`` hooks, over the full
        collection in collection order.  Run this *before* ``-k`` / ``-m`` / ``--deselect``."""
        return self._modify(records, late=False)

    def modify_collection_after_selection(self, records: Sequence[ItemRecord]) -> CollectionEffects:
        """The ``trylast`` hooks, over the records ``-k`` / ``-m`` / ``--deselect`` left (in
        their current order); run before ``--lf`` / ``--ff``.  Marks stage one added are
        applied to these items by the runner."""
        return self._modify(records, late=True)

    def _modify(self, records: Sequence[ItemRecord], *, late: bool) -> CollectionEffects:
        st = self._st
        st.phase += 1
        st.marks.clear()
        st.deselected.clear()
        if not late:
            st.added.clear()  # one runner per session, each stage once: nothing carries over
        empty = CollectionEffects([], [], [], [])
        chain = [i for i in self._chain("pytest_collection_modifyitems") if i.trylast == late]
        if st.violations or not chain:
            return empty if st.violations else self._unchanged(records)
        for record in records:
            if record.kind not in ("function", "unittest"):
                self._violate_quietly(
                    f"collected an item the hook runtime cannot model ({record.kind})"
                )
                return empty
        if any(record.reordered for record in records):
            # pytest's FixtureManager reorders by fixture scope between the plain impls
            # registered after sessionstart and those before it; see `ItemRecord.reordered`.
            for impl in chain:
                if impl.trylast or (impl.initial and not impl.tryfirst):
                    st.where = f"{impl.label}::{impl.hook}"
                    self._violate_quietly(
                        "would be shown the items in the order pytest's fixture-scope"
                        + " reordering gives them (a parametrized fixture wider than function)"
                    )
                    return empty
        counts = Counter(record.nodeid for record in records)
        st.duplicates = {nodeid for nodeid, count in counts.items() if count > 1}
        prepared = [self._with_earlier_marks(record) for record in records]
        items = [_new_item(st, self._config, record) for record in prepared]
        ids = {id(item): record.nodeid for item, record in zip(items, records, strict=True)}
        shown: list[object] = list(items)
        before = _item_refs(items, [shown])
        for impl in chain:
            if st.violations:
                break
            self._call(impl, session=self._session, config=self._config, items=shown)
        if st.violations:
            return empty
        if not self._items_released(items, shown, before):
            self._violate_quietly("kept a reference to an item after returning")
            return empty
        ordered: list[str] = []
        seen: set[int] = set()
        for item in shown:
            if id(item) not in ids or id(item) in seen:
                self._violate_quietly("left the item list with items it did not receive, or twice")
                return empty
            seen.add(id(item))
            ordered.append(ids[id(item)])
        counted = {id(item) for item in st.deselected}
        if counted & seen:
            self._violate_quietly("reported an item as deselected and kept it")
            return empty
        if any(id(item) not in ids for item in st.deselected):
            self._violate_quietly("reported an item it did not receive as deselected")
            return empty
        for added in st.marks:
            st.added.setdefault(added.nodeid, []).append(added)
        return CollectionEffects(
            order=ordered,
            deselected=[ids[id(item)] for item in st.deselected],
            vanished=[ids[id(i)] for i in items if id(i) not in seen and id(i) not in counted],
            marks=list(st.marks),
        )

    def _items_released(self, items: list[_Item], shown: list[object], before: list[int]) -> bool:
        for attempt in range(2):
            after = _item_refs(items, [shown, self._st.deselected])
            if not any(a > b for a, b in zip(after, before, strict=True)):
                return True
            if attempt == 0:
                _ = gc.collect()
        return False

    @staticmethod
    def _unchanged(records: Sequence[ItemRecord]) -> CollectionEffects:
        return CollectionEffects([r.nodeid for r in records], [], [], [])

    def _with_earlier_marks(self, record: ItemRecord) -> ItemRecord:
        added = self._st.added.get(record.nodeid)
        if not added:
            return record
        markers = list(record.markers)
        own = record.own_marker_count
        for entry in added:
            own = _with_mark(self._st, markers, own, entry.mark, entry.append)
        return replace(record, markers=tuple(markers), own_marker_count=own)

    # -- machinery ----------------------------------------------------------------------

    def _violate_quietly(self, what: str) -> None:
        self._st.violations.append(
            f"{self._st.where or 'a hook'} {what}, which rustest's hook runtime does not support"
        )

    def _chain(self, hook: str) -> list[_Impl]:
        chain: list[_Impl] = []
        for plugin in self._plugins:
            impl = plugin.impls.get(hook)
            if impl is not None:
                _insert(chain, impl)
        return chain[::-1]  # pluggy calls the chain last-registered first

    def _run_all(self, hook: str, **available: object) -> None:
        for impl in self._chain(hook):
            if self._st.violations:
                return
            self._call(impl, **available)

    def _tracked(self, available: Mapping[str, object]) -> list[_Shim]:
        """The shims whose reference counts must not grow across a hook: what it was handed, and
        the persistent objects hanging off ``config``."""
        config = self._config
        found: list[_Shim] = [
            config,
            self._session,
            _getattribute(config, "option"),
            _getattribute(config, "hook"),
            _getattribute(config, "pluginmanager"),
        ]
        known = {id(o) for o in found}
        for value in available.values():
            if issubclass(type(value), _Shim) and id(value) not in known:
                known.add(id(value))
                found.append(cast(_Shim, value))
        return found

    def _kept(self, tracked: list[_Shim], before: list[int]) -> str | None:
        """The path of a shim the hook kept a reference to, or ``None``.  Before looking again it
        collects garbage, so a reference cycle that is already dead is not a retention."""
        for attempt in range(2):
            after = _refs(tracked)
            leaked = [i for i, (a, b) in enumerate(zip(after, before, strict=True)) if a > b]
            alive = [r for r in self._st.born if r() is not None]
            if not leaked and not alive:
                return None
            if attempt == 0:
                del alive
                _ = gc.collect()
                continue
            if leaked:
                return str(_getattribute(tracked[leaked[0]], "_path"))
            survivor = alive[0]()
            return str(_getattribute(survivor, "_path")) if survivor is not None else "a shim"
        return None

    def _call(self, impl: _Impl, **available: object) -> None:
        """Call one hook as pluggy does: the parameters without defaults, positionally."""
        st = self._st
        st.where = f"{impl.label}::{impl.hook}"
        args = [available[name] for name in impl.argnames]
        tracked = self._tracked(available)
        before = _refs(tracked)
        st.born.clear()
        st.active = True
        try:
            _ = impl.func(*args)
        except HookUnsupported:
            return  # already recorded
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001 - `pytest.skip`/`exit`, SystemExit, errors:
            # pytest treats each differently (a skip here is not a test's outcome); hand off
            self._violate_quietly(f"raised {type(exc).__name__}: {exc}")
            return
        finally:
            st.active = False
        kept = self._kept(tracked, before)
        st.born.clear()
        if kept is not None:
            self._violate_quietly(
                f"kept a reference to {kept} after returning (for example `CFG = config`)"
            )


def _refs(objs: Sequence[object]) -> list[int]:
    return [sys.getrefcount(o) for o in objs]


def _item_refs(items: Sequence[_Item], containers: Sequence[Sequence[object]]) -> list[int]:
    """Reference counts of the items and their keywords, not counting the lists that hold them."""
    counts = Counter(id(o) for c in containers for o in c)
    return [sys.getrefcount(i) - counts[id(i)] for i in items] + [
        sys.getrefcount(_getattribute(i, "keywords")) for i in items
    ]
