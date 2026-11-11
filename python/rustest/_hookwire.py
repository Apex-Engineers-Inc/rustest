"""The worker's half of the native hook runtime: ``_hookshim`` wired into ``_worker``.

``_hookshim`` runs a conftest's own ``pytest_addoption`` / ``pytest_configure`` /
``pytest_sessionstart`` / ``pytest_collection_modifyitems`` against strict shims, or says why not.
This module is everything between that and the worker protocol:

* **init.**  When ``init`` carries ``conftests`` (the session's conftests, each flagged
  ``initial``), the worker starts a :class:`HookRunner`, imports and registers the initial
  conftests and their ``pytest_plugins`` modules, and runs ``configure`` and ``sessionstart``.
  The rest register as the orchestrator has them imported, in walk order (pytest's own order:
  later conftests are registered during collection, after ``configure`` and ``sessionstart``).
  Without that field nothing here runs, and the worker behaves exactly as before.
* **request.config.**  :class:`HookSession` is the ``rustest._builtins.HookBridge``: the options
  and ini keys a conftest declared, the core options the CLI carried, and the attributes
  ``configure`` stored on ``config``.  ``request.config`` is the worker's own ``Config``; a declared
  option answers with its value and ignores a call-site ``default=``.
* **Records.**  :func:`build_record` makes the :class:`ItemRecord` of a collected test (wire form,
  sent on ``collected`` whenever the runtime is on); :func:`modify_items`
  runs one stage over the records the orchestrator sends back.
* **Reasons.**  Every hand-off reason the runner recorded is reported, once, as a whole reason
  (``REASON_PREFIX``) through the ``hooks`` arrays of ``collected`` / ``conftests_imported`` /
  ``modified_items`` -- always *before* a test executes.
* **Backstop.**  :func:`late_count` is the length of ``HookRunner.late_reasons``: a shim used after
  its hook returned.  The worker compares it around each test and errors the test during which it
  grew, even if the user swallowed the exception.

Imported lazily by ``_worker`` (and ``_hookshim`` lazily by this module): a run whose ``init``
carries no ``conftests`` imports neither.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

if TYPE_CHECKING:
    from ._hookshim import HookRunner

__all__ = [
    "REASON_PREFIX",
    "HookSession",
    "build_record",
    "late_count",
    "modify_items",
    "native_module",
    "pending_reasons",
    "start",
]

#: A reason that is already a whole sentence (``core.NeedsPytest`` does not wrap it).
REASON_PREFIX: Final = "rustest's hook runtime: "


class _MarkLike(Protocol):
    name: str
    args: tuple[object, ...]
    kwargs: Mapping[str, object]


class HookSession:
    """One worker's hook runtime, and the answers ``request.config`` gets from it."""

    def __init__(self, runner: HookRunner, options: Mapping[str, object]) -> None:
        super().__init__()
        self.runner = runner
        self.options: Mapping[str, object] = options
        self.initial: set[str] = set()
        self.loading_initial = False
        self.registered: set[int] = set()
        self._reported: set[str] = set()

    # -- the HookBridge ``request.config`` consults -------------------------------------

    def lookup_option(self, name: str) -> tuple[bool, object]:
        found, value = self.runner.lookup_option(name)
        if found:
            return True, value
        if name in self.options:
            return True, self.options[name]
        return False, None

    def lookup_ini(self, name: str) -> tuple[bool, object]:
        return self.runner.lookup_ini(name)

    def user_attribute(self, name: str) -> tuple[bool, object]:
        attributes = self.runner.user_attributes
        return name in attributes, attributes.get(name)

    # -- reasons ------------------------------------------------------------------------

    def new_reasons(self) -> list[str]:
        """Hand-off reasons not reported yet, as whole reasons; marks them reported."""
        fresh = [r for r in self.runner.reasons if r not in self._reported]
        self._reported.update(fresh)
        return [REASON_PREFIX + reason for reason in fresh]


#: This worker's runtime, or ``None``: the hook runtime is off.
_session: HookSession | None = None


def late_count() -> int:
    """How many uses of a shim after its hook returned this worker has seen."""
    return len(_session.runner.late_reasons) if _session is not None else 0


