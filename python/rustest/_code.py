"""The read surface of ``_pytest._code`` — ``Traceback`` and what it is made of.

Port of `_pytest/_code/code.py` (pytest 8.4.2) and the sliver of `_pytest/_code/source.py`
it needs, restricted to the **read** surface a test suite touches:
:class:`Source`, :class:`Code`, :class:`Frame`, :class:`TracebackEntry`, :class:`Traceback`.

**Why it exists — MECHANISM M3 of the Phase 4 Task 1b sweep.** ``ExceptionInfo.traceback``
is not the raw ``types.TracebackType``. pytest returns a ``Traceback``, which *subclasses
list*, so ``len(excinfo.traceback)``, ``excinfo.traceback[0]`` and ``for entry in
excinfo.traceback`` are all part of the public API. rustest aliased it to ``.tb``, and
werkzeug's ``tests/test_utils.py::test_import_string_provides_traceback`` does::

    traceback = "".join(str(line) for line in baz_exc.traceback)
    assert "bb.py':1" in traceback
    assert "from os import a_typo" in traceback

...which under rustest raised ``TypeError: 'traceback' object is not iterable``. The two
assertions are also why :meth:`TracebackEntry.__str__` and :attr:`TracebackEntry.statement`
had to be ported rather than approximated: the first pins the exact
``  File '<path>':<lineno> in <name>`` spelling, the second the *source text* of the
failing statement.

**The off-by-one is pytest's, and it is load-bearing.** ``TracebackEntry.lineno`` is
``tb_lineno - 1`` — zero-based — and ``__str__`` prints ``self.lineno + 1``. Anything that
"corrects" one without the other silently shifts every line number a suite asserts on.

What is deliberately *not* here: ``ExceptionChainRepr`` and the whole formatting half
(``getrepr``/``repr_excinfo``), which is pytest's terminal rendering and which rustest does
through :mod:`rustest._assertion`; and ``TracebackEntry.ishidden``'s ``__tracebackhide__``
protocol, which rustest already implements against raw frames in
``_v2_worker._visible_frames``.
"""

from __future__ import annotations

import ast
import linecache
import types
from pathlib import Path
from typing import Any, Iterator, Sequence, overload


class Source:
    """A block of source lines. Port of `_pytest/_code/source.py::Source`'s read surface.

    pytest's is a list-of-lines wrapper whose ``__str__`` re-joins with newlines, so
    ``str(entry.statement)`` yields the statement's text with no trailing newline. Slicing
    returns another ``Source``; integer indexing returns one line as a ``str``. Both are
    pytest's (l. 43-52).
    """

    def __init__(self, obj: str | Sequence[str] | None = None) -> None:
        super().__init__()
        if obj is None:
            self.lines: list[str] = []
        elif isinstance(obj, str):
            self.lines = obj.split("\n")
        else:
            self.lines = list(obj)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Source):
            return self.lines == other.lines
        if isinstance(other, str):
            return str(self) == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(tuple(self.lines))

    @overload
    def __getitem__(self, key: int) -> str: ...

    @overload
    def __getitem__(self, key: slice) -> Source: ...

    def __getitem__(self, key: int | slice) -> str | Source:
        if isinstance(key, int):
            return self.lines[key]
        if key.step not in (None, 1):
            raise IndexError("cannot slice a Source with a step")
        return Source(self.lines[key.start : key.stop])

    def __iter__(self) -> Iterator[str]:
        return iter(self.lines)

    def __len__(self) -> int:
        return len(self.lines)

    def __str__(self) -> str:
        return "\n".join(self.lines)

    def deindent(self) -> Source:
        """`source.py::Source.deindent` (l. 78-81) — ``textwrap.dedent`` over the block."""
        import textwrap

        return Source(textwrap.dedent(str(self)))

    def getstatement(self, lineno: int) -> Source:
        """The full statement containing (zero-based) *lineno*.

        pytest resolves this with ``getstatementrange_ast`` (`source.py` l. 132-186), which
        parses the module and walks for the smallest statement node spanning the line — the
        point being that a call split over five lines renders as all five, not as the one
        the traceback happens to name. Reproduced with the same AST information: since
        Python 3.8 every ``ast.stmt`` carries ``end_lineno``, so the smallest containing
        statement is directly computable and no range-walking heuristic is needed.

        A file that cannot be parsed (or a line that belongs to no statement, which happens
        for synthesized code objects) falls back to the single line, which is also what
        pytest's ``except SyntaxError: end = self.lineno + 1`` arm does.
        """
        start, end = self._statement_range(lineno)
        return Source(self.lines[start:end])

    def _statement_range(self, lineno: int) -> tuple[int, int]:
        target = lineno + 1  # ast line numbers are 1-based
        try:
            tree = ast.parse(str(self))
        except (SyntaxError, ValueError):
            return lineno, lineno + 1
        best: tuple[int, int] | None = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt):
                continue
            end_lineno = node.end_lineno
            if end_lineno is None:  # pragma: no cover - always set on 3.8+
                continue
            if node.lineno <= target <= end_lineno:
                span = (node.lineno - 1, end_lineno)
                if best is None or (span[1] - span[0]) < (best[1] - best[0]):
                    best = span
        if best is None:
            return lineno, lineno + 1
        return best


