"""Child level conftest.py."""
import rustest


@rustest.fixture
def child_fixture():
    """Fixture from child conftest.py."""
    return "child"


@rustest.fixture
def overridable_fixture():
    """Override the root fixture."""
    return "from_child"


@rustest.fixture
def child_with_root_dep(root_fixture):
    """Child fixture that depends on root fixture."""
    return f"child_uses_{root_fixture}"


@rustest.fixture
def another_overridable():
    """Will be overridden by deepdir conftest."""
    return "from_child_level"