def late_reasons_since(count: int) -> list[str]:
    return _session.runner.late_reasons[count:] if _session is not None else []


def pending_reasons() -> list[str]:
    return _session.new_reasons() if _session is not None else []


def start(
    message: Mapping[str, object],
    *,
    rootdir: Path,
    invocation_dir: Path,
    plugins: Sequence[str],
    load: Callable[[Path], None],
) -> HookSession | None:
    """Start the runtime if ``init`` asked for it (``conftests``); ``load(path)`` imports one
    conftest (and its ``pytest_plugins``) through the worker, which calls back :func:`on_module`.

    Only the **initial** conftests are imported here, then ``configure`` and ``sessionstart`` run.
    The rest arrive later, in walk order, through ``import_conftests`` (pytest imports each when
    its walk reaches it), and register then -- pluggy's historic ``pytest_configure`` included.
    *plugins* are the ``pytest11`` entry points pytest would load (``-p no:`` already applied).
    """
    global _session
    entries = message.get("conftests")
    if not isinstance(entries, list) or not entries:
        return None
    from . import _builtins
    from ._hookshim import HookRunner

    raw_context = message.get("hook_context")
    context: dict[str, object] = (
        {str(k): v for k, v in cast("dict[object, object]", raw_context).items()}
        if isinstance(raw_context, dict)
        else {}
    )
    raw_options = context.get("options")
    options: dict[str, object] = (
        {str(k): v for k, v in cast("dict[object, object]", raw_options).items()}
        if isinstance(raw_options, dict)
        else {}
    )
    raw_ini = context.get("ini")  # the ini file's values: what `addini` keys are read from
    ini: dict[str, object] = (
        {str(k): v for k, v in cast("dict[object, object]", raw_ini).items()}
        if isinstance(raw_ini, dict)
        else {}
    )
    runner = HookRunner(
        rootpath=rootdir,
        ini=ini,
        builtin_options=options,
        invocation_dir=invocation_dir,
        plugins=plugins,
    )
    _session = HookSession(runner, options)
    _builtins.set_hooks(_session)
    initial = [
        Path(str(cast("dict[str, object]", entry)["path"]))
        for entry in cast("list[object]", entries)
        if isinstance(entry, dict) and cast("dict[str, object]", entry).get("initial")
    ]
    _session.initial.update(os.path.normcase(str(path)) for path in initial)
    _session.loading_initial = True
    for path in initial:
        try:
            load(path)
        except Exception:  # noqa: BLE001 - recorded by the worker as an import failure
            pass
    _session.loading_initial = False
    runner.configure()
    runner.sessionstart()
    _sync_marks(runner)
    return _session


def _sync_marks(runner: HookRunner) -> None:
    """Tell the ``PytestUnknownMarkWarning`` registry what ``pytest_configure`` registered. If the
    runtime refused something, the registry may be incomplete (without fallback the run goes on),
    so nothing is checked -- a missed warning, never a false one (or a false error) -- and the
    stderr note says so (``src/engine/collect.rs::unrun_hooks_note``)."""
    from .compat.pytest import add_registered_markers, set_mark_registry

    if runner.reasons:
        set_mark_registry(None, strict=False)
    else:
        add_registered_markers(runner.markers)


def on_module(label: str, module: object, *, conftest: Path | None = None) -> None:
    """A conftest or ``pytest_plugins`` module was just imported: register it, in import order.
    A ``pytest_plugins`` module is initial when it was imported for an initial conftest."""
    if _session is None or id(module) in _session.registered:
        return
    _session.registered.add(id(module))
    initial = (
        os.path.normcase(str(conftest)) in _session.initial
        if conftest is not None
        else _session.loading_initial
    )
    _session.runner.register(label, module, initial=initial)
    _sync_marks(_session.runner)


def native_module(module: object) -> bool:
    """Did the runtime take over this module's native hooks (so they are not 'unrun')?"""
    return _session is not None and id(module) in _session.registered


# --------------------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------------------


def wants_records() -> bool:
    return _session is not None and _session.runner.implements("pytest_collection_modifyitems")