class Code:
    """A code object, with the accessors pytest hangs off it (`code.py` l. 58-118)."""

    def __init__(self, obj: types.CodeType) -> None:
        super().__init__()
        self.raw = obj

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Code) and self.raw == other.raw

    def __hash__(self) -> int:
        return hash(self.raw)

    @property
    def firstlineno(self) -> int:
        """Zero-based, as pytest's is (``co_firstlineno - 1``)."""
        return self.raw.co_firstlineno - 1

    @property
    def name(self) -> str:
        return self.raw.co_name

    @property
    def path(self) -> Path | str:
        """The source path, or ``co_filename`` verbatim when there is no such file.

        pytest returns the **string** for a non-existent file (``<string>``, a doctest, an
        ``exec``'d snippet) rather than a ``Path`` that cannot be opened, and
        :meth:`TracebackEntry.__str__` prints whichever it gets. Ported, because the
        difference shows up in the rendered line werkzeug asserts on.
        """
        try:
            path = Path(self.raw.co_filename)
            if not path.exists():
                return self.raw.co_filename
        except OSError:  # pragma: no cover - unrepresentable filename
            return self.raw.co_filename
        return path

    @property
    def fullsource(self) -> Source | None:
        """The whole file this code came from, or ``None``.

        ``linecache`` rather than ``inspect.getsource`` because that is what keeps a
        *modified-since-import* file consistent with what the traceback's line numbers mean,
        and because it is already populated for anything that has raised.
        """
        lines = linecache.getlines(self.raw.co_filename)
        if not lines:
            return None
        return Source([line.rstrip("\n") for line in lines])


class Frame:
    """A stack frame — `code.py::Frame` (l. 121-189), read surface only."""

    def __init__(self, frame: types.FrameType) -> None:
        super().__init__()
        self.raw = frame
        self.lineno = frame.f_lineno - 1
        self.f_globals = frame.f_globals
        self.f_locals = frame.f_locals
        self.code = Code(frame.f_code)

    @property
    def statement(self) -> Source:
        """The statement this frame is currently executing."""
        if self.code.fullsource is None:
            return Source("")
        return self.code.fullsource.getstatement(self.lineno)

    def eval(self, code: str, **vars: Any) -> Any:
        """`Frame.eval` (l. 148-158): evaluate *code* in this frame's namespaces.

        **The ``eval`` is the feature.** This is pytest's post-mortem introspection hook —
        "evaluate this expression as if you were standing in that frame" — and it is what
        ``--showlocals``-style tooling and debugging plugins call. There is no safer
        substitute: ``ast.literal_eval`` cannot see ``f_globals``/``f_locals``, which is the
        entire question being asked. The expression comes from the *caller of the test
        framework*, never from the code under test, and it is evaluated in a frame that
        process has already executed, so it grants no capability the caller did not already
        have. Substituting anything narrower would silently answer a different question.
        """
        f_locals = self.f_locals.copy()
        f_locals.update(vars)
        return eval(code, self.f_globals, f_locals)  # noqa: S307 - see the docstring

    def repr(self, object: object) -> str:
        """`Frame.repr` (l. 160-162)."""
        return repr(object)

    def getargs(self, var: bool = False) -> list[tuple[str, Any]]:
        """`Frame.getargs` (l. 164-189): the frame's arguments, name/value pairs."""
        retval: list[tuple[str, Any]] = []
        for arg in self.code.raw.co_varnames[: self.code.raw.co_argcount]:
            try:
                retval.append((arg, self.f_locals[arg]))
            except KeyError:
                pass  # this can occur when using Psyco
        if var:
            for arg in self.code.raw.co_varnames[
                self.code.raw.co_argcount : self.code.raw.co_argcount
                + self.code.raw.co_kwonlyargcount
            ]:
                try:
                    retval.append((arg, self.f_locals[arg]))
                except KeyError:
                    pass
        return retval


