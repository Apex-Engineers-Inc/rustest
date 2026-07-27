"""``rustest.raises`` against pytest's own, shape by shape.

Oracles: `_pytest/raises.py::raises` (pytest 8.4.2, l. 104-300 — the module `python_api.py`
delegates to since 8.4), `_pytest/raises.py::RaisesExc` (l. 543-732) and
`_pytest/_code/code.py::ExceptionInfo` (l. 604-781).

Every expectation was **measured** first: `scratchpad/probe/raises` runs the same 20 shapes
under pytest and under rustest and prints both (see the Phase 4 Task 1 report §2).  What the
probe found, and what this file pins:

* the *legacy callable* form ``raises(Exc, func, *args, **kwargs)`` was simply absent —
  rustest's signature was ``raises(exc_type, *, match=None)``, so jinja2's 41 uses of the
  callable form died with ``TypeError: raises() takes 1 positional argument``;
* ``ExceptionInfo`` had ``.value``/``.type`` and nothing else — no ``.tb``, ``.typename``,
  ``.match()``, ``.exconly()``, ``.errisinstance()``, and ``__enter__`` handed back the
  *context manager* rather than an ``ExceptionInfo``;
* ``match=`` compared against ``str(exc)`` where pytest compares against
  ``stringify_exception`` — i.e. the message **plus PEP-678 ``__notes__``**.

Known, deliberate divergence: a failed ``raises`` raises ``AssertionError`` where pytest
raises ``_pytest.outcomes.Failed`` (a *BaseException*).  Both report the test as ``failed``
with the same text, which is all any of the four real suites can observe; the type is
recorded in the report rather than changed, because rustest's ``Failed`` derives from
``Exception`` and swapping it would change what a user's ``except Exception`` catches.
"""

from __future__ import annotations

import re
from typing import Any, NoReturn

import pytest

from rustest import ExceptionInfo, raises


def boom(*args: Any, **kwargs: Any) -> NoReturn:
    raise ValueError(f"boom args={args} kwargs={sorted(kwargs)}")


def quiet(*args: Any, **kwargs: Any) -> str:
    return "no-raise"


# --------------------------------------------------------------------------- legacy form


def test_legacy_callable_form_returns_exception_info() -> None:
    excinfo = raises(ValueError, boom)
    assert isinstance(excinfo, ExceptionInfo)
    assert excinfo.type is ValueError
    assert str(excinfo.value) == "boom args=() kwargs=[]"


def test_legacy_callable_form_forwards_positional_arguments() -> None:
    excinfo = raises(ValueError, boom, 1, 2)
    assert str(excinfo.value) == "boom args=(1, 2) kwargs=[]"


def test_legacy_callable_form_forwards_keyword_arguments() -> None:
    excinfo = raises(ValueError, boom, x=1)
    assert str(excinfo.value) == "boom args=() kwargs=['x']"


def test_legacy_callable_form_forwards_match_to_the_function() -> None:
    """`_pytest/raises.py` l. 296: with a ``func`` present, **kwargs go to ``func``."""
    excinfo = raises(ValueError, boom, match="zz")
    assert str(excinfo.value) == "boom args=() kwargs=['match']"


def test_legacy_callable_form_accepts_a_tuple_of_types() -> None:
    excinfo = raises((ValueError, TypeError), boom)
    assert excinfo.type is ValueError


def test_legacy_callable_form_that_does_not_raise() -> None:
    with pytest.raises(AssertionError) as outer:
        raises(ValueError, quiet)
    assert str(outer.value) == "DID NOT RAISE <class 'ValueError'>"


def test_legacy_callable_form_rejects_a_non_callable() -> None:
    with pytest.raises(TypeError) as outer:
        raises(ValueError, 3)  # type: ignore[arg-type]
    assert str(outer.value) == "3 object (type: <class 'int'>) must be callable"


def test_legacy_callable_form_rejects_a_falsy_exception_type() -> None:
    with pytest.raises(ValueError) as outer:
        raises(None, boom)  # type: ignore[arg-type]
    assert str(outer.value).startswith(
        "Expected an exception type or a tuple of exception types, but got `None`."
    )


# ------------------------------------------------------------------- context-manager form


def test_unknown_keyword_in_context_manager_form() -> None:
    with pytest.raises(TypeError) as outer:
        raises(ValueError, unknown=1)  # type: ignore[call-overload]
    assert str(outer.value) == (
        "Unexpected keyword arguments passed to pytest.raises: unknown"
        "\nUse context-manager form instead?"
    )


def test_context_manager_without_a_type_matches_on_match_alone() -> None:
    with raises(match="wanted"):
        raise RuntimeError("wanted this")


def test_context_manager_with_no_parameters_at_all_is_a_value_error() -> None:
    with pytest.raises(ValueError) as outer:
        raises()
    assert str(outer.value) == "You must specify at least one parameter to match on."


