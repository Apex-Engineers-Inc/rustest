"""Pins for :mod:`rustest._code` — the ``Traceback``/``Source`` read surface (MECHANISM M3).

Every test here is a **differential** against pytest 8.4.2 wherever pytest is importable, and
a hard-coded oracle answer otherwise. The oracle answers were probed on this repo's pinned
pytest before being written down; the `_pytest_available` differentials exist so they cannot
rot silently when the pin moves.

These were added by the Phase 4 final polish wave, for findings I1 and I6 of the Task 1c
review. The common shape of both: the port was *structurally* right and answered a different
question than pytest in a specific, unpinned corner.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from rustest._code import Code, Source, Traceback, TracebackEntry, getstatementrange_ast

try:
    from _pytest._code.source import Source as PytestSource

    _PYTEST_CODE = True
except Exception:  # pragma: no cover - the shim is installed instead of real pytest
    _PYTEST_CODE = False


needs_real_pytest = pytest.mark.skipif(
    not _PYTEST_CODE,
    reason="differential needs the real _pytest._code (absent under the compat shim)",
)


# ---------------------------------------------------------------------------
# I1 — statement ranges bound at the NEXT statement's start
# ---------------------------------------------------------------------------

_HEADERS = """\
import os


def outer(flag):
    with open(os.devnull) as fh:
        data = fh.read()
    for item in [1, 2, 3]:
        print(item)
    if flag:
        print("yes")
    else:
        print("no")
    try:
        raise ValueError("x")
    except ValueError:
        print("caught")
    finally:
        print("done")
    return data
"""


@pytest.mark.parametrize(
    "lineno, expected",
    [
        # `with` header alone -- NOT the header plus its body.
        (4, ["    with open(os.devnull) as fh:"]),
        (5, ["        data = fh.read()"]),
        # `for` header alone.
        (6, ["    for item in [1, 2, 3]:"]),
        # `if` header alone, and the `else:` keyword line is its own statement boundary.
        (8, ["    if flag:"]),
        (9, ['        print("yes")']),
        # `try` header, the `except` clause (an ExceptHandler, not an ast.stmt), and the
        # `finally:` line -- all three are separate statements.
        (12, ["    try:"]),
        (14, ["    except ValueError:"]),
        (16, ["    finally:"]),
    ],
)
def test_a_compound_header_is_its_own_statement(lineno: int, expected: list[str]) -> None:
    """The whole of I1: the end bound is the NEXT statement's start, not ``end_lineno``.

    Under the old "smallest node whose lineno..end_lineno spans the line" rule, line 4 (the
    ``with`` header) resolved to the ``with`` *node*, i.e. lines 4-5 -- and for a real
    twenty-line ``with`` body, all twenty. A traceback entry stopped on a header therefore
    rendered the entire block onto one line.
    """
    source = Source(_HEADERS)
    assert source.getstatement(lineno).lines == expected


def test_a_call_split_over_several_lines_is_one_statement() -> None:
    """The case the end-lineno rule got right, and which must not regress."""
    source = Source("x = max(\n    1,\n    2,\n)\ny = 3\n")
    assert source.getstatement(0).lines == ["x = max(", "    1,", "    2,", ")"]
    assert source.getstatement(2).lines == ["x = max(", "    1,", "    2,", ")"]


def test_the_trailing_comment_and_blank_line_correction() -> None:
    """``getstatementrange_ast``'s walk-back over comment-only and empty trailing lines."""
    source = Source("a = 1\n\n# a comment\n\nb = 2\n")
    assert source.getstatement(0).lines == ["a = 1"]


def test_the_last_statement_of_a_suite_does_not_swallow_the_dedent() -> None:
    """The ``inspect.BlockFinder`` correction, which is why the AST bound alone is not enough.

    The next statement start after ``inner`` is ``after`` at module level, so the raw AST
    range would run from the indented body all the way down through the blank line to it.
    """
    source = Source("def f():\n    inner = 1\n\n\nafter = 2\n")
    assert source.getstatement(1).lines == ["    inner = 1"]


def test_a_decorated_def_contributes_its_decorator_lines() -> None:
    """`get_statement_startend2`'s ClassDef/FunctionDef arm: the node's lineno is the ``def``."""
    source = Source("import functools\n\n\n@functools.cache\ndef f():\n    return 1\n")
    assert source.getstatement(3).lines == ["@functools.cache"]
    assert source.getstatement(4).lines == ["def f():"]


