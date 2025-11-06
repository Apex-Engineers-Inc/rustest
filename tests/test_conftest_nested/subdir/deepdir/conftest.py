"""Deep level conftest.py."""
import rustest as testlib


@testlib.fixture
def deep_fixture():
    """Fixture from deep conftest.py."""
    return "deep"


@testlib.fixture
def another_overridable():
    """Override the child fixture."""
    return "from_deep_level"


@testlib.fixture
def deep_with_chain(deep_fixture, child_fixture, root_fixture):
    """Deep fixture that depends on fixtures from multiple levels."""
    return f"{deep_fixture}_{child_fixture}_{root_fixture}"