def test_check_callable_is_honoured() -> None:
    with raises(ValueError, check=lambda exc: exc.args == (7,)):
        raise ValueError(7)


def test_check_callable_that_returns_false_fails() -> None:
    with pytest.raises(AssertionError) as outer:
        with raises(ValueError, check=lambda exc: False):
            raise ValueError(7)
    assert "did not return True" in str(outer.value)


def test_wrong_exception_type_propagates_unchanged() -> None:
    with pytest.raises(TypeError, match="^other$"):
        with raises(ValueError):
            raise TypeError("other")


def test_wrong_type_propagates_even_when_match_is_given() -> None:
    with pytest.raises(TypeError, match="^other$"):
        with raises(ValueError, match="anything"):
            raise TypeError("other")


# ------------------------------------------------------------------------- the failure text


def test_did_not_raise_single_type_message() -> None:
    with pytest.raises(AssertionError) as outer:
        with raises(ValueError):
            pass
    assert str(outer.value) == "DID NOT RAISE <class 'ValueError'>"


def test_did_not_raise_multiple_types_message() -> None:
    with pytest.raises(AssertionError) as outer:
        with raises((ValueError, TypeError)):
            pass
    assert str(outer.value) == ("DID NOT RAISE any of (<class 'ValueError'>, <class 'TypeError'>)")


def test_did_not_raise_with_no_expected_type_message() -> None:
    with pytest.raises(AssertionError) as outer:
        with raises(match="x"):
            pass
    assert str(outer.value) == "DID NOT RAISE any exception"


def test_match_failure_message_is_pytests() -> None:
    with pytest.raises(AssertionError) as outer:
        with raises(ValueError, match="expected-pattern"):
            raise ValueError("actual message")
    assert str(outer.value) == (
        "Regex pattern did not match.\n Regex: 'expected-pattern'\n Input: 'actual message'"
    )


def test_match_failure_message_unwraps_a_compiled_pattern() -> None:
    with pytest.raises(AssertionError) as outer:
        with raises(ValueError, match=re.compile("expected-pattern")):
            raise ValueError("actual message")
    assert str(outer.value) == (
        "Regex pattern did not match.\n Regex: 'expected-pattern'\n Input: 'actual message'"
    )


def test_match_considers_pep_678_notes() -> None:
    """`_pytest/_code/code.py::stringify_exception` joins the message with ``__notes__``."""
    with raises(ValueError, match="note-text"):
        exc = ValueError("plain")
        exc.add_note("note-text")
        raise exc


# ------------------------------------------------------------------------ ExceptionInfo


def test_enter_yields_an_exception_info() -> None:
    with raises(ValueError) as excinfo:
        raise ValueError("msg-here")
    assert isinstance(excinfo, ExceptionInfo)


def test_exception_info_attribute_surface() -> None:
    with raises(ValueError) as excinfo:
        raise ValueError("msg-here")
    assert excinfo.type is ValueError
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.typename == "ValueError"
    assert excinfo.tb is excinfo.value.__traceback__
    assert excinfo.traceback is excinfo.tb
    assert excinfo.exconly() == "ValueError: msg-here"
    assert excinfo.errisinstance(ValueError) is True
    assert excinfo.errisinstance(TypeError) is False


def test_exception_info_repr_matches_pytests_shape() -> None:
    with raises(ValueError) as excinfo:
        raise ValueError("msg-here")
    assert repr(excinfo) == "<ExceptionInfo ValueError('msg-here') tblen=1>"


def test_exception_info_before_the_block_exits() -> None:
    with raises(ValueError) as excinfo:
        assert repr(excinfo) == "<ExceptionInfo for raises contextmanager>"
        with pytest.raises(AssertionError) as outer:
            _ = excinfo.value
        assert str(outer.value).startswith(".value can only be used after")
        raise ValueError("x")


def test_exception_info_match_returns_true() -> None:
    with raises(ValueError) as excinfo:
        raise ValueError("msg-here")
    assert excinfo.match("msg") is True


def test_exception_info_match_failure_is_pytests_message() -> None:
    with raises(ValueError) as excinfo:
        raise ValueError("msg-here")
    with pytest.raises(AssertionError) as outer:
        excinfo.match("nope")
    assert str(outer.value) == ("Regex pattern did not match.\n Regex: 'nope'\n Input: 'msg-here'")


def test_context_object_still_exposes_value_and_type() -> None:
    """``RaisesContext`` is public API; the pre-Phase-4 accessors keep working."""
    ctx = raises(ValueError)
    with ctx:
        raise ValueError("boom")
    assert ctx.type is ValueError
    assert str(ctx.value) == "boom"
    assert ctx.excinfo is not None


def test_context_object_before_exit_still_raises_attribute_error() -> None:
    ctx = raises(ValueError)
    with pytest.raises(AttributeError, match="No exception was caught"):
        _ = ctx.value