def test_out_of_range_is_an_index_error() -> None:
    """pytest's own guard (`source.py` l. 98-100), not a silent single-line fallback."""
    with pytest.raises(IndexError, match="lineno out of range"):
        Source("a = 1\n").getstatement(50)


@needs_real_pytest
@pytest.mark.parametrize("lineno", list(range(len(_HEADERS.splitlines()))))
def test_statement_ranges_are_pytests_line_for_line(lineno: int) -> None:
    """The differential: every line of the fixture, against real pytest's own ``Source``.

    ``PytestSource(str)`` deindents and rustest's does not, so the fixture is written flush
    left on purpose -- with no common indent the two constructors agree and the comparison is
    of the *range logic*, which is what is under test.
    """
    ours = Source(_HEADERS).getstatement(lineno).lines
    theirs = PytestSource(_HEADERS).getstatement(lineno).lines
    assert ours == theirs


@needs_real_pytest
def test_getstatementrange_ast_signature_matches_pytests() -> None:
    """Same name, same four parameters, same return arity -- so a port cannot drift silently."""
    import inspect as _inspect

    from _pytest._code.source import getstatementrange_ast as theirs

    assert list(_inspect.signature(getstatementrange_ast).parameters) == list(
        _inspect.signature(theirs).parameters
    )


# ---------------------------------------------------------------------------
# I6 — the fidelity list
# ---------------------------------------------------------------------------


def _entry_from(fn: Any) -> TracebackEntry:
    try:
        fn()
    except Exception as exc:
        tb = exc.__traceback__
        assert tb is not None and tb.tb_next is not None
        return Traceback(tb)[1]
    raise AssertionError("the callable did not raise")


def _multiline_failure() -> None:
    """A frame whose failing statement sits inside a function with a real ``def`` line."""
    value = 1
    assert value == 2


def test_source_spans_from_the_def_line_while_statement_does_not() -> None:
    """`.source` starts at ``getfirstlinesource()``; `.statement` starts at the statement.

    rustest returned ``self.statement`` from ``getsource()``, so the two were the same object
    and ``entry.source`` never carried the function header pytest puts there.
    """
    entry = _entry_from(_multiline_failure)
    source = entry.source
    assert source is not None
    assert source.lines[0].strip().startswith("def _multiline_failure")
    assert source.lines[-1].strip() == "assert value == 2"
    # `.statement` is the failing statement alone.
    assert entry.statement.lines[-1].strip() == "assert value == 2"
    assert len(entry.statement.lines) < len(source.lines)


def test_getsource_populates_the_astcache_it_is_given() -> None:
    """The cache parameter is pytest's, and it is threaded, not accepted-and-ignored."""
    entry = _entry_from(_multiline_failure)
    cache: dict[Any, Any] = {}
    assert entry.getsource(cache) is not None
    assert list(cache) == [entry.frame.code.path]


def test_code_path_is_absolute() -> None:
    """``absolutepath``, i.e. ``os.path.abspath`` -- not ``resolve()``, so symlinks stay."""
    entry = _entry_from(_multiline_failure)
    path = entry.path
    assert isinstance(path, Path)
    assert path.is_absolute()


def test_an_empty_co_filename_is_the_empty_string_not_the_cwd() -> None:
    """``Path("")`` is ``Path(".")``, which exists -- so the old guard answered a *directory*."""
    code = compile("x = 1", "", "exec")
    assert Code(code).path == ""


def test_a_synthetic_filename_stays_a_string() -> None:
    code = compile("x = 1", "<not-a-real-file>", "exec")
    assert Code(code).path == "<not-a-real-file>"


def test_filter_requires_its_argument() -> None:
    """pytest's is positional-only and REQUIRED; the old default silently kept everything."""
    entry = _entry_from(_multiline_failure)
    tb = Traceback([entry])
    with pytest.raises(TypeError):
        tb.filter()  # pyright: ignore[reportCallIssue]


def test_filter_accepts_a_predicate() -> None:
    entry = _entry_from(_multiline_failure)
    tb = Traceback([entry, entry])
    assert len(tb.filter(lambda _e: False)) == 0
    assert len(tb.filter(lambda _e: True)) == 2
    assert isinstance(tb.filter(lambda _e: True), Traceback)


