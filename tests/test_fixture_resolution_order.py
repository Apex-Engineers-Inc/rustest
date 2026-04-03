"""Test that fixture resolution follows scope ordering.

Higher-scoped fixtures (session) should resolve before lower-scoped (function)
autouse fixtures, even when the session fixtures are only requested via
test parameters (not autouse).
"""
from rustest import fixture


_init_order: list[str] = []


@fixture(scope="session")
def session_resource():
    """Session-scoped fixture that initializes a shared resource."""
    _init_order.append("session")
    return {"initialized": True}


@fixture(autouse=True)
def function_cleanup(session_resource):
    """Function-scoped autouse that depends on session resource."""
    _init_order.append("function_autouse")
    assert session_resource["initialized"], "Session resource should be initialized"
    yield
    # cleanup


def test_order_first(session_resource):
    """First test - session resource should init before function autouse."""
    assert "session" in _init_order
    idx_session = _init_order.index("session")
    idx_function = len(_init_order) - 1  # Last function_autouse
    assert idx_session < idx_function


def test_order_second(session_resource):
    """Second test - session resource cached, function autouse still runs."""
    assert _init_order.count("session") == 1  # Only initialized once
    assert _init_order.count("function_autouse") >= 2  # Runs each test
