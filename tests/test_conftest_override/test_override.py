"""Test that module fixtures override conftest fixtures."""
import rustest


@rustest.fixture
def shared_name():
    """Module-level fixture overrides conftest fixture."""
    return "from_module"


@rustest.fixture
def another_shared():
    """Another module-level override."""
    return "module_version"


@rustest.fixture
def module_only():
    """Module-only fixture."""
    return "module_value"


def test_uses_module_override(shared_name):
    """Test that module fixture takes precedence over conftest."""
    assert shared_name == "from_module"


def test_uses_conftest_only(conftest_only):
    """Test can still access conftest-only fixtures."""
    assert conftest_only == "conftest_value"


def test_another_module_override(another_shared):
    """Test another module override."""
    assert another_shared == "module_version"


def test_module_only_fixture(module_only):
    """Test can access module-only fixtures."""
    assert module_only == "module_value"


def test_combined(shared_name, conftest_only, another_shared, module_only):
    """Test using both module and conftest fixtures."""
    assert shared_name == "from_module"
    assert conftest_only == "conftest_value"
    assert another_shared == "module_version"
    assert module_only == "module_value"
