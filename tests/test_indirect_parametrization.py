"""``indirect=`` parametrization — **pytest's**, since Phase 4 Task 1.

Before Phase 4 this file tested a rustest-only feature that borrowed pytest's keyword: an
indirect value was read as *the name of a fixture to resolve*.  No pytest suite could use
it, `pyproject.toml` had to `--ignore` this file so pytest would not fail on it, and Apex
Member Designer's ``TestAPIEndpoints`` — which writes the ordinary pytest shape — lost 120
tests to ``AttributeError: 'str' object has no attribute 'model_dump'``.

The semantics here are now `_pytest/python.py::Metafunc._resolve_args_directness`
(l. 1417-1454): an indirect argname keeps its fixturedefs, the parametrized value is handed
to that fixture as ``request.param``, and the test receives whatever the fixture returns.
Node ids are unaffected — they are generated from the parameter either way.

Runs under **both** runners, which is the point: every assertion below was diffed against
pytest 8.4.2 before it was written (`scratchpad/probe/ind`, 11 modules). The *refusals*
(a string ``indirect``, an unknown name, a non-Sequence) live in
`python/tests/test_indirect_parametrization.py` instead, because rustest raises them at
decoration and pytest at collection — the same collection error and the same exit 2, but
not at a moment one file can assert in both runners.
"""

import os

from rustest import fixture, parametrize

#: True only under the **legacy** engine, which still reads an indirect value as the name of
#: a fixture to resolve and is deleted in Phase 4 Task 2 rather than re-taught.
#: ``RUSTEST_RUNNING`` is set by both engines; only the v2 worker also sets
#: ``RUSTEST_ENGINE=v2`` (`src/v2/collect.rs` l. 306-315). Plain pytest sets neither, so it
#: runs everything below.
_UNDER_V1 = bool(os.environ.get("RUSTEST_RUNNING")) and os.environ.get("RUSTEST_ENGINE") != "v2"


@fixture
def doubled(request):
    """The canonical shape: the parameter arrives as ``request.param``."""
    return request.param * 2


@fixture
def labelled(request):
    return f"<{request.param}>"


@fixture
def data_1():
    return {"name": "fixture_1", "value": 42}


@fixture
def data_2():
    return {"name": "fixture_2", "value": 100}


@fixture
def chosen(request):
    """Apex Member Designer's shape: the parameter names another fixture."""
    return request.getfixturevalue(request.param)


@parametrize("doubled", [3, 5], indirect=["doubled"])
def test_indirect_as_list(doubled):
    """A list names which parameters are routed; the rest stay direct."""
    assert doubled in (6, 10)


@parametrize("doubled", [3, 5], indirect=True)
def test_indirect_true(doubled):
    """``True`` routes every parametrized name."""
    assert doubled in (6, 10)


@parametrize("plain,labelled", [(1, "a"), (2, "b")], indirect=["labelled"])
def test_mixed_indirect_direct(plain, labelled):
    """Direct and indirect names in one ``parametrize``, and the id keeps both parts."""
    assert plain in (1, 2)
    assert labelled in ("<a>", "<b>")


@parametrize("chosen", ["data_1", "data_2"], indirect=True)
def test_getfixturevalue_through_request_param(chosen):
    """``request.getfixturevalue(request.param)`` — the Member Designer pattern."""
    assert chosen["value"] in (42, 100)


@parametrize("labelled", [1, 2], indirect=True)
@parametrize("plain", ["x", "y"])
def test_stacked_decorators(labelled, plain):
    """Stacking crosses the two, and only the inner name is routed."""
    assert labelled in ("<1>", "<2>")
    assert plain in ("x", "y")


# Skipped rather than marked: v1 honours neither a module-level ``pytestmark`` nor an
# ``@mark.skipif`` decorator (probed -- both run the body anyway), but it does read
# ``__rustest_skip__``, which is what ``rustest.skip`` sets. Under pytest and under v2 the
# condition is False and nothing here executes.
if _UNDER_V1:  # pragma: no cover - legacy engine only, deleted with v1
    for _name, _obj in list(globals().items()):
        if _name.startswith("test_"):
            _obj.__rustest_skip__ = "the legacy engine has rustest's pre-0.18 indirect="
