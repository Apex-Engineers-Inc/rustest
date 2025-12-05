"""Child conftest.py that uses parent named fixtures and adds its own."""
import rustest as testlib


@testlib.fixture(name="child_named")
def _child_named_fixture():
    """Child fixture with custom name."""
    return "child_named_value"


@testlib.fixture
def child_uses_parent_named(db):
    """Child fixture that depends on parent's named fixture."""
    return f"child_using_{db['name']}"
