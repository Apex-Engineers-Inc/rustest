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
through :mod:`rustest._assertion`.

``TracebackEntry.ishidden`` **is** here as of the Phase 4 final polish wave, because
:meth:`Traceback.filter`'s primary argument form is an ``ExceptionInfo`` and that form is
*defined* as "drop the hidden entries". It was previously left out on the ground that
``_worker._visible_frames`` already applies the rule against raw frames — true, and that
remains where the runner's own traceback trimming happens; but a caller who writes
``excinfo.traceback.filter(excinfo)`` is asking this class, not the worker.
"""

from __future__ import annotations

import ast
import inspect
import linecache
import os
import tokenize
import types
import warnings
from bisect import bisect_right
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
        """The full statement containing (zero-based) *lineno* — `source.py` l. 92-95."""
        start, end = self.getstatementrange(lineno)
        return self[start:end]

    def getstatementrange(self, lineno: int) -> tuple[int, int]:
        """``(start, end)`` for the minimal statement region holding *lineno* (l. 97-103).

        Out-of-range is an ``IndexError``, as pytest's is; both callers of ``statement``
        already sit inside the ``except BaseException: line = "???"`` arm that pytest's
        ``TracebackEntry.__str__`` uses for exactly this.
        """
        if not (0 <= lineno < len(self)):
            raise IndexError("lineno out of range")
        _, start, end = getstatementrange_ast(lineno, self)
        return start, end


def get_statement_startend2(lineno: int, node: ast.AST) -> tuple[int, int | None]:
    """Port of `_pytest/_code/source.py::get_statement_startend2` (l. 148-170), verbatim.

    **This is the whole of I1, and it is not a detail.** The rule is *not* "the smallest AST
    node whose ``lineno..end_lineno`` spans the line" — which is what rustest computed before,
    and which is wrong for every compound statement. It is: flatten the **start line** of every
    ``stmt`` and every ``ExceptHandler`` into one sorted list, then the statement containing
    *lineno* runs from the greatest start ``<= lineno`` to **the next start**, exclusive.

    The difference is visible on any header. For::

        with open(p) as fh:      # line 10
            data = fh.read()     # line 11

    pytest's range for line 10 is ``[10, 11)`` — the ``with`` **header alone**, because the
    next statement starts at 11. The end-lineno rule returns the ``with`` node's own span,
    ``[10, 12)``, i.e. the header *plus its whole body*. A frame stopped on a ``for``/``if``/
    ``with``/``try`` header therefore rendered the entire block into a single traceback line,
    and the longer the block the worse it got.

    Three refinements of pytest's that a paraphrase drops:

    * a decorated ``ClassDef``/``FunctionDef``/``AsyncFunctionDef`` contributes each
      **decorator's** line as well, because the node's own ``lineno`` points at the ``def``;
    * ``finalbody`` and ``orelse`` are marked as statements in their own right, at
      ``first_stmt.lineno - 2`` — the ``finally:``/``else:`` keyword line itself — so a frame
      inside a ``finally`` does not report the whole ``try``;
    * ``ExceptHandler`` is not an ``ast.stmt`` and would otherwise be invisible, making an
      ``except`` clause part of the preceding ``try`` body.
    """
    values: list[int] = []
    for x in ast.walk(node):
        if isinstance(x, (ast.stmt, ast.ExceptHandler)):
            # The lineno points to the class/def, so need to include the decorators.
            if isinstance(x, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in x.decorator_list:
                    values.append(d.lineno - 1)
            values.append(x.lineno - 1)
            for name in ("finalbody", "orelse"):
                val: list[ast.stmt] | None = getattr(x, name, None)
                if val:
                    # Treat the finally/orelse part as its own statement.
                    values.append(val[0].lineno - 1 - 1)
    values.sort()
    insert_index = bisect_right(values, lineno)
    start = values[insert_index - 1]
    if insert_index >= len(values):
        end = None
    else:
        end = values[insert_index]
    return start, end


def getstatementrange_ast(
    lineno: int,
    source: Source,
    assertion: bool = False,
    astnode: ast.AST | None = None,
) -> tuple[ast.AST, int, int]:
    """Port of `source.py::getstatementrange_ast` (l. 173-224), including both corrections.

    The bare next-statement bound is a *lower* bound on what a reader wants, because the AST
    has no idea about comments, blank lines or dedents. pytest applies two corrections and
    both are load-bearing:

    * ``inspect.BlockFinder`` over the tokenised range, so a statement never spans two
      differently-indented blocks — this is the same helper ``inspect.getsource`` uses, and it
      is what stops the *last* statement of a suite (whose "next start" is the following
      dedented statement) from swallowing the dedent;
    * a trailing walk-back over comment-only and blank lines.

    ``assertion`` is pytest's parameter and is unused there too; it is kept so the signature
    cannot drift. ``astnode`` is the re-parse cache :meth:`TracebackEntry.getsource` threads
    through, exactly as pytest does.
    """
    if astnode is None:
        content = str(source)
        # See #4260: don't produce duplicate warnings when compiling source to find AST.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            astnode = ast.parse(content, "source", "exec")

    start, end = get_statement_startend2(lineno, astnode)
    # We need to correct the end:
    # - ast-parsing strips comments
    # - there might be empty lines
    # - we might have lesser indented code blocks at the end
    if end is None:
        end = len(source.lines)

    if end > start + 1:
        # Make sure we don't span differently indented code blocks by using the BlockFinder
        # helper used which inspect.getsource() uses itself.
        block_finder = inspect.BlockFinder()
        # If we start with an indented line, put blockfinder to "started" mode.
        block_finder.started = bool(source.lines[start]) and source.lines[start][0].isspace()
        it = ((x + "\n") for x in source.lines[start:end])
        try:
            for tok in tokenize.generate_tokens(lambda: next(it)):
                block_finder.tokeneater(*tok)
        except (inspect.EndOfBlock, IndentationError):
            end = block_finder.last + start
        except Exception:  # noqa: S110 - pytest's own bare guard (l. 214-215)
            pass

    # The end might still point to a comment or empty line, correct it.
    while end:
        line = source.lines[end - 1].lstrip()
        if line.startswith("#") or not line:
            end -= 1
        else:
            break
    return astnode, start, end


def _absolutepath(path: str) -> Path:
    """Port of `_pytest/pathlib.py::absolutepath` (l. 306-311), reduced to the ``str`` case.

    ``os.path.abspath`` rather than ``Path.resolve()``: pytest is explicit that it does **not**
    want symlinks followed here, only a normalised absolute path.
    """
    return Path(os.path.abspath(path))


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

        Port of `code.py` l. 87-102. pytest returns the **string** for a non-existent file
        (``<string>``, a doctest, an ``exec``'d snippet) rather than a ``Path`` that cannot be
        opened, and :meth:`TracebackEntry.__str__` prints whichever it gets. Ported, because
        the difference shows up in the rendered line werkzeug asserts on.

        Two details this used to miss, both of them observable:

        * the path is **absolutised** (``absolutepath``, i.e. ``os.path.abspath`` — not
          ``resolve()``, so symlinks are left alone). A ``co_filename`` is relative whenever
          the module was imported through a relative path, and pytest's rendered
          ``File '<path>':<lineno>`` line is absolute in that case while rustest's was not;
        * an **empty** ``co_filename`` returns ``""`` and never touches the filesystem.
          ``Path("")`` is ``Path(".")``, which *exists*, so the old code answered the current
          directory — a directory, presented as the source file of a frame.
        """
        if not self.raw.co_filename:
            return ""
        try:
            p = _absolutepath(self.raw.co_filename)
            if not p.exists():
                raise OSError("path check failed.")
            return p
        except OSError:
            return self.raw.co_filename

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

    def getsource(self, astcache: dict[Path | str, ast.AST] | None = None) -> Source | None:
        """`TracebackEntry.getsource` (l. 284-308) — **and it is not** :attr:`statement`.

        The two differ in their *start*, and the difference is the point: ``statement`` starts
        at the failing statement, while ``source`` starts at ``getfirstlinesource()`` — the
        **``def`` line of the enclosing code object**. ``entry.source`` therefore spans the
        function header down to the end of the failing statement, which is what makes
        ``str(entry.source)`` readable as "the function, up to where it went wrong".

        rustest returned ``self.statement`` here, so ``.source`` and ``.statement`` were the
        same object and the def-line span was simply absent.

        ``astcache`` is pytest's re-parse cache, threaded into
        :func:`getstatementrange_ast` and populated on the way out; it is keyed by
        ``code.path`` and exists so rendering a deep traceback parses each file once.
        """
        source = self.frame.code.fullsource
        if source is None:
            return None
        # pytest guards `key is not None` here because its own `Code.path` was once
        # optional; ours is `Path | str` by construction (`""` for an empty `co_filename`),
        # so the guard would be dead code and the checker says so.
        key: Path | str | None = None
        astnode: ast.AST | None = None
        if astcache is not None:
            key = self.frame.code.path
            astnode = astcache.get(key, None)
        start = self.getfirstlinesource()
        try:
            astnode, _, end = getstatementrange_ast(self.lineno, source, astnode=astnode)
        except SyntaxError:
            end = self.lineno + 1
        else:
            if key is not None and astcache is not None:
                astcache[key] = astnode
        return source[start:end]

    source = property(getsource)

    def ishidden(self, excinfo: Any | None) -> bool:
        """``__tracebackhide__`` in this frame's locals or globals — l. 312-335, verbatim.

        The callable form is passed the ``ExceptionInfo`` and decides for itself, which is how
        ``raises``-style helpers hide their own frame only when the exception is the expected
        one. Every access is guarded because ``exec``/``eval`` can put objects that are not
        dictionaries in ``f_locals``/``f_globals``.
        """
        tbh: Any = False
        for maybe_ns_dct in (self.frame.f_locals, self.frame.f_globals):
            try:
                tbh = maybe_ns_dct["__tracebackhide__"]
            except Exception:  # noqa: BLE001 - pytest's own guard, see the docstring
                pass
            else:
                break
        if tbh and callable(tbh):
            return bool(tbh(excinfo))
        return bool(tbh)

    def __repr__(self) -> str:
        return f"<TracebackEntry {self.frame.code.path}:{self.lineno + 1}>"

    def __str__(self) -> str:
        """Port of l. 337-348.

        The **template** is pytest's character for character — re-verified against pytest
        8.4.2 l. 348, which is this same f-string and not the ``%r``-formatted one older
        pytests carried. pytest's own comment there is worth keeping: *"This output does not
        quite match Python's repr for traceback entries, but changing it to do so would break
        certain plugins."* It is a contract, not a rendering choice, and it is what werkzeug's
        ``assert "bb.py':1" in traceback`` matches against.

        **What the template alone does not buy**, and what the earlier "character for
        character" claim overstated: the line it renders is only pytest's if
        :attr:`statement` resolves pytest's statement range and :attr:`path` is pytest's path.
        Both were wrong here until the Phase 4 final polish wave — a frame stopped on a
        ``with``/``for``/``if`` header rendered its entire body onto this one line, and a
        relative ``co_filename`` was printed relative. An identical format string over
        different inputs is not an identical output.
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

    def filter(self, excinfo_or_fn: Any, /) -> Traceback:
        """`Traceback.filter` (l. 426-441): the entries kept, as a ``Traceback``.

        **The argument is required and positional-only, as pytest's is**, and it means one of
        two things: an ``ExceptionInfo``, which selects "everything not hidden by
        ``__tracebackhide__``" (:meth:`TracebackEntry.ishidden`), or a predicate over entries.

        rustest previously defaulted it to ``None`` = keep everything. That is not a smaller
        API, it is a **different** one: pytest's zero-argument call is a ``TypeError``, so the
        default silently turned a caller's mistake into a traceback with every internal frame
        still in it — the opposite of what ``filter()`` is for. The default was justified by
        ``_worker._visible_frames`` applying the rule elsewhere, which is true of the
        *runner's* rendering and says nothing about a test that filters a traceback itself.
        """
        from rustest.decorators import ExceptionInfo

        if isinstance(excinfo_or_fn, ExceptionInfo):
            excinfo = excinfo_or_fn

            def fn(entry: TracebackEntry) -> bool:
                return not entry.ishidden(excinfo)
        else:
            fn = excinfo_or_fn
        return Traceback(list(filter(fn, self)))

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