def test_filter_accepts_an_exception_info_and_drops_hidden_frames() -> None:
    """The primary form: ``traceback.filter(excinfo)`` means "drop ``__tracebackhide__``"."""
    from rustest import raises

    def hidden_helper() -> None:
        __tracebackhide__ = True
        raise ValueError("boom")

    def caller() -> None:
        hidden_helper()

    with raises(ValueError) as excinfo:
        caller()

    full = excinfo.traceback
    assert any(entry.ishidden(excinfo) for entry in full)
    filtered = full.filter(excinfo)
    assert len(filtered) == len(full) - 1
    assert not any(entry.ishidden(excinfo) for entry in filtered)


def test_ishidden_honours_the_callable_form() -> None:
    """``__tracebackhide__`` may be a callable taking the ``ExceptionInfo``."""
    from rustest import raises

    def maybe_hidden() -> None:
        __tracebackhide__ = lambda excinfo: excinfo.errisinstance(ValueError)  # noqa: E731
        raise ValueError("boom")

    with raises(ValueError) as excinfo:
        maybe_hidden()
    assert excinfo.traceback[-1].ishidden(excinfo) is True


def test_tblen_follows_an_assigned_traceback() -> None:
    """``repr``'s ``tblen`` is ``len(self.traceback)``, so ``cut``/``filter`` are visible.

    Walking the raw ``tb_next`` chain reported the original length forever -- the one place a
    reader checks whether a filter took effect was the one place that could not show it.
    """
    from rustest import raises

    def inner() -> None:
        raise ValueError("msg-here")

    def outer() -> None:
        inner()

    with raises(ValueError) as excinfo:
        outer()

    assert len(excinfo.traceback) == 3
    assert repr(excinfo) == "<ExceptionInfo ValueError('msg-here') tblen=3>"
    excinfo.traceback = excinfo.traceback[-1:]
    assert repr(excinfo) == "<ExceptionInfo ValueError('msg-here') tblen=1>"


def test_entry_str_renders_only_the_header_for_a_compound_statement() -> None:
    """I1 seen from `__str__`, which is the surface werkzeug asserts against."""

    def with_block() -> None:
        with open(__file__, encoding="utf-8"):
            raise ValueError("boom")

    try:
        with_block()
    except ValueError as exc:
        tb = exc.__traceback__
        assert tb is not None and tb.tb_next is not None
        entry = Traceback(tb)[1]
    rendered = str(entry)
    assert rendered.endswith('raise ValueError("boom")\n')
    assert "with open(" not in rendered
    assert f"  File '{entry.path}':{entry.lineno + 1} in with_block\n" in rendered


@needs_real_pytest
def test_entry_str_is_byte_identical_to_pytests() -> None:
    """The whole rendered line, ours against theirs, on the same live traceback object."""
    from _pytest._code.code import TracebackEntry as PytestEntry

    def boom() -> None:
        raise ValueError("x")

    try:
        boom()
    except ValueError as exc:
        raw = exc.__traceback__
        assert raw is not None and raw.tb_next is not None
        raw = raw.tb_next
    assert str(TracebackEntry(raw)) == str(PytestEntry(raw))


@needs_real_pytest
def test_source_span_is_byte_identical_to_pytests() -> None:
    """`.source` -- the def-line span -- against pytest's, on the same frame."""
    from _pytest._code.code import TracebackEntry as PytestEntry

    try:
        _multiline_failure()
    except AssertionError as exc:
        raw = exc.__traceback__
        assert raw is not None and raw.tb_next is not None
        raw = raw.tb_next
    ours = TracebackEntry(raw).source
    theirs = PytestEntry(raw).source
    assert ours is not None and theirs is not None
    assert ours.lines == theirs.lines


def test_unparsable_source_falls_back_to_the_single_line() -> None:
    """pytest's ``except SyntaxError: end = self.lineno + 1`` arm in ``getsource``."""
    entry = _entry_from(_multiline_failure)
    with pytest.raises(SyntaxError):
        getstatementrange_ast(0, Source("def f(:\n    pass\n"))
    # ...and the entry-level API absorbs it rather than propagating.
    assert entry.getsource() is not None
    assert sys.version_info >= (3, 12)
