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
