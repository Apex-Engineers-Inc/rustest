"""The `recwarn` builtin fixture -- a `WarningsRecorder` entered for the whole test.

Port target: `_pytest/recwarn.py::recwarn` (l. 33-40) and `WarningsRecorder` (l. 168-255).

MECHANISM M5 of the Phase 4 Task 1b sweep. `recwarn` was listed in the worker's
`UNSUPPORTED_BUILTIN_FIXTURES` on the recorded grounds that it "needs a warnings channel,
which the v2 wire does not have". It does not: it records in-process, inside the test's own
call phase, and never reports anything to the orchestrator. attrs'
`tests/test_packaging.py::TestLegacyMetadataHack` reads `recwarn.list` and cost 4 tests.

`pytest.warns` is the SAME recorder -- pytest spells it `class WarningsChecker(
WarningsRecorder)` -- so the last test here pins that the two agree.
"""

import warnings

import pytest


def test_recwarn_starts_empty(recwarn):
    """attrs' exact assertion."""
    assert [] == recwarn.list
    assert len(recwarn) == 0


def test_recwarn_records_the_body_s_warnings(recwarn):
    warnings.warn("hello", UserWarning)
    assert len(recwarn.list) == 1
    assert recwarn.list[0].category is UserWarning
    assert "hello" in str(recwarn[0].message)


def test_recwarn_is_iterable_and_indexable(recwarn):
    warnings.warn("one", UserWarning)
    warnings.warn("two", FutureWarning)
    assert [w.category for w in recwarn] == [UserWarning, FutureWarning]
    assert recwarn[1].category is FutureWarning


def test_recwarn_pop_removes_and_returns(recwarn):
    warnings.warn("dep", DeprecationWarning)
    warnings.warn("user", UserWarning)
    popped = recwarn.pop(DeprecationWarning)
    assert "dep" in str(popped.message)
    assert len(recwarn) == 1


def test_recwarn_pop_without_a_match_raises(recwarn):
    with pytest.raises(AssertionError):
        recwarn.pop(FutureWarning)


def test_recwarn_pop_prefers_an_exact_category_match(recwarn):
    """`recwarn.py` l. 206-223: an EXACT category match wins immediately and stops the scan.

    The subclass was raised first, so a naive "first thing that passes `issubclass`" would
    hand back the `DeprecationWarning`. pytest hands back the exact `Warning`.
    """
    warnings.warn("subclass first", DeprecationWarning)
    warnings.warn("exact second", Warning)
    popped = recwarn.pop(Warning)
    assert popped.category is Warning
    assert "exact second" in str(popped.message)
    assert len(recwarn) == 1


def test_recwarn_pop_tie_break_among_sibling_categories(recwarn):
    """Two OVERLAPPING inexact matches, and the answer is NOT "the first one".

    `pop`'s rule (l. 206-219) is *"the first recorded warning which is an instance of `cls`,
    but not an instance of a child class of any other match"*, implemented as a running best
    that is replaced whenever the next candidate is **not** a subclass of the current best.
    `DeprecationWarning` and `UserWarning` are siblings, so neither is a subclass of the
    other, so the LAST sibling replaces the first and wins.

    Written the other way round first, from the docstring alone, and the differential
    against real pytest corrected it -- which is the reason this pair is in the graded corpus
    rather than in a unit test.
    """
    warnings.warn("first", DeprecationWarning)
    warnings.warn("second", UserWarning)
    popped = recwarn.pop(Warning)
    assert popped.category is UserWarning
    assert "second" in str(popped.message)


def test_recwarn_pop_prefers_a_base_over_its_own_subclass_either_way_round(recwarn):
    """With a real subclass relation, the BASE wins from both orders -- that is the rule.

    Sibling order is incidental (above); this is the property `pop` actually encodes, and it
    is order-independent, which is what makes it a rule rather than an artifact.
    """

    class Derived(UserWarning):
        pass

    warnings.warn("derived first", Derived)
    warnings.warn("base second", UserWarning)
    assert recwarn.pop(Warning).category is UserWarning

    recwarn.clear()
    warnings.warn("base first", UserWarning)
    warnings.warn("derived second", Derived)
    assert recwarn.pop(Warning).category is UserWarning


def test_recwarn_clear_empties_in_place(recwarn):
    warnings.warn("x", UserWarning)
    recwarn.clear()
    assert recwarn.list == []


def test_warns_yields_the_same_kind_of_recorder():
    with pytest.warns(UserWarning) as rec:
        warnings.warn("in-block", UserWarning)
    assert len(rec) == 1
    assert len(rec.list) == 1
    assert "in-block" in str(rec[0].message)
