"""`indirect=` parametrization: values routed through a same-named fixture.

`_pytest/python.py::Metafunc._resolve_args_directness` (l. 1417-1454) splits the
parametrized names into "direct" and "indirect"; an indirect one keeps its fixturedefs in
the closure (`_get_direct_parametrize_args` passes only the *direct* ones to
`getfixtureclosure`'s ignore list), its value reaches that fixture as `request.param`, and
the test receives whatever the fixture returns. Ids are generated from the parameter either
way, so making a name indirect must not move a single node id.

rustest's `indirect=` used to mean something else entirely -- the value was read as the
*name of a fixture to resolve* -- which is why Apex Member Designer's `TestAPIEndpoints`
failed 120 tests with `AttributeError: 'str' object has no attribute 'model_dump'` and why
this repo's own `pyproject.toml` carried `--ignore=tests/test_indirect_parametrization.py`
(Phase 3 Task 4 report, section 5).
"""

import pytest


@pytest.fixture
def doubled(request):
    return request.param * 2


@pytest.fixture
def labelled(request):
    return f"<{request.param}>"


@pytest.fixture
def alpha():
    return "A"


@pytest.fixture
def beta():
    return "B"


@pytest.fixture
def chosen(request):
    return request.getfixturevalue(request.param)


@pytest.mark.parametrize("doubled", [3, 5], indirect=["doubled"])
def test_partial_list(doubled):
    assert doubled in (6, 10)


@pytest.mark.parametrize("doubled", [3, 5], indirect=True)
def test_indirect_true(doubled):
    assert doubled in (6, 10)


@pytest.mark.parametrize("plain,labelled", [(1, "a"), (2, "b")], indirect=["labelled"])
def test_mixed(plain, labelled):
    assert plain in (1, 2)
    assert labelled in ("<a>", "<b>")


@pytest.mark.parametrize("chosen", ["alpha", "beta"], indirect=True)
def test_getfixturevalue_shape(chosen):
    assert chosen in ("A", "B")


@pytest.mark.parametrize("labelled", [1, 2], indirect=True)
@pytest.mark.parametrize("plain", ["x", "y"])
def test_stacked(labelled, plain):
    assert labelled in ("<1>", "<2>")
    assert plain in ("x", "y")


@pytest.mark.parametrize("label,chosen", [("a", "alpha"), ("b", "beta")], indirect=["chosen"])
class TestClassLevelIndirect:
    """A class-level `@parametrize(..., indirect=[...])` -- Member Designer's shape.

    `decorators.py::parametrize` writes its metadata onto whatever it decorates, so a
    class-level `indirect=` lands on the *class object*, where a method cannot see it
    (functions do not inherit class attributes). Reading only the function's own metadata
    left Apex Member Designer's 240 `TestAPIEndpoints` cases receiving the raw string:
    `AttributeError: 'str' object has no attribute 'model_dump'`.
    """

    def test_one(self, label, chosen):
        assert chosen in ("A", "B")

    def test_two(self, label, chosen):
        assert chosen in ("A", "B")
