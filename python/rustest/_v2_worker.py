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

**Known scope limits of this worker** (each deliberate, none silent):

* *No execute op yet.*  The wire is at protocol v2, which defines ``execute_test`` /
  ``test_result`` (``src/v2/protocol.rs``); this module provides collection and the
  fixture machinery an execute half needs, but not the op itself.  An ``execute_test``
  request therefore falls through to :func:`main`'s unknown-op branch and exits 2 rather
  than being answered or ignored.

* *No warning channel.*  pytest reports a class with ``__init__``/``__new__`` via
  ``PytestCollectionWarning`` and still exits 0.  The protocol has no warnings field,
  so such a class is skipped and **no error entry is emitted** — emitting one would
  turn pytest's exit 0 into the orchestrator's exit 2 and diverge from the oracle.
* *Marks are carried, never evaluated.*  ``skipif``/``xfail`` conditions travel as
  data; evaluation is the next task.
* *No ``pytest_generate_tests`` hook and no ``indirect=`` parametrization.*  Decorator
  metadata and fixture ``params=`` are the only two sources of parametrization; a module-
  or class-level ``pytest_generate_tests`` is not called.
* *No item reordering.*  pytest groups tests sharing a higher-scoped parametrized fixture
  (`_pytest/fixtures.py::reorder_items`); that pass needs the whole session's item list,
  which a per-file worker does not have.  Ids stay correct; order can differ for a module-
  or wider-scoped parametrized fixture.
* *``session`` and ``package`` scope are per-worker.*  See :data:`_SCOPE_BUCKET`.
* *A file is enumerated exactly as handed over.*  pytest never collects ``conftest.py``
  as a test module (it matches no ``python_files`` pattern and is loaded as a plugin),
  so the orchestrator should not send one as a collect target; doing so anyway is
  harmless — a conventional conftest yields no tests — and imports it under its real
  name.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence, Set as AbstractSet
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
import sys
import traceback
import types
from typing import Any, Final, NotRequired, TypedDict, cast
import unittest

__all__ = [
    "BUILTIN_FIXTURES",
    "DEFAULT_NAMING",
    "PROTOCOL_VERSION",
    "SCOPE_NAMES",
    "UNSUPPORTED_BUILTIN_FIXTURES",
    "CollectionRefusal",
    "FixtureClosure",
    "FixtureDef",
    "FixtureLookupError",
    "FixtureRegistry",
    "FixtureRunner",
    "NotInitializedError",
    "Naming",
    "ScopeMismatch",
    "ExecutionPlan",
    "build_closure",
    "build_nodeid",
    "build_registry",
    "collect_file",
    "collect_module",
    "conftest_chain",
    "encode_response",
    "enumerate_module",
    "fixture_param_dimensions",
    "handle_init",
    "handle_shutdown",
    "import_conftest",
    "import_test_module",
    "install_pytest_shim",
    "main",
    "matches_name_pattern",
    "resolve_module_identity",
    "execution_plan",
]

#: The protocol this worker **speaks**.  Mirrors ``PROTOCOL_VERSION`` in
#: ``src/v2/protocol.rs``; the two must move together — a worker declaring the other side's
#: number turns every run into a handshake error.
#:
#: v2 adds the execute ops (``execute_test`` -> ``test_result``) and ``init.invocation_dir``.
#: This worker declares 2 because it speaks v2's *handshake*; the execute half lands in the
#: next tasks, and until then an ``execute_test`` request takes the unknown-op path in
#: :func:`main` — exit 2, loudly, never a silent skip (pinned by
#: ``test_execute_test_is_not_implemented_yet_and_is_protocol_fatal``).
PROTOCOL_VERSION: Final = 2


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
    op: str
    path: str
    tests: NotRequired[list[CollectedTestDict]]
    error: NotRequired[CollectionErrorDict]


class ByeResponse(TypedDict):
    op: str


class NotInitializedError(Exception):
    """`collect_file` arrived before `init` — a protocol violation, not a bad file.

    Routed to the fatal-on-drift path in :func:`main` (exit 2), never turned into a
    ``collected`` error entry: the worker has no rootdir, so it could not even name
    the file correctly in one.
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


def _spec_from_mark_dict(raw: object, owner: object) -> MarkSpecDict:
    """One ``__rustest_marks__`` entry -> one ``MarkSpec``.

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
    spec: MarkSpecDict = {"name": str(mark.get("name", ""))}
    args = [_json_safe(arg) for arg in cast(Sequence[object], mark.get("args", ()))]
    if args:
        spec["args"] = args
    kwargs = {
        str(key): _json_safe(value)
        for key, value in cast(Mapping[object, object], mark.get("kwargs", {})).items()
    }
    if kwargs:
        spec["kwargs"] = kwargs
    return spec


