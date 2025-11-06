"""Basic conftest.py file with simple fixtures."""
import rustest as testlib


@testlib.fixture
def basic_fixture():
    """A simple fixture from conftest.py."""
    return "from_conftest"


@testlib.fixture
def conftest_value():
    """Another fixture from conftest.py."""
    return 42


@testlib.fixture
def conftest_with_dependency(basic_fixture):
    """Conftest fixture that depends on another conftest fixture."""
    return f"depends_on_{basic_fixture}"


@testlib.fixture(scope="module")
def module_scoped_conftest():
    """Module-scoped fixture from conftest.py."""
    return "module_conftest"


@testlib.fixture
def conftest_yield():
    """Yield fixture in conftest.py."""
    setup_value = "setup"
    yield setup_value
    # Teardown happens here
