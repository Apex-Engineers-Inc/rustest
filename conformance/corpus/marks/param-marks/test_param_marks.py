"""`pytest.param(..., marks=...)` — a mark that applies to ONE parameter set.

`_pytest/mark/structures.py::ParameterSet.param` stores `tuple(marks)` and
`Metafunc.parametrize` hands each set's marks to the item built from it.
`decorators.py::ParameterSet` stored the attribute and read it nowhere -- its own comment
said "Currently not used, but stored for future support" -- so `xfail`ing a single case did
nothing and 9 Apex Member Designer tests reported `failed` where pytest reports `xfailed`
(Phase 4 Task 1 re-sweep).
"""

import pytest


@pytest.mark.parametrize(
    "x",
    [
        1,
        pytest.param(2, marks=pytest.mark.xfail(reason="known bad")),
        pytest.param(3, marks=pytest.mark.skip(reason="not this one")),
        pytest.param(4, marks=[pytest.mark.xfail(strict=True), pytest.mark.slow]),
    ],
)
def test_one_case_is_marked(x):
    assert x == 1


@pytest.mark.parametrize(
    "y", [pytest.param(1, marks=pytest.mark.xfail(raises=ValueError, strict=True))]
)
def test_xfail_raises_on_one_case(y):
    raise ValueError("expected by the mark")


@pytest.mark.parametrize("b", [10, pytest.param(20, marks=pytest.mark.xfail)])
@pytest.mark.parametrize("a", [1])
def test_marks_survive_a_cross_product(a, b):
    assert b == 10


@pytest.fixture(
    params=[
        1,
        pytest.param(2, marks=pytest.mark.xfail(reason="known", strict=False)),
        pytest.param(3, marks=pytest.mark.skip(reason="not this one")),
    ]
)
def numbered(request):
    """A **fixture's** params take marks too, and Apex Member Designer's CL scenarios are
    exactly that shape: `pytest.fixture(params=[pytest.param(s, marks=xfail(...))])`.

    pytest reaches them through the same `ParameterSet`, because
    `FixtureManager.pytest_generate_tests` hands `fixturedef.params` straight to
    `metafunc.parametrize`. rustest's `_build_fixture_params` read only `.id` and `.value`,
    so those 9 tests reported `failed` where pytest reports `xfailed`.
    """
    return request.param


def test_fixture_param_marks(numbered):
    assert numbered == 1


def test_fixture_param_marks_again(numbered):
    assert numbered in (1, 2, 3)