def _spec_from_pytestmark(entry: object, owner: object) -> MarkSpecDict:
    """One ``pytestmark`` entry -> one ``MarkSpec``.

    ``pytestmark`` holds *decorator objects*, not dicts.  Through the compat shim
    ``pytest.mark.slow`` is ``decorators.py::MarkGenerator._create_mark``'s
    ``_MarkDecoratorFactory`` (bare, uncalled — it carries only ``mark_name``), while
    ``pytest.mark.skipif(...)`` is a ``MarkDecorator`` (``name``/``args``/``kwargs``).
    Real pytest's ``MarkDecorator`` exposes the same three attributes, so the duck
    typing covers a worker running without the shim too.  Anything else is malformed
    and refuses the file rather than silently dropping a mark.
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
        return {"name": bare_name}
    raise CollectionRefusal(
        "malformed pytestmark entry on "
        + f"{getattr(owner, '__qualname__', getattr(owner, '__name__', owner))!r}: "
        + f"{entry!r} is not a mark"
    )


def _pytestmark_specs(obj: object, *, consider_mro: bool) -> list[MarkSpecDict]:
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

    specs: list[MarkSpecDict] = []
    for item in mark_lists:
        entries = cast(Sequence[object], item) if isinstance(item, list) else [item]
        specs.extend(_spec_from_pytestmark(entry, obj) for entry in entries)
    return specs


def _rustest_mark_specs(obj: object, *, consider_mro: bool) -> list[MarkSpecDict]:
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

    specs: list[MarkSpecDict] = []
    for raw_list in mark_lists:
        if not isinstance(raw_list, list):
            raise CollectionRefusal(
                "malformed rustest mark metadata on "
                + f"{getattr(obj, '__qualname__', obj)!r}: "
                + f"__rustest_marks__ must be a list, got {raw_list!r}"
            )
        specs.extend(_spec_from_mark_dict(raw, obj) for raw in cast(Sequence[object], raw_list))
    return specs


def _mark_specs(obj: object, *, consider_mro: bool = False) -> list[MarkSpecDict]:
    """All marks carried *directly* by *obj*, as ``MarkSpec`` dicts.

    Three sources, in the order pytest itself would report them for one node:

    * ``pytestmark`` — the attribute pytest reads
      (`_pytest/nodes.py` line 284/1599: ``own_markers.extend(get_unpacked_marks(self.obj))``);
    * ``__rustest_marks__`` — rustest's own decorator metadata
      (``decorators.py::MarkDecorator.__call__``);
    * ``__rustest_skip__`` — the reason string set by ``decorators.py::skip_decorator``,
      which is what the *compat* ``pytest.mark.skip`` uses instead of a mark entry.

    Conditions are **not** evaluated here.  (Note a v1-ism inherited through the
    shim: ``decorators.py::MarkDecorator._normalize_args`` evaluates a *string*
    ``skipif`` condition at decoration time, so such a condition arrives already
    reduced to a bool.  The worker adds no evaluation of its own.)
    """
    specs = _pytestmark_specs(obj, consider_mro=consider_mro)
    specs.extend(_rustest_mark_specs(obj, consider_mro=consider_mro))
    skip_reason = _safe_getattr(obj, "__rustest_skip__", None)
    if skip_reason is not None:
        specs.append({"name": "skip", "kwargs": {"reason": _json_safe(skip_reason)}})
    return specs


def _parametrization(func: object) -> list[tuple[str, Mapping[str, object]]] | None:
    """Read v1's parametrize metadata and return ``(param_id, values)`` per case.

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
    return list(zip(_unique_parameterset_ids(raw_ids), valuesets, strict=True))


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

    Limitation: pytest's ``num_mock_patch_args`` adjustment for ``mock.patch``-wrapped
    tests is not ported.
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
    return names


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

    This is :func:`_requested_argnames` minus the names the *parametrization* supplies:
    those come from the decorator, not from a fixture, so reporting them as requested
    fixtures would be a lie on the wire.  Limitation: ``indirect=True`` parameters are
    fixtures in pytest but are excluded here as ordinary parametrized names.
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
#: ``package`` and ``session`` collapse onto one worker-lifetime bucket.  **Documented
#: limitation, not an oversight:** a worker is handed an arbitrary subset of the run's
#: files (``src/v2/collect.rs`` routes by stem hash), so it cannot know when the last
#: test of a package — or of the session — has run anywhere else.  A session fixture
#: therefore executes once *per worker* rather than once per run, and a package fixture
#: is not torn down at the package boundary.  No corpus case exercises either
#: (`fixtures/*` are all function/module scope); Phase 1c owns the corpus additions and
#: the cross-worker session protocol.
_SCOPE_BUCKET: Final[Mapping[str, str]] = {
    "function": "function",
    "class": "class",
    "module": "module",
    "package": "session",
    "session": "session",
}

