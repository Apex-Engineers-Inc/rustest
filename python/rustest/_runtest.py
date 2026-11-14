"""pytest's execution-side objects, for the hooks rustest runs around each test.

``TestReport``, ``CallInfo``, ``Stash`` and ``StashKey`` are what ``pytest.TestReport`` and friends
resolve to under rustest, so ``isinstance`` against the pytest names holds.  They are ordinary,
keepable objects -- a plugin that collects reports may hold them for the whole session, as under
pytest -- except where rustest's answer would not be pytest's: a failure's ``longrepr`` carries
``reprcrash`` and nothing else, the captured-output sections are not modelled, and
``report.outcome`` cannot be rewritten.  Touching any of those is a violation (:func:`set_violate`):
the session goes to pytest.  Standalone: no pytest import, nothing from the worker.
"""

from __future__ import annotations

import os
import time
import traceback
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, Generic, NoReturn, TypeVar, cast

__all__ = ["CallInfo", "ReprFileLocation", "Stash", "StashKey", "TestReport", "set_violate"]

T = TypeVar("T")
D = TypeVar("D")

_violate: Callable[[str], NoReturn] | None = None

#: Frames inside rustest itself are pytest's internals as far as a report is concerned.
_PACKAGE_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))


def set_violate(violate: Callable[[str], NoReturn] | None) -> None:
    """Install the hook runtime's violation recorder (``HookRunner.violate``)."""
    global _violate
    _violate = violate


def _refuse(what: str) -> NoReturn:
    if _violate is not None:
        _violate(what)
    raise AttributeError(what)


class StashKey(Generic[T]):
    """`_pytest/stash.py::StashKey`: a typed key, compared by identity."""

    __slots__ = ()


class Stash:
    """`_pytest/stash.py::Stash`: a type-safe heterogeneous mutable mapping."""

    __slots__ = ("_storage",)

    def __init__(self) -> None:
        super().__init__()
        self._storage: dict[StashKey[Any], object] = {}

    def __setitem__(self, key: StashKey[T], value: T) -> None:
        self._storage[key] = value

    def __getitem__(self, key: StashKey[T]) -> T:
        return cast(T, self._storage[key])

    def get(self, key: StashKey[T], default: D) -> T | D:
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key: StashKey[T], default: T) -> T:
        try:
            return self[key]
        except KeyError:
            self[key] = default
            return default

    def __delitem__(self, key: StashKey[T]) -> None:
        del self._storage[key]

    def __contains__(self, key: StashKey[T]) -> bool:
        return key in self._storage

    def __len__(self) -> int:
        return len(self._storage)


class ReprFileLocation:
    """`_pytest/_code/code.py::ReprFileLocation`: where a failure was raised."""

    __slots__ = ("lineno", "message", "path")

    def __init__(self, path: str, lineno: int, message: str) -> None:
        super().__init__()
        self.path = path
        self.lineno = lineno
        self.message = message


def _hidden(tb: TracebackType) -> bool:
    frame = tb.tb_frame
    if os.path.normcase(os.path.dirname(os.path.abspath(frame.f_code.co_filename))) == _PACKAGE_DIR:
        return True
    hide = frame.f_locals.get("__tracebackhide__", frame.f_globals.get("__tracebackhide__"))
    return bool(hide)


def _exconly(exc: BaseException) -> str:
    """`ExceptionInfo.exconly(tryshort=True)`: a rewritten assert loses ``AssertionError: ``."""
    text = "".join(traceback.format_exception_only(type(exc), exc)).rstrip()
    if isinstance(exc, AssertionError):
        info = getattr(exc, "msg", None)
        if info is None:
            info = repr(exc)
        if str(info).startswith("AssertionError('assert ") and text.startswith("AssertionError: "):
            text = text[len("AssertionError: ") :]
    return text


def reprcrash(exc: BaseException, message: str | None = None) -> ReprFileLocation:
    """`ExceptionInfo._getreprcrash`: the innermost frame that is not pytest's (or rustest's)."""
    frames: list[TracebackType] = []
    tb = exc.__traceback__
    while tb is not None:
        frames.append(tb)
        tb = tb.tb_next
    for entry in reversed(frames):
        if not _hidden(entry):
            return ReprFileLocation(
                entry.tb_frame.f_code.co_filename,
                entry.tb_lineno,
                _exconly(exc) if message is None else message,
            )
    last = frames[-1] if frames else None
    return ReprFileLocation(
        "" if last is None else last.tb_frame.f_code.co_filename,
        0 if last is None else last.tb_lineno,
        _exconly(exc) if message is None else message,
    )


