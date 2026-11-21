"""`ExceptionInfo.traceback` is a `Traceback` -- a list of `TracebackEntry`, not a raw tb.

Port target: `_pytest/_code/code.py` -- `Traceback` (a `list` subclass), `TracebackEntry`,
`Frame`, `Code`, and `_pytest/_code/source.py::Source`.

MECHANISM M3 of the Phase 4 Task 1b sweep. rustest aliased `.traceback` to `.tb`, the raw
`types.TracebackType`, so werkzeug's `tests/test_utils.py::test_import_string_provides_traceback`
-- `"".join(str(line) for line in baz_exc.traceback)` -- died with
`TypeError: 'traceback' object is not iterable`.

The `str(entry)` spelling is a CONTRACT, not a rendering choice: pytest's own source says
changing it "would break certain plugins" (code.py l. 341-343), and werkzeug matches
`"bb.py':1"` against it. The zero-based `lineno` with a `+ 1` in `__str__` is likewise
pytest's, and moving one without the other shifts every line number a suite asserts on.
"""

import pytest


def _raises_here():
    raise ValueError("deliberate")


def test_traceback_is_a_sequence():
    with pytest.raises(ValueError) as excinfo:
        _raises_here()
    tb = excinfo.traceback
    assert len(tb) == 2
    assert list(tb) == [tb[0], tb[1]]
    assert len(tb[0:1]) == 1


def test_entry_exposes_lineno_frame_and_name():
    with pytest.raises(ValueError) as excinfo:
        _raises_here()
    entry = excinfo.traceback[-1]
    assert entry.name == "_raises_here"
    assert entry.frame.code.name == "_raises_here"
    assert entry.lineno >= 0
    assert "deliberate" in str(entry.statement)
    assert "test_exceptioninfo_traceback.py" in str(entry.path)


def test_str_of_an_entry_carries_path_line_and_source():
    with pytest.raises(ValueError) as excinfo:
        _raises_here()
    rendered = "".join(str(line) for line in excinfo.traceback)
    assert "test_exceptioninfo_traceback.py'" in rendered
    assert "in _raises_here" in rendered
    assert 'raise ValueError("deliberate")' in rendered


def test_repr_of_an_entry_is_one_based():
    with pytest.raises(ValueError) as excinfo:
        _raises_here()
    entry = excinfo.traceback[-1]
    assert repr(entry) == f"<TracebackEntry {entry.frame.code.path}:{entry.lineno + 1}>"


def test_a_multiline_statement_renders_whole():
    with pytest.raises(TypeError) as excinfo:
        int(
            "x",
            base="not-an-int",
        )
    text = str(excinfo.traceback[-1].statement)
    assert "base=" in text


def test_tb_is_still_the_raw_traceback():
    """`.tb` did not change meaning -- jinja2's `tests/test_debug.py` reads it."""
    with pytest.raises(ValueError) as excinfo:
        _raises_here()
    assert excinfo.tb.tb_next is not None