#: Teardown buckets, narrowest first — the order :meth:`FixtureRunner.teardown` unwinds.
_BUCKET_ORDER: Final = ("function", "class", "module", "session")

#: Builtin fixtures this worker provides, taken from v1 (``builtin_fixtures.py``).
#: ``tmp_path_factory`` is here because ``tmp_path`` requests it.
BUILTIN_FIXTURES: Final = ("tmp_path_factory", "tmp_path", "monkeypatch", "capsys")

#: Fixtures real pytest provides that this worker does not yet.  Requesting one is an
#: error with its own wording rather than pytest's ``not found``: "not found" would send
#: an operator hunting for a missing ``@fixture`` that was never theirs to write.
UNSUPPORTED_BUILTIN_FIXTURES: Final = frozenset(
    {
        "cache",
        "capfd",
        "capfdbinary",
        "caplog",
        "capsysbinary",
        "capteesys",
        "doctest_namespace",
        "mocker",
        "pytestconfig",
        "pytester",
        "record_property",
        "record_testsuite_property",
        "record_xml_attribute",
        "recwarn",
        "testdir",
        "tmpdir",
        "tmpdir_factory",
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
    params: tuple[tuple[str, object], ...] | None
    autouse: bool
    baseid: str
    argnames: tuple[str, ...]
    #: True for a plain ``def`` in a test-class body, whose first parameter is ``self``.
    #: :meth:`FixtureRunner._call` binds it to the test's instance, as pytest's
    #: ``resolve_fixture_function`` does.  False for module-level fixtures and for
    #: ``staticmethod``/``classmethod`` ones, which need no instance.
    needs_instance: bool = False


def _fixture_param_cases(func: object, fixture_name: str) -> tuple[tuple[str, object], ...] | None:
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
    for case in cast(Sequence[object], raw):
        if not isinstance(case, dict):
            raise CollectionRefusal(
                f"malformed rustest fixture params on {fixture_name!r}: "
                + f"expected a case dict, got {case!r}"
            )
        entry = cast(Mapping[str, object], case)
        ids.append(str(entry.get("id", "")))
        values.append(entry.get("value"))
    if not ids:
        return None
    return tuple(zip(_unique_parameterset_ids(ids), values, strict=True))


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


def build_closure(
    registry: FixtureRegistry,
    argnames: Sequence[str],
    ignore_args: AbstractSet[str] = frozenset(),
) -> FixtureClosure:
    """Port of `_pytest/fixtures.py::FixtureManager.getfixtureclosure` (l. 1624-1664).

    The initial set is ``deduplicate_names(autousenames, usefixturesnames, argnames)``
    (`getfixtureinfo` l. 1568-1570) — **autouse first**, which is why an autouse
    parametrized fixture contributes the *leftmost* id component even for a test that
    never mentions it (probed: ``test_auto_only[1]``/``[2]``).  ``usefixtures`` is not
    read: the mark travels as data on the wire but nothing evaluates marks until Task 3,
    so honouring it here would be half a feature; recorded as a Task 3 obligation.

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
    closure = list(dict.fromkeys([*registry.autouse_names, *argnames]))
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
) -> list[tuple[str, tuple[tuple[str, object], ...]]]:
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
    dimensions: list[tuple[str, tuple[tuple[str, object], ...]]] = []
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
    marks: tuple[MarkSpecDict, ...]
    unittest_case: type | None = None
    unittest_method: str | None = None


class _SubRequest:
    """The ``request`` object a fixture (or a test) receives.

    pytest special-cases ``request`` in `_get_active_fixturedef` (l. 566-570): it is never
    a registered fixture, it is a ``PseudoFixtureDef`` synthesised per requester.  Same
    here — which is why ``request`` appearing in a closure never raises "not found".

    Deliberately smaller than pytest's ``SubRequest``: ``param``, ``scope``,
    ``fixturename``, ``addfinalizer`` and ``getfixturevalue`` are what fixtures actually
    use; ``node``/``config``/``instance``/``module`` need collection-tree objects the
    worker does not build.  ``param`` is *absent* (not ``None``) on an unparametrized
    fixture, so ``request.param`` raises ``AttributeError`` exactly as under pytest.
    """

    def __init__(
        self,
        runner: FixtureRunner,
        closure: FixtureClosure,
        params: Mapping[str, object],
        fixturename: str | None,
        scope: str,
        chain: tuple[str, ...],
    ) -> None:
        super().__init__()
        self._runner = runner
        self._closure = closure
        self._params = params
        self._chain = chain
        self.fixturename: Final = fixturename
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
        """Port of `FixtureRequest.getfixturevalue` (l. 527-...): resolve a name on demand.

        Names outside the static closure are looked up in the registry, which is what
        pytest's ``getfixturedefs(argname, self._pyfuncitem)`` fallback does.
        """
        return self._runner.resolve(argname, self._closure, self._params, self._chain)


@dataclass
class _Cached:
    value: object
    param_key: object
    finalizer: _Finalizer


@dataclass
class _Finalizer:
    """One fixture's teardown: its post-yield half plus anything ``addfinalizer`` added.

    Mirrors ``FixtureDef._finalizers``, which `call_fixture_func` appends the yield teardown
    to *after* the body has run — so draining LIFO means yield teardown first, then the
    body's own finalizers in reverse registration order.
    """

    fixturedef: FixtureDef | None
    call: Callable[[], object] | None
    extras: list[Callable[[], object]] = field(default_factory=list)
    #: Set the moment :meth:`FixtureRunner._finish` starts, so a finalizer already run by a
    #: parametrization change is skipped when its bucket is drained — never run twice.
    done: bool = False


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

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[FixtureDef, _Cached] = {}
        self._finalizers: dict[str, list[_Finalizer]] = {name: [] for name in _BUCKET_ORDER}
        #: One entry per fixture body currently executing; ``addfinalizer`` targets the top.
        self._extras_stack: list[list[Callable[[], object]]] = []
        #: The test-class instance the current test (and its class fixtures) is bound to.
        #: pytest builds one per ``Function`` and both the test method and any class-body
        #: fixture see the same object (`resolve_fixture_function` l. 1142-1164, which
        #: rebinds the fixture to ``request.instance``).  ``None`` for a module-level test.
        self.instance: object | None = None

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
        """
        self.instance = (
            plan.owner() if plan.owner is not None and plan.unittest_case is None else None
        )
        values: dict[str, object] = dict(plan.direct_params)
        for name in plan.closure.names:
            if name in values:
                continue
            values[name] = self.resolve(name, plan.closure, plan.fixture_params, ())
        return {name: values[name] for name in plan.argnames if name in values}

    def resolve(
        self,
        name: str,
        closure: FixtureClosure,
        params: Mapping[str, object],
        chain: tuple[str, ...],
    ) -> object:
        """Return one fixture's value, executing and caching it if needed.

        Port of `_get_active_fixturedef` (l. 566-641) + `FixtureDef.execute` (l. 1074-1140).
        ``chain`` is the fixture names currently being resolved, which is pytest's
        ``_iter_chain`` walk: an overriding fixture that requests its own name gets the
        *next* definition up (l. 596-608, ``index = -1``, decremented once per matching
        request), and exhausting the levels is an error rather than infinite recursion.
        """
        if name == "request":
            requester = chain[-1] if chain else None
            scope = "function"
            if requester is not None:
                defs = closure.arg2defs.get(requester)
                if defs:
                    scope = defs[-1].scope
            return _SubRequest(self, closure, params, requester, _SCOPE_BUCKET[scope], chain)

        defs = closure.arg2defs.get(name) or closure.registry.getfixturedefs(name)
        if not defs:
            raise FixtureLookupError(_fixture_not_found_message(name, closure.registry))

        index = -1 - sum(1 for entry in chain if entry == name)
        if -index > len(defs):
            raise FixtureLookupError(
                f" recursive dependency involving fixture '{name}' detected"
                + _available_fixtures_suffix(closure.registry)
            )
        fixturedef = defs[index]

        param_key = params.get(name, _NO_PARAM)
        cached = self._cache.get(fixturedef)
        if cached is not None:
            if cached.param_key is param_key or cached.param_key == param_key:
                return cached.value
            # "We have a previous but differently parametrized fixture instance so we
            # need to tear it down before creating a new one." (FixtureDef.execute
            # l. 1119-1122).  It is the *registered* finalizer that runs -- running a
            # freshly made empty one would drop the cache and silently skip the
            # post-yield teardown.  Probed: pytest emits setup:a, teardown:a, setup:b,
            # teardown:b for a module-scoped params=["a","b"] fixture.
            self._finish(cached.finalizer)

        bucket = _SCOPE_BUCKET[fixturedef.scope]
        sub_chain = (*chain, name)
        kwargs: dict[str, object] = {}
        for argname in fixturedef.argnames:
            if argname == "request":
                kwargs[argname] = _SubRequest(self, closure, params, name, bucket, sub_chain)
                continue
            self._check_scope(fixturedef, argname, closure)
            kwargs[argname] = self.resolve(argname, closure, params, sub_chain)

        value, finalizer = self._call(fixturedef, kwargs, bucket)
        self._cache[fixturedef] = _Cached(value, param_key, finalizer)
        return value

    def _check_scope(self, fixturedef: FixtureDef, argname: str, closure: FixtureClosure) -> None:
        """Port of `FixtureRequest._check_scope` — a wider fixture may not request a narrower one."""
        defs = closure.arg2defs.get(argname)
        if not defs:
            return
        requested = defs[-1]
        if _SCOPE_INDEX[requested.scope] < _SCOPE_INDEX[fixturedef.scope]:
            raise ScopeMismatch(
                f"You tried to access the {requested.scope} scoped fixture "
                + f"{argname!r} with a {fixturedef.scope} scoped request object, "
                + "involved factories: "
                + f"{fixturedef.name!r}"
            )

    def _call(
        self, fixturedef: FixtureDef, kwargs: Mapping[str, object], bucket: str
    ) -> tuple[object, _Finalizer]:
        """Port of `call_fixture_func` (l. 916-932) — run to the yield, defer the rest.

        The finalizer is registered *after* the body runs, so a finalizer the body added
        through ``request.addfinalizer`` sits below it on the stack and therefore runs
        after it, matching pytest's single per-fixture LIFO list.  A finalizer is appended
        even for a non-yield fixture: `FixtureDef.execute` schedules ``finish`` in a
        ``finally`` regardless (l. 1132-1136), and that is what drops the cached value at
        the end of the scope.  It is returned as well as stacked so the cache can run it
        early when a parametrization change invalidates the entry.

        A fixture defined in a test-class body is bound to the same instance the test
        method gets — `resolve_fixture_function` l. 1152-1163
        (``fixturefunc.__get__(instance)``).  Without it the fixture would be called with
        ``self`` missing.
        """
        func = fixturedef.func
        if fixturedef.needs_instance and self.instance is not None:
            func = cast(Callable[..., object], cast(Any, func).__get__(self.instance))
        extras: list[Callable[[], object]] = []
        self._extras_stack.append(extras)
        try:
            if inspect.isgeneratorfunction(func):
                generator = cast(Any, func)(**kwargs)
                try:
                    value = cast(object, next(generator))
                except StopIteration:
                    raise FixtureLookupError(f"{fixturedef.name} did not yield a value") from None
                teardown: Callable[[], object] | None = functools.partial(
                    _teardown_yield, fixturedef, generator
                )
            else:
                value = func(**kwargs)
                teardown = None
        finally:
            _ = self._extras_stack.pop()
        finalizer = _Finalizer(fixturedef, teardown, extras)
        self._finalizers[bucket].append(finalizer)
        return value, finalizer

    def add_finalizer(self, finalizer: Callable[[], object]) -> None:
        """Attach a ``request.addfinalizer`` callable to the fixture being set up.

        Outside any fixture body — a ``request`` the test itself holds — it goes on the
        function bucket, which is where pytest's ``TopRequest.addfinalizer`` puts it
        (``self._pyfuncitem.addfinalizer``).
        """
        if self._extras_stack:
            self._extras_stack[-1].append(finalizer)
        else:
            self._finalizers["function"].append(_Finalizer(None, finalizer))

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
        """Unwind every scope — the worker is shutting down."""
        self.teardown("session")

    def _finish(self, finalizer: _Finalizer) -> None:
        """Drain one fixture's teardown and drop its cached value, even if it raised.

        Port of `FixtureDef.finish` (l. 1053-1072): LIFO over the fixture's finalizer list —
        the post-yield half first (appended last by ``call_fixture_func``), then whatever the
        body registered, in reverse — collecting exceptions rather than stopping, and
        invalidating the cache regardless ("Even if finalization fails, we invalidate the
        cached fixture value").
        """
        if finalizer.done:
            return
        finalizer.done = True
        calls: list[Callable[[], object]] = []
        if finalizer.call is not None:
            calls.append(finalizer.call)
        calls.extend(reversed(finalizer.extras))
        exceptions: list[BaseException] = []
        for call in calls:
            try:
                _ = call()
            except BaseException as exc:  # noqa: BLE001 - re-raised below, never dropped
                exceptions.append(exc)
        if finalizer.fixturedef is not None:
            _ = self._cache.pop(finalizer.fixturedef, None)
        if len(exceptions) == 1:
            raise exceptions[0]
        if exceptions:
            name = finalizer.fixturedef.name if finalizer.fixturedef else "request"
            raise BaseExceptionGroup(
                f"errors while tearing down fixture {name!r}", exceptions[::-1]
            )


def _teardown_yield(fixturedef: FixtureDef, generator: object) -> None:
    """Port of `_pytest/fixtures.py::_teardown_yield_fixture` (l. 934-949).

    A second yield is an error, not a silent stop: the code after it would never run.
    """
    try:
        _ = next(cast(Any, generator))
    except StopIteration:
        return
    raise FixtureLookupError(f"fixture function has more than one 'yield': {fixturedef.name}")


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
    """Register the v1 builtins this worker supports, at total visibility.

    Reused verbatim from ``python/rustest/builtin_fixtures.py`` — wrapped, never forked:
    ``tmp_path_factory``/``tmp_path`` (l. 283-296, the ``TmpPathFactory`` yield pair),
    ``monkeypatch`` (l. 312-318, ``MonkeyPatch`` with ``undo()`` in a ``finally``) and
    ``capsys`` (l. 421-441, ``CaptureFixture`` swapping ``sys.stdout``/``sys.stderr``).
    Each already carries ``__rustest_fixture__`` metadata, so the ordinary
    :meth:`FixtureRegistry.parse_factories` path reads them; the module is imported lazily
    so a unit test importing this worker does not pay for it.

    ``baseid=""`` is pytest's "always matches" marker for a plugin-provided fixture
    (`FixtureDef.__init__`: "For other plugins, the baseid is the empty string").
    Registered first, so any user fixture of the same name shadows it — which is what
    pytest's furthest-to-nearest ordering does for plugin fixtures too.

    Note ``capsys`` here is v1's stream-swapping implementation, not pytest's fd-level
    capture; the worker has already rebound ``sys.stdout`` to stderr, and the swap is
    save/restore, so the two compose.  True fd capture is Phase 1c.
    """
    from rustest import builtin_fixtures

    for name in BUILTIN_FIXTURES:
        func = getattr(builtin_fixtures, name)
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


def build_registry(module: types.ModuleType, path: Path, rootdir: Path) -> FixtureRegistry:
    """Assemble the fixture registry for one test file: builtins, conftests, module.

    Registration order *is* the shadowing rule: builtins, then each conftest from rootdir
    down to the file's own directory, then the module itself.  ``defs[-1]`` is therefore
    the nearest definition, which is what corpus ``fixtures/override-nearest`` asserts (a
    module ``value`` beating the conftest's).

    The conftest chain is imported here, during collection, because the closure it feeds
    is what expands parametrized fixtures into separate nodeids — the registry cannot wait
    for execution.
    """
    registry = FixtureRegistry()
    _register_builtin_fixtures(registry)
    for conftest in conftest_chain(path, rootdir):
        conftest_module = import_conftest(conftest, rootdir)
        registry.parse_factories(conftest_module, _conftest_baseid(conftest, rootdir))
    registry.parse_factories(module, _relative_posix(path, rootdir))
    return registry


def _build_entry(
    rel_path: str,
    parts: tuple[str, ...],
    param_id: str | None,
    marks: list[MarkSpecDict],
    fixtures: list[str],
) -> CollectedTestDict:
    """Assemble one ``CollectedTest``, omitting every empty optional field.

    ``class_name`` carries the whole class chain (``TestBox.TestInner``), which is
    identical to the innermost class name for every non-nested case; ``qualname``
    already carries the same chain plus the function name.
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
        entry["marks"] = marks
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


def _collect_function(
    obj: object,
    name: str,
    parts: tuple[str, ...],
    owner: type | None,
    outer_marks: list[MarkSpecDict],
    registry: FixtureRegistry,
    context: _CollectContext,
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
    full_parts = (*parts, name)
    cases = _parametrization(func)
    direct_cases: list[tuple[str | None, Mapping[str, object]]] = (
        [(None, {})] if cases is None else [(case_id, values) for case_id, values in cases]
    )
    direct_argnames = frozenset(name for _case_id, values in direct_cases for name in values)

    argnames = _requested_argnames(func, name, owner)
    closure = build_closure(registry, argnames, ignore_args=direct_argnames)
    dimensions = fixture_param_dimensions(closure, direct_argnames)

    entries: list[CollectedTestDict] = []
    for combination in itertools.product(*(values for _name, values in dimensions)):
        fixture_ids = [param_id for param_id, _value in combination]
        fixture_params = {
            dimension[0]: value for dimension, (_id, value) in zip(dimensions, combination)
        }
        for case_id, case_values in direct_cases:
            id_parts = [*fixture_ids, *([] if case_id is None else [case_id])]
            param_id = "-".join(id_parts) if id_parts else None
            entry = _build_entry(
                context.rel_path,
                full_parts,
                param_id,
                marks,
                _fixture_names(func, name, owner, frozenset(case_values)),
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
                    fixture_params=fixture_params,
                    direct_params=case_values,
                    argnames=tuple(argnames),
                    marks=tuple(marks),
                )
            )
    return entries


def _collect_unittest_class(
    cls: type,
    name: str,
    parts: tuple[str, ...],
    outer_marks: list[MarkSpecDict],
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
    """
    if _safe_getattr(cls, "__test__", True) is False:
        return []

    class_marks = _mark_specs(cls, consider_mro=True) + outer_marks
    child_parts = (*parts, name)
    loader = unittest.TestLoader()
    entries: list[CollectedTestDict] = []
    method_names = list(loader.getTestCaseNames(cast(type[unittest.TestCase], cls)))
    if not method_names and getattr(cls, "runTest", None) is not None:
        method_names = ["runTest"]
    for method_name in method_names:
        method = _safe_getattr(cls, method_name, None)
        if method is not None and _safe_getattr(method, "__test__", True) is False:
            continue
        marks = (_mark_specs(_unwrap(method)) if method is not None else []) + class_marks
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
                closure=build_closure(registry, ()),
                fixture_params={},
                direct_params={},
                argnames=(),
                marks=tuple(marks),
                unittest_case=cls,
                unittest_method=method_name,
            )
        )
    return entries


