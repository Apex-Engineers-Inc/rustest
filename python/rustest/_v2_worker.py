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

**Known scope limits of protocol v1** (each deliberate, none silent):

* *No warning channel.*  pytest reports a class with ``__init__``/``__new__`` via
  ``PytestCollectionWarning`` and still exits 0.  The protocol has no warnings field,
  so such a class is skipped and **no error entry is emitted** — emitting one would
  turn pytest's exit 0 into the orchestrator's exit 2 and diverge from the oracle.
* *Marks are carried, never evaluated.*  ``skipif``/``xfail`` conditions travel as
  data; evaluation is Phase 1b.2.
* *No ``pytest_generate_tests`` hook, no fixture-level parametrization, no indirect
  expansion.*  Parametrization comes exclusively from the decorator metadata.  This
  is the one corpus case a worker-vs-pytest id differential still misses:
  ``fixtures/parametrized-fixture`` (``@pytest.fixture(params=[1, 2])``) collects as
  ``test_number[1]``/``test_number[2]`` under pytest and as a single ``test_number``
  here.  Expanding it needs the fixture *closure* — which spans conftest files the
  worker is never told about — so it needs a protocol change, not a local fix.
* *A file is enumerated exactly as handed over.*  pytest never collects ``conftest.py``
  as a test module (it matches no ``python_files`` pattern and is loaded as a plugin),
  so the orchestrator should not send one as a collect target; doing so anyway is
  harmless — a conventional conftest yields no tests — and imports it under its real
  name.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import fnmatch
import importlib
import inspect
import io
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
    "DEFAULT_NAMING",
    "PROTOCOL_VERSION",
    "CollectionRefusal",
    "Naming",
    "build_nodeid",
    "collect_file",
    "encode_response",
    "enumerate_module",
    "handle_init",
    "handle_shutdown",
    "import_test_module",
    "install_pytest_shim",
    "main",
    "matches_name_pattern",
    "resolve_module_identity",
]

#: The protocol this worker **speaks**.  Mirrors ``PROTOCOL_VERSION`` in
#: ``src/v2/protocol.rs``; the two must move together.
PROTOCOL_VERSION: Final = 1


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
    to ``/``.  A path outside rootdir keeps its absolute (posix) form rather than
    growing ``..`` segments, mirroring pytest's ``bestrelpath`` fallback.
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


def _mark_specs(obj: object) -> list[MarkSpecDict]:
    """Read rustest's mark metadata into ``MarkSpec`` dicts.

    Sources consumed (both set by ``python/rustest/decorators.py``, and reached from
    ``pytest.mark.*`` through ``python/rustest/compat/pytest.py``):

    * ``__rustest_marks__`` — list of ``{"name", "args", "kwargs"}``
      (``decorators.py::MarkDecorator.__call__``);
    * ``__rustest_skip__`` — the reason string set by ``decorators.py::skip_decorator``,
      which is what the *compat* ``pytest.mark.skip`` uses instead of a mark entry.

    Conditions are **not** evaluated here.  (Note a v1-ism inherited through the
    shim: ``decorators.py::MarkDecorator._normalize_args`` evaluates a *string*
    ``skipif`` condition at decoration time, so such a condition arrives already
    reduced to a bool.  The worker adds no evaluation of its own.)
    """
    specs: list[MarkSpecDict] = []
    raw_marks = _safe_getattr(obj, "__rustest_marks__", None)
    if isinstance(raw_marks, list):
        for raw in cast(list[object], raw_marks):
            if not isinstance(raw, dict):
                continue
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
            specs.append(spec)

    skip_reason = _safe_getattr(obj, "__rustest_skip__", None)
    if skip_reason is not None:
        specs.append({"name": "skip", "kwargs": {"reason": _json_safe(skip_reason)}})
    return specs