def build_record(
    *,
    nodeid: str,
    path: Path,
    rootdir: Path,
    parts: Sequence[str],
    func: object,
    marks: Sequence[_MarkLike],
    own_count: int,
    parametrized: bool,
    reordered: bool,
    kind: str,
    has_function_marks: bool,
) -> dict[str, Any]:
    """The :class:`ItemRecord` of one collected test, in wire form.

    *marks* are in pytest's ``iter_markers`` order **minus** the ``parametrize`` marks (the worker
    cannot place them: ``parametrized`` says they are missing): the function's own decorators, then
    the parameter set's marks, then the enclosing classes' and the module's.  Anything the wire
    cannot carry exactly makes the record unusable (kind ``"unsendable"``), which hands the session
    to pytest the moment a hook is shown it.
    """
    from ._hookshim import ItemRecord, Mark, mark_is_wire_exact

    try:
        relative = path.relative_to(rootdir).parts
    except ValueError:
        relative = (path.name,)
    segment = nodeid.rsplit("::", 1)[-1]
    name = parts[-1] if parts else segment
    param_id = segment[len(name) + 1 : -1] if segment.startswith(name + "[") else None
    attributes = {
        key
        for key in getattr(func, "__dict__", {})
        if isinstance(key, str) and not key.startswith("__rustest_")
    }
    if has_function_marks:
        attributes.add("pytestmark")  # pytest's decorators leave it on the function
    hook_marks = tuple(Mark(m.name, tuple(m.args), dict(m.kwargs)) for m in marks)
    if not all(mark_is_wire_exact(m) for m in hook_marks):
        kind = "unsendable"
    record = ItemRecord(
        nodeid=nodeid,
        name=segment,
        path=str(path),
        markers=hook_marks if kind != "unsendable" else (),
        own_marker_count=own_count,
        rootdir_name=rootdir.name,
        dir_names=tuple(relative[:-1]),
        module_name=relative[-1],
        class_names=tuple(parts[:-1]),
        function_attrs=frozenset(attributes),
        param_id=param_id,
        kind=kind,
        parametrized=parametrized,
        reordered=reordered,
    )
    return record.to_wire()


def modify_items(stage: int, wire_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Run one ``pytest_collection_modifyitems`` stage over *wire_records*: ``stage`` 1 is the
    ``tryfirst`` and plain hooks, 2 the ``trylast`` ones.

    The reply is the ``items_modified`` response the orchestrator compares across workers: the
    order, the deselected ids (duplicates kept, as pytest counts them), the vanished ones, and the
    marks added (``mark`` is a manifest ``MarkSpec``).  A mark whose arguments the wire cannot
    carry exactly is a reason, not a guess; with a reason the rest is not to be used.
    """
    from ._hookshim import ItemRecord, mark_is_wire_exact

    reply: dict[str, Any] = {"op": "items_modified", "order": []}
    if _session is None:
        reply["order"] = [str(r["nodeid"]) for r in wire_records]
        return reply
    runner = _session.runner
    records = [ItemRecord.from_wire(r) for r in wire_records]
    effects = (
        runner.modify_collection_before_selection(records)
        if stage == 1
        else runner.modify_collection_after_selection(records)
    )
    marks: list[dict[str, Any]] = []
    for added in effects.marks:
        if not mark_is_wire_exact(added.mark):
            runner.note_violation(
                f"added a {added.mark.name!r} mark to {added.nodeid} whose arguments cannot be"
                + " sent between processes exactly"
            )
            continue
        spec: dict[str, Any] = {"name": added.mark.name}
        if added.mark.args:
            spec["args"] = list(added.mark.args)
        if added.mark.kwargs:
            spec["kwargs"] = dict(added.mark.kwargs)
        marks.append({"nodeid": added.nodeid, "mark": spec, "append": added.append})
    if runner.reasons:  # a hand-off is coming: effects computed beside a violation mean nothing
        reply["order"] = [r.nodeid for r in records]
    else:
        reply["order"] = effects.order
        if effects.deselected:
            reply["deselected"] = effects.deselected
        if effects.vanished:
            reply["vanished"] = effects.vanished
        if marks:
            reply["marks"] = marks
    if reasons := _session.new_reasons():
        reply["hooks"] = reasons
    return reply