def _collect_class(
    cls: type,
    name: str,
    parts: tuple[str, ...],
    outer_marks: list[MarkSpecDict],
    registry: FixtureRegistry,
    context: _CollectContext,
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
    class_registry.parse_factories(cls, f"{context.rel_path}::{'::'.join(child_parts)}", cls)
    entries: list[CollectedTestDict] = []
    for member_name, member in _mro_ordered_members(cls):
        entries.extend(
            _make_items(member, member_name, child_parts, cls, class_marks, class_registry, context)
        )
    return entries


def _make_items(
    obj: object,
    name: str,
    parts: tuple[str, ...],
    owner: type | None,
    outer_marks: list[MarkSpecDict],
    registry: FixtureRegistry,
    context: _CollectContext,
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
            return _collect_class(obj, name, parts, outer_marks, registry, context)
        return []
    if _is_test_function(obj, name, context.naming):
        return _collect_function(obj, name, parts, owner, outer_marks, registry, context)
    return []


def collect_module(
    module: types.ModuleType,
    path: Path,
    rootdir: Path,
    naming: Naming,
    registry: FixtureRegistry | None = None,
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

    **Known ordering limitation.**  pytest reorders collected items after the fact so that
    tests sharing a higher-scoped parametrized fixture run consecutively
    (`_pytest/fixtures.py::reorder_items`, invoked from ``pytest_collection_modifyitems``).
    That pass is a no-op for ``function`` scope (``reorder_items_atscope`` returns early on
    ``scope is Scope.Function``) and operates on the whole session's item list, which a
    per-file worker does not have.  So a **module- or wider-scoped parametrized fixture**
    yields pytest-correct ids in a non-pytest order; no corpus case has one, and closing it
    needs the reorder to live in the orchestrator's manifest assembly.
    """
    if _safe_getattr(module, "__test__", True) is False:
        return [], []

    if registry is None:
        registry = FixtureRegistry()
        _register_builtin_fixtures(registry)
        registry.parse_factories(module, _relative_posix(path, rootdir))

    context = _CollectContext(
        module=module,
        path=path,
        rel_path=_relative_posix(path, rootdir),
        naming=naming,
        plans=[],
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
# protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerState:
    """What ``init`` established, needed by every later ``collect_file``."""

    rootdir: Path
    naming: Naming


_state: WorkerState | None = None

#: Execution plans for every test this worker has collected, keyed by manifest id.
#: Worker-local by design: the orchestrator routes an ``execute_test`` back to the worker
#: that collected the file (``src/v2/collect.rs`` stem-hash routing), so the plan — and the
#: warm module object it points at — is always here.  An id that is not is a routing bug,
#: and :func:`execution_plan` says so loudly rather than returning ``None``.
_execution_plans: dict[str, ExecutionPlan] = {}


def execution_plan(test_id: str) -> ExecutionPlan:
    """The execution plan for *test_id*, or ``KeyError`` naming the routing failure."""
    try:
        return _execution_plans[test_id]
    except KeyError:
        raise KeyError(
            f"no collected test with id {test_id!r} in this worker "
            + "(the orchestrator must execute a test in the worker that collected it)"
        ) from None


def _pattern_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in cast(Sequence[object], value))
    return default


def handle_init(message: Mapping[str, object]) -> ReadyResponse:
    """Handle ``init``; reply ``ready``.

    The reply states :data:`PROTOCOL_VERSION` — the version this worker *speaks*.
    It is never an echo of ``message["protocol_version"]``: an echo would make the
    handshake agree with any orchestrator and detect no skew at all.  Deciding what
    to do about a mismatch is the orchestrator's job (``src/v2/protocol.rs``).

    v2's ``invocation_dir`` is accepted and **not yet stored**: nothing in the collection
    half is invocation-relative (paths arrive absolute, nodeids are rootdir-relative), and
    :class:`WorkerState` should gain the field in the task that first needs it rather than
    carry an unread value that could silently go stale.
    """
    global _state
    _state = WorkerState(
        rootdir=Path(str(message["rootdir"])),
        naming=Naming(
            python_files=_pattern_tuple(message.get("python_files"), DEFAULT_PYTHON_FILES),
            python_classes=_pattern_tuple(message.get("python_classes"), DEFAULT_PYTHON_CLASSES),
            python_functions=_pattern_tuple(
                message.get("python_functions"), DEFAULT_PYTHON_FUNCTIONS
            ),
        ),
    )
    return {"op": "ready", "protocol_version": PROTOCOL_VERSION}


def handle_shutdown() -> ByeResponse:
    """Handle ``shutdown``; reply ``bye`` — the last line the worker writes."""
    return {"op": "bye"}


def collect_file(path: str) -> CollectedResponse:
    """Collect one file and build its ``collected`` response.

    ``path`` is echoed verbatim (absolute posix, as sent); the nested entries carry
    rootdir-relative paths per the manifest contract.  Exactly one of ``tests`` /
    ``error`` is present — and neither appears as an empty value: a file that
    legitimately collects nothing is ``{"op":"collected","path":...}`` alone.

    Any import-time exception becomes an error entry rather than killing the worker,
    since one unimportable file must not lose the whole run.  ``BaseException`` is
    deliberately *not* caught: a ``SystemExit`` or ``KeyboardInterrupt`` raised at
    import time should end the process, exactly as it would under pytest.
    """
    if _state is None:
        raise NotInitializedError("collect_file received before init")
    state = _state

    file_path = Path(path)
    response: CollectedResponse = {"op": "collected", "path": path}
    try:
        # Conftests first, exactly as pytest loads them before any test module: the
        # registry they build is what expands parametrized fixtures into separate ids,
        # and a test module's own ``import conftest`` must find the chain already in
        # ``sys.modules`` (issue #130 / corpus fixtures/autouse).
        registry = FixtureRegistry()
        _register_builtin_fixtures(registry)
        for conftest in conftest_chain(file_path, state.rootdir):
            conftest_module = import_conftest(conftest, state.rootdir)
            registry.parse_factories(conftest_module, _conftest_baseid(conftest, state.rootdir))
        module = import_test_module(file_path, state.rootdir)
        registry.parse_factories(module, _relative_posix(file_path, state.rootdir))
        tests, plans = collect_module(module, file_path, state.rootdir, state.naming, registry)
        for plan in plans:
            _execution_plans[plan.id] = plan
    except CollectionRefusal as exc:
        response["error"] = {
            "path": _relative_posix(file_path, state.rootdir),
            "message": str(exc),
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


def main() -> int:
    """Run the protocol loop: read a request per line, write a response per line.

    stdout is reserved for protocol traffic, so ``sys.stdout`` is rebound to stderr
    **before any test module is imported** — a module that prints at import time then
    writes to stderr instead of corrupting the stream.  (A module writing straight to
    ``sys.__stdout__`` still would; nothing short of an fd-level dup can stop that,
    and that is Task 3's option if it ever matters.)

    Every protocol violation exits **2** on the same path: an unparseable line, an
    unknown ``op``, a ``collect_file`` with no ``path``, and a ``collect_file`` before
    ``init``.  The protocol is internal, so drift means a bug and must be loud
    (``src/v2/protocol.rs`` module docs) — and it must be *distinguishable*, which an
    uncaught traceback (exit 1, no framing) is not.  A file that merely fails to
    import is NOT drift: it is data, and it comes back as a ``collected`` error entry.
    """
    install_pytest_shim()

    protocol_out = sys.stdout
    _reconfigure(protocol_out)
    _reconfigure(sys.stdin)
    sys.stdout = sys.stderr

    def emit(response: Mapping[str, object]) -> None:
        _ = protocol_out.write(encode_response(response) + "\n")
        protocol_out.flush()

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
            try:
                response = collect_file(path)
            except NotInitializedError as exc:
                print(f"rustest v2 worker: {exc}", file=sys.stderr)
                return 2
            emit(response)
        elif op == "shutdown":
            emit(handle_shutdown())
            return 0
        else:
            print(f"rustest v2 worker: unknown op {op!r} in line: {line!r}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main())