def _parametrization(func: object) -> list[tuple[str, frozenset[str]]] | None:
    """Read v1's parametrize metadata and return ``(param_id, argnames)`` per case.

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
    if not isinstance(cases, (list, tuple)) or not cases:
        return None

    raw_ids: list[str] = []
    argnames: list[frozenset[str]] = []
    for case in cast(Sequence[object], cases):
        if not isinstance(case, dict):
            return None
        entry = cast(Mapping[str, object], case)
        raw_ids.append(str(entry.get("id", "")))
        values = entry.get("values", {})
        names = (
            frozenset(str(name) for name in cast(Mapping[object, object], values))
            if isinstance(values, dict)
            else frozenset[str]()
        )
        argnames.append(names)
    return list(zip(_unique_parameterset_ids(raw_ids), argnames, strict=True))


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


def _fixture_names(func: object, in_class: bool, param_names: frozenset[str]) -> list[str]:
    """Direct fixture parameters, in signature order (the manifest's ``fixtures``).

    Parametrized argnames are supplied by the parametrization, not by a fixture, so
    they are excluded; ``self``/``cls`` is dropped for methods.  Limitation:
    ``indirect=True`` parameters are fixtures in pytest but are excluded here as
    ordinary parametrized names — indirect resolution is 1b.2 territory.
    """
    try:
        signature = inspect.signature(cast(Any, func))
    except (TypeError, ValueError):  # pragma: no cover - builtins never reach here
        return []
    accepted = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    names = [
        parameter.name for parameter in signature.parameters.values() if parameter.kind in accepted
    ]
    if in_class and names and names[0] in ("self", "cls"):
        names = names[1:]
    return [name for name in names if name not in param_names]


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


def _collect_function(
    obj: object,
    name: str,
    rel_path: str,
    parts: tuple[str, ...],
    outer_marks: list[MarkSpecDict],
) -> list[CollectedTestDict]:
    """Port of `_pytest/python.py::pytest_pycollect_makeitem`'s function branch.

    A matching name that is not a function warns and collects nothing; ``__test__ =
    False`` hides it; a generator function is a hard ``fail()`` in pytest, i.e. a
    collection error for the whole module.

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
    in_class = bool(parts)
    cases = _parametrization(func)
    if cases is None:
        fixtures = _fixture_names(func, in_class, frozenset())
        return [_build_entry(rel_path, full_parts, None, marks, fixtures)]
    return [
        _build_entry(
            rel_path,
            full_parts,
            param_id,
            marks,
            _fixture_names(func, in_class, param_names),
        )
        for param_id, param_names in cases
    ]


def _collect_unittest_class(
    cls: type,
    name: str,
    rel_path: str,
    parts: tuple[str, ...],
    outer_marks: list[MarkSpecDict],
) -> list[CollectedTestDict]:
    """Port of `_pytest/unittest.py::UnitTestCase.collect` — the discovery half only.

    Names come from ``unittest.TestLoader().getTestCaseNames(cls)``, which applies
    ``testMethodPrefix`` and **sorts** them (``sortTestMethodsUsing``); probed, and
    ``test_zeta``/``test_alpha`` really do come back alphabetically, unlike a plain
    class.  Nothing is instantiated and nothing is run.

    ``UnitTestCase.nofuncargs = True``, so these items take no fixtures — the
    ``fixtures`` field is left empty rather than filled from the signature.
    """
    if _safe_getattr(cls, "__test__", True) is False:
        return []

    class_marks = _mark_specs(cls) + outer_marks
    child_parts = (*parts, name)
    loader = unittest.TestLoader()
    entries: list[CollectedTestDict] = []
    for method_name in loader.getTestCaseNames(cast(type[unittest.TestCase], cls)):
        method = _safe_getattr(cls, method_name, None)
        if _safe_getattr(method, "__test__", True) is False:
            continue
        entries.append(
            _build_entry(
                rel_path,
                (*child_parts, method_name),
                None,
                _mark_specs(_unwrap(method)) + class_marks,
                [],
            )
        )

    if not entries and getattr(cls, "runTest", None) is not None:
        entries.append(_build_entry(rel_path, (*child_parts, "runTest"), None, class_marks, []))
    return entries


def _collect_class(
    cls: type,
    name: str,
    rel_path: str,
    parts: tuple[str, ...],
    naming: Naming,
    outer_marks: list[MarkSpecDict],
) -> list[CollectedTestDict]:
    """Port of `_pytest/python.py::Class.collect`.

    A class with an ``__init__`` or a ``__new__`` is refused — pytest emits
    ``PytestCollectionWarning("cannot collect test class ... because it has a
    __init__ constructor")`` and returns ``[]``, **without** failing the run.  The
    protocol has no warning channel in v1, so the class is skipped silently; emitting
    a collection error instead would turn pytest's exit 0 into exit 2 (corpus
    ``collection/class-collection``).
    """
    if _safe_getattr(cls, "__test__", True) is False:
        return []
    if _hasinit(cls) or _hasnew(cls):
        return []

    class_marks = _mark_specs(cls) + outer_marks
    child_parts = (*parts, name)
    entries: list[CollectedTestDict] = []
    for member_name, member in _mro_ordered_members(cls):
        entries.extend(_make_items(member, member_name, rel_path, child_parts, naming, class_marks))
    return entries


def _make_items(
    obj: object,
    name: str,
    rel_path: str,
    parts: tuple[str, ...],
    naming: Naming,
    outer_marks: list[MarkSpecDict],
) -> list[CollectedTestDict]:
    """Dispatch one namespace entry, mirroring pytest's ``pytest_pycollect_makeitem`` hooks.

    The ``unittest`` plugin's implementation runs **first** (pluggy calls the most
    recently registered plugin first, and it is ``firstresult``), which is why a
    ``TestCase`` subclass is handled before the name filters in
    `_pytest/python.py::pytest_pycollect_makeitem` ever apply.
    """
    if _is_unittest_case(obj):
        return _collect_unittest_class(cast(type, obj), name, rel_path, parts, outer_marks)
    if inspect.isclass(obj):
        if _is_test_class(obj, name, naming):
            return _collect_class(obj, name, rel_path, parts, naming, outer_marks)
        return []
    if _is_test_function(obj, name, naming):
        return _collect_function(obj, name, rel_path, parts, outer_marks)
    return []


def enumerate_module(
    module: types.ModuleType,
    path: Path,
    rootdir: Path,
    naming: Naming,
) -> list[CollectedTestDict]:
    """Enumerate *module* into ``CollectedTest`` dicts, in pytest's collection order.

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
    """
    if _safe_getattr(module, "__test__", True) is False:
        return []

    rel_path = _relative_posix(path, rootdir)
    entries: list[CollectedTestDict] = []
    for name, obj in list(vars(module).items()):
        if name in IGNORED_ATTRIBUTES:
            continue
        entries.extend(_make_items(obj, name, rel_path, (), naming, []))
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
        raise RuntimeError("collect_file called before init")
    state = _state

    file_path = Path(path)
    response: CollectedResponse = {"op": "collected", "path": path}
    try:
        module = import_test_module(file_path, state.rootdir)
        tests = enumerate_module(module, file_path, state.rootdir, state.naming)
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

    Process-global by nature, so it is called from :func:`main` only, never from the
    collection helpers (a unit test importing this module must not have its own
    ``pytest`` swapped out from under it).
    """
    from rustest.compat import pytest as compat_pytest
    from rustest.compat import pytest_asyncio as compat_pytest_asyncio

    sys.modules["pytest"] = compat_pytest
    sys.modules["pytest_asyncio"] = compat_pytest_asyncio


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

    An unparseable line or an unknown ``op`` is fatal, never skipped: the protocol is
    internal, so drift means a bug and must be loud (``src/v2/protocol.rs`` module
    docs).
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
            emit(collect_file(str(request["path"])))
        elif op == "shutdown":
            emit(handle_shutdown())
            return 0
        else:
            print(f"rustest v2 worker: unknown op {op!r} in line: {line!r}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main())
