"""Fixture instantiation order, pinned against what pytest actually does.

**Why this file was re-authored.** It arrived from `main` claiming to test that
"higher-scoped fixtures resolve before lower-scoped autouse fixtures", and it did not test
that at all: its autouse fixture *depended on* its session fixture, and a dependency edge
forces the session fixture first under any ordering policy whatsoever. The assertions were
tautologies with respect to the question in the docstring — they would have passed against
an implementation that got the ordering exactly backwards.

`RELEASE-CHECKLIST.md` §4 item 2 flagged the file as asserting "the opposite of pytest's
ordering" and predicted a false *failure*. That prediction was wrong -- it passes -- but the
concern was right: it was not pinning anything.

**The real order, measured** on pytest 8.4.2 with four fixtures covering both axes
(`s_auto`, `f_auto`, `s_req`, `f_req`):

    ['s_auto', 's_req', 'f_auto', 'f_req']

So **scope is the primary key** (session before function) and **autouse is the secondary
key** *within* a scope (autouse before requested). Note the checklist's parenthetical had
these the other way round.

The same four-fixture file run under rustest produces the identical list, which is what
these tests pin. Every test here runs under both runners, so a divergence in either
direction is a failure rather than a silent difference.
"""

from rustest import fixture

_init_order: list[str] = []
_session_inits: list[str] = []


@fixture(scope="session", autouse=True)
def order_session_autouse():
    _init_order.append("s_auto")


@fixture(scope="session")
def order_session_requested():
    _init_order.append("s_req")
    _session_inits.append("once")
    return {"initialized": True}


@fixture(autouse=True)
def order_function_autouse():
    _init_order.append("f_auto")


@fixture
def order_function_requested():
    _init_order.append("f_req")
    return "f_req_value"


def test_scope_outranks_autouse_and_parameter_order(
    order_function_requested: str,
    order_session_requested: dict[str, bool],
) -> None:
    """The four fixtures resolve session-first, autouse-before-requested within a scope.

    The two requested fixtures are declared on this test **function-first**, deliberately:
    if parameter order drove instantiation, `f_req` would come before `s_req`. It does not.
    Scope does.
    """
    assert order_session_requested["initialized"] is True
    assert order_function_requested == "f_req_value"

    # Only this test's own slice -- a second test appends its own function-scoped entries.
    assert _init_order[:4] == ["s_auto", "s_req", "f_auto", "f_req"]


def test_session_fixtures_are_built_once_and_function_ones_repeat(
    order_function_requested: str,
    order_session_requested: dict[str, bool],
) -> None:
    """The half of the original file that *was* meaningful, kept.

    A session fixture is instantiated once for the whole worker; the function-scoped ones
    run again for every test. (Session scope is per **worker**, not per run -- see the
    "Known gaps" entry in the changelog -- which is why this counts inits recorded by this
    module rather than asserting anything about other files.)
    """
    assert _session_inits == ["once"]
    assert _init_order.count("s_auto") == 1
    assert _init_order.count("s_req") == 1

    # Two tests have now run, so both function-scoped fixtures have run twice.
    assert _init_order.count("f_auto") == 2
    assert _init_order.count("f_req") == 2