class FailureRepr:
    """A failure's ``longrepr``: its ``reprcrash``.  rustest formats a failure its own way, so
    the text, the traceback entries and ``toterminal`` are pytest's to answer."""

    __slots__ = ("reprcrash",)

    def __init__(self, crash: ReprFileLocation) -> None:
        super().__init__()
        self.reprcrash = crash

    def __getattr__(self, name: str) -> NoReturn:
        _refuse(f"reads report.longrepr.{name}")

    def __str__(self) -> str:
        _refuse("reads the text of report.longrepr")

    __repr__ = __str__


class CallInfo(Generic[T]):
    """`_pytest/runner.py::CallInfo`: the result or exception of one phase's hook call."""

    def __init__(
        self,
        result: T | None,
        excinfo: Any,
        start: float,
        stop: float,
        duration: float,
        when: str,
    ) -> None:
        super().__init__()
        self._result = result
        self.excinfo = excinfo
        self.start = start
        self.stop = stop
        self.duration = duration
        self.when = when

    @property
    def result(self) -> T:
        if self.excinfo is not None:
            raise AttributeError(f"{self!r} has no valid result")
        return cast(T, self._result)

    @classmethod
    def from_call(
        cls,
        func: Callable[[], T],
        when: str,
        reraise: type[BaseException] | tuple[type[BaseException], ...] | None = None,
    ) -> CallInfo[T]:
        from .decorators import ExceptionInfo

        excinfo = None
        start = time.time()
        precise_start = time.perf_counter()
        try:
            result: T | None = func()
        except BaseException as exc:
            if reraise is not None and isinstance(exc, reraise):
                raise
            excinfo = ExceptionInfo(type(exc), exc, exc.__traceback__)
            result = None
        duration = time.perf_counter() - precise_start
        return cls(result, excinfo, start, time.time(), duration, when)

    def __repr__(self) -> str:
        if self.excinfo is None:
            return f"<CallInfo when={self.when!r} result: {self._result!r}>"
        return f"<CallInfo when={self.when!r} excinfo={self.excinfo!r}>"


class TestReport:
    """`_pytest/reports.py::TestReport` for one phase of one test."""

    __test__ = False

    def __init__(
        self,
        nodeid: str,
        location: tuple[str, int | None, str],
        keywords: Mapping[str, object],
        outcome: str,
        longrepr: object,
        when: str,
        duration: float,
        start: float,
        stop: float,
        user_properties: list[tuple[str, object]],
        message: str | None,
    ) -> None:
        super().__init__()
        self.nodeid = nodeid
        self.location = location
        self.keywords = dict(keywords)
        self._outcome = outcome
        self._longrepr = longrepr
        self.when = when
        self.duration = duration
        self.start = start
        self.stop = stop
        self.user_properties = user_properties
        #: rustest's own text for the wire (never shown to a hook).
        self._message = message

    @property
    def outcome(self) -> str:
        return self._outcome

    @outcome.setter
    def outcome(self, value: str) -> None:
        _refuse(f"sets report.outcome to {value!r}")

    @property
    def longrepr(self) -> object:
        return self._longrepr

    @longrepr.setter
    def longrepr(self, value: object) -> None:
        _refuse("sets report.longrepr")

    @property
    def passed(self) -> bool:
        return self._outcome == "passed"

    @property
    def failed(self) -> bool:
        return self._outcome == "failed"

    @property
    def skipped(self) -> bool:
        return self._outcome == "skipped"

    @property
    def fspath(self) -> str:
        return os.fspath(self.nodeid.split("::")[0])

    @property
    def count_towards_summary(self) -> bool:
        return True

    @property
    def head_line(self) -> str | None:
        return self.location[2]

    @property
    def longreprtext(self) -> str:
        if self._longrepr is None:
            return ""
        if isinstance(self._longrepr, str):
            return self._longrepr.strip()
        if isinstance(self._longrepr, tuple):
            return str(cast("tuple[object, ...]", self._longrepr)).strip()
        _refuse("reads report.longreprtext")

    @property
    def sections(self) -> NoReturn:
        _refuse("reads report.sections")

    @property
    def capstdout(self) -> NoReturn:
        _refuse("reads report.capstdout")

    @property
    def capstderr(self) -> NoReturn:
        _refuse("reads report.capstderr")

    @property
    def caplog(self) -> NoReturn:
        _refuse("reads report.caplog")

    def get_sections(self, prefix: str) -> NoReturn:
        _refuse("reads report.get_sections")

    def toterminal(self, out: object) -> NoReturn:
        _refuse("calls report.toterminal")

    def __repr__(self) -> str:
        return f"<TestReport {self.nodeid!r} when={self.when!r} outcome={self._outcome!r}>"