class TracebackEntry:
    """One frame of a traceback — `code.py::TracebackEntry` (l. 192-354).

    ``lineno`` is **zero-based** (``tb_lineno - 1``, l. 210-212) and :meth:`__str__` adds the
    one back. Both halves are pytest's and neither may move on its own.
    """

    __slots__ = ("_rawentry",)

    def __init__(self, rawentry: types.TracebackType) -> None:
        super().__init__()
        self._rawentry = rawentry

    @property
    def lineno(self) -> int:
        return self._rawentry.tb_lineno - 1

    @property
    def frame(self) -> Frame:
        return Frame(self._rawentry.tb_frame)

    @property
    def relline(self) -> int:
        return self.lineno - self.frame.code.firstlineno

    @property
    def name(self) -> str:
        """`co_name` of the underlying code (l. 352-354)."""
        return self.frame.code.raw.co_name

    @property
    def path(self) -> Path | str:
        return self.frame.code.path

    @property
    def locals(self) -> dict[str, Any]:
        return self.frame.f_locals

    @property
    def statement(self) -> Source:
        """The source of the statement this entry points at (l. 269-274)."""
        source = self.frame.code.fullsource
        if source is None:
            return Source("")
        return source.getstatement(self.lineno)

    def getfirstlinesource(self) -> int:
        return self.frame.code.firstlineno

    def getsource(self) -> Source | None:
        """`TracebackEntry.getsource` (l. 285-308), reduced to the statement it resolves."""
        if self.frame.code.fullsource is None:
            return None
        return self.statement

    source = property(getsource)

    def __repr__(self) -> str:
        return f"<TracebackEntry {self.frame.code.path}:{self.lineno + 1}>"

    def __str__(self) -> str:
        """Port of l. 337-350, character for character.

        pytest's own comment there is worth carrying: *"This output does not quite match
        Python's repr for traceback entries, but changing it to do so would break certain
        plugins."* It is therefore a contract, not a rendering choice — and it is what
        werkzeug's ``assert "bb.py':1" in traceback`` matches against.
        """
        name = self.frame.code.name
        try:
            line = str(self.statement).lstrip()
        except KeyboardInterrupt:
            raise
        except BaseException:
            line = "???"
        return f"  File '{self.path}':{self.lineno + 1} in {name}\n  {line}\n"


class Traceback(list[TracebackEntry]):
    """A list of :class:`TracebackEntry` — `code.py::Traceback` (l. 356-...).

    **A ``list`` subclass**, which is the whole point: ``len()``, indexing, iteration and
    slicing are the API, and returning the raw ``TracebackType`` supported none of them.
    """

    def __init__(self, tb: types.TracebackType | list[TracebackEntry]) -> None:
        if isinstance(tb, types.TracebackType):

            def walk(cur: types.TracebackType | None) -> Iterator[TracebackEntry]:
                while cur is not None:
                    yield TracebackEntry(cur)
                    cur = cur.tb_next

            super().__init__(walk(tb))
        else:
            super().__init__(tb)

    def cut(
        self,
        path: Any = None,
        lineno: int | None = None,
        firstlineno: int | None = None,
        excludepath: Any = None,
    ) -> Traceback:
        """`Traceback.cut` (l. 376-...): the sub-traceback starting at the first match.

        Returns ``self`` when nothing matches, exactly as pytest does — a ``cut`` that
        found nothing must not silently empty the traceback.
        """
        import os

        path_ = None if path is None else os.fspath(path)
        excludepath_ = None if excludepath is None else os.fspath(excludepath)
        for index, entry in enumerate(self):
            code = entry.frame.code
            codepath = code.path
            if path is not None and str(codepath) != path_:
                continue
            if (
                lineno is not None
                and entry.lineno != lineno
                or firstlineno is not None
                and code.firstlineno != firstlineno
            ):
                continue
            if excludepath is not None and str(codepath).startswith(str(excludepath_)):
                continue
            return Traceback(self[index:])
        return self

    def filter(self, fn: Any = None) -> Traceback:
        """`Traceback.filter`: the entries *fn* keeps, as a ``Traceback``.

        pytest's default predicate hides ``__tracebackhide__`` frames; here the default
        keeps everything, because rustest applies that rule in the worker
        (``_v2_worker._visible_frames``) where it also has the ``ExceptionInfo`` the
        callable form of ``__tracebackhide__`` expects.
        """
        if fn is None:
            return Traceback(list(self))
        return Traceback([entry for entry in self if fn(entry)])

    def __getitem__(self, key: Any) -> Any:
        """A slice of a ``Traceback`` is a ``Traceback``; an index is an entry.

        ``list.__getitem__`` would hand back a plain ``list`` for a slice, and pytest's own
        ``cut``/``filter`` callers chain further ``Traceback`` methods onto the result.
        """
        if isinstance(key, slice):
            return Traceback(list.__getitem__(self, key))
        return list.__getitem__(self, key)

    def __repr__(self) -> str:
        return f"<Traceback {list(self)!r}>"
