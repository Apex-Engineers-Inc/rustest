"""Live progress output in pytest's serial layout (the plain renderer).

Fed by the engine's ``on_event`` callback, in the orchestrator process only:

* ``("collected", (ids, rootdir))`` -- every selected node id in manifest order; an id's
  index is its *slot*.
* ``("results", [(slot, status, reason), ...])`` -- finished tests in **arrival** order,
  which with a pool of workers is not slot order.
* ``("done", stopped)`` -- the session ended; ``stopped`` means it was cut short
  (``-x``/``--maxfail``, ``pytest.exit``), in which case pytest writes no final percent.

Results are held until every earlier slot has been printed, so the bytes written are
identical for any arrival order.  The rules below are ported from pytest 8.4.2's
``_pytest/terminal.py`` (``pytest_runtest_logreport``, ``write_fspath_result``,
``_write_progress_information_filling_space``, ``_write_progress_information_if_past_edge``,
``_format_trimmed``) and are diffed against real pytest in ``test_progress_oracle.py``.

Deliberately not matched: the blank line pytest writes between ``collected N items`` and
the first path (this renderer prints no header, so it owns nothing above its first line);
``-vv`` (treated as ``-v``); double-width characters (``len`` stands in for ``wcswidth``);
and the ``.E`` pytest prints for a pass whose teardown errors (one status per test here).
"""

from __future__ import annotations

import os
from typing import Final, TextIO, cast

#: engine status -> (``-q``/default letter, ``-v`` word).  pytest's own table.
_STATUS: Final[dict[str, tuple[str, str]]] = {
    "passed": (".", "PASSED"),
    "failed": ("F", "FAILED"),
    "error": ("E", "ERROR"),
    "skipped": ("s", "SKIPPED"),
    "xfailed": ("x", "XFAIL"),
    "xpassed": ("X", "XPASS"),
}

#: Statuses whose reason pytest appends to a ``-v`` line.
_REASON_STATUSES: Final = frozenset({"skipped", "xfailed", "xpassed"})

#: ``len(" [100%]")``: the column pytest keeps free on the right.
_PERCENT_WIDTH: Final = 7

_Result = tuple[int, str, str | None]


def _trim_reason(reason: str, available: int) -> str | None:
    """pytest's ``_format_trimmed(" ({})", reason, available)``, with ``len`` for width."""
    msg = reason.split("\n", 1)[0]
    if 3 + 3 > available:
        return None
    if 3 + len(msg) > available:
        msg = msg[: available - 6] + "..."
    return f" ({msg})"


class Progress:
    """Writes pytest's progress section to *out* as events arrive; one call per event."""

    def __init__(
        self,
        out: TextIO,
        *,
        verbosity: int,
        width: int,
        cwd: str,
        capture: bool = True,
    ) -> None:
        super().__init__()
        self._out = out
        self._verbose = verbosity > 0
        self._letters_only = verbosity < 0  # -q: no path column
        self._width = width
        self._cwd = cwd
        self._percent = capture  # pytest drops the column under -s
        self._ids: list[str] = []
        self._root = ""
        self._relative_ids = False
        self._pending: dict[int, tuple[str, str | None]] = {}
        self._next = 0
        self._reported = 0
        self._col = 0  # width of the current output line
        self._file: str | None = None  # the path the current line belongs to
        self._started = False  # something has been written
        self._paths: dict[str, str] = {}
        self._buf: list[str] = []

    def __call__(self, kind: str, payload: object) -> None:
        if kind == "collected":
            self._ids, self._root = cast("tuple[list[str], str]", payload)
            # pytest rewrites `-v` ids only when the invocation dir is not the rootdir.
            self._relative_ids = os.path.normcase(os.path.abspath(self._root)) != os.path.normcase(
                os.path.abspath(self._cwd)
            )
        elif kind == "results":
            for slot, status, reason in cast("list[_Result]", payload):
                self._pending[slot] = (status, reason)
            self._drain()
        elif kind == "done":
            self._drain(everything=True)
            self._finish(stopped=bool(payload))
        self._flush()

    def close(self) -> None:
        """End a half-written line: an interrupted run sends no ``done`` event."""
        if self._col:
            self._w("\n")
            self._flush()

    # -- output -----------------------------------------------------------------------

    def _w(self, text: str) -> None:
        self._buf.append(text)
        head, newline, tail = text.rpartition("\n")
        self._col = len(tail) if newline else self._col + len(head) + len(tail)

    def _flush(self) -> None:
        text = "".join(self._buf)
        self._buf.clear()
        if not text:
            return
        # Unencodable characters are the caller's to escape: `core.run` writes inside
        # `_escaping_unencodable_output()`.
        _ = self._out.write(text)
        self._out.flush()

    def _fill(self) -> None:
        total = len(self._ids)
        msg = f" [{self._reported * 100 // total:3d}%]" if total else " [100%]"
        self._w(msg.rjust(self._width - self._col - 1))

    # -- ordering ---------------------------------------------------------------------

    def _drain(self, *, everything: bool = False) -> None:
        if everything:  # `-x` leaves gaps; print what arrived, in slot order
            order = sorted(self._pending)
        else:
            order: list[int] = []
            while self._next in self._pending:
                order.append(self._next)
                self._next += 1
        for slot in order:
            status, reason = self._pending.pop(slot)
            self._emit(self._ids[slot], status, reason)

    # -- one test ---------------------------------------------------------------------

    def _path(self, file: str) -> str:
        path = self._paths.get(file)
        if path is None:
            full = os.path.join(self._root, file)
            try:
                path = os.path.relpath(full, self._cwd)
            except ValueError:  # another drive: pytest prints the absolute path
                path = os.path.normpath(full)
            self._paths[file] = path
        return path

    def _emit(self, node_id: str, status: str, reason: str | None) -> None:
        letter, word = _STATUS.get(status, ("?", status.upper()))
        file, sep, rest = node_id.partition("::")
        if self._verbose:
            if self._started:
                self._w("\n")
            shown = node_id
            if self._relative_ids:
                shown = self._path(file) + sep + rest
            self._w(f"{shown} {word}")
            if reason and status in _REASON_STATUSES:
                trimmed = _trim_reason(reason, self._width - self._col - _PERCENT_WIDTH - 1)
                if trimmed is not None:
                    self._w(trimmed)
            self._reported += 1
            if self._percent:
                self._fill()
            self._started = True
            return
        if not self._letters_only and file != self._file:
            if self._file is not None:
                if self._percent:
                    self._fill()
                self._w("\n")
            self._file = file
            self._w(self._path(file) + " ")
        self._reported += 1
        self._w(letter)
        self._started = True
        if self._percent and self._reported < len(self._ids):
            if self._col + _PERCENT_WIDTH + 1 >= self._width:  # past the edge: wrap
                total = len(self._ids)
                self._w(f" [{self._reported * 100 // total:3d}%]\n")

    def _finish(self, *, stopped: bool) -> None:
        if not self._started:
            return
        if not self._verbose and self._percent and not stopped:
            self._fill()
        self._w("\n")
        if self._verbose or not self._letters_only:
            self._w("\n")  # pytest's summary `ensure_newline`, absent under -q
