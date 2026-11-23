"""`pytest.raises` in its legacy callable form, and the ExceptionInfo it returns.

`_pytest/raises.py::raises` (l. 275-300) takes a second positional `func` and calls it
immediately, returning the `ExceptionInfo` rather than a context manager -- and `**kwargs`
in that form go to `func`, not to `raises`. jinja2 uses the shape 41 times and reads
`.tb`/`.match()` in four more, which cost that suite 50 tests before Phase 4 (Phase 3
Task 4 report, section 4.5).
"""

import re

import pytest


def boom(*args, **kwargs):
    raise ValueError(f"boom args={args} kwargs={sorted(kwargs)}")


def test_legacy_callable_form():
    excinfo = pytest.raises(ValueError, boom)
    assert excinfo.type is ValueError
    assert str(excinfo.value) == "boom args=() kwargs=[]"


def test_legacy_callable_form_forwards_arguments():
    excinfo = pytest.raises(ValueError, boom, 1, 2, extra=3)
    assert str(excinfo.value) == "boom args=(1, 2) kwargs=['extra']"


def test_exception_info_read_surface():
    with pytest.raises(ValueError) as excinfo:
        raise ValueError("msg-here")
    assert excinfo.typename == "ValueError"
    assert excinfo.tb is excinfo.value.__traceback__
    assert excinfo.exconly() == "ValueError: msg-here"
    assert excinfo.errisinstance(ValueError)
    assert excinfo.match("msg")


def test_match_searches_notes_too():
    with pytest.raises(ValueError, match="note-text"):
        exc = ValueError("plain")
        exc.add_note("note-text")
        raise exc


def test_match_accepts_a_compiled_pattern():
    with pytest.raises(ValueError, match=re.compile("lit.ral")):
        raise ValueError("literal")


def test_a_wrong_type_propagates():
    with pytest.raises(TypeError):
        with pytest.raises(ValueError):
            raise TypeError("other")
