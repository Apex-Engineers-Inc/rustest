"""`parametrize(argnames=..., argvalues=...)` -- pytest's own parameter names as keywords.

pytest's signature is `Metafunc.parametrize(argnames, argvalues, indirect=False, ids=None,
scope=None)` (`_pytest/python.py` l. 1163-1167), so a suite writing either as a keyword is
spelling pytest's name. rustest's first parameter is `arg_names`, and `argvalues` already
had an alias -- a completed pattern with one case missed.

MECHANISM M8 of the Phase 4 Task 1b sweep. Three FastAPI modules use `argnames=`; each
raised `TypeError` at import, and a collection error aborts the session, so one missing
keyword alias cost all 3 289 of FastAPI's collected tests.
"""

import pytest


@pytest.mark.parametrize(argnames="value", argvalues=[1, 2, 3])
def test_both_as_keywords(value):
    assert value > 0


@pytest.mark.parametrize(argnames="a,b", argvalues=[(1, 2), (3, 4)])
def test_two_names_as_keywords(a, b):
    assert b > a


@pytest.mark.parametrize("value", argvalues=[7, 8])
def test_values_keyword_with_positional_names(value):
    assert value > 6


@pytest.mark.parametrize(argnames=["x", "y"], argvalues=[(1, 2)])
def test_sequence_argnames_as_keyword(x, y):
    assert y > x


@pytest.mark.parametrize(argnames="value", argvalues=[[1], [2, 3]], ids=["one", "two"])
def test_keyword_form_obeys_the_same_unpacking_rule(value):
    """M8 and M2 interact: `argnames="value"` is a str, so it still forces a tuple."""
    assert isinstance(value, list)
