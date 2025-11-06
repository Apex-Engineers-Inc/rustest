"""Basic conftest.py file with simple fixtures."""
import rustest


@rustest.fixture
def basic_fixture():
    """A simple fixture from conftest.py."""
    return "from_conftest"


@rustest.fixture
def conftest_value():
    """Another fixture from conftest.py."""
    return 42


@rustest.fixture
def conftest_with_dependency(basic_fixture):
    """Conftest fixture that depends on another conftest fixture."""
    return f"depends_on_{basic_fixture}"


@rustest.fixture(scope="module")
def module_scoped_conftest():
    """Module-scoped fixture from conftest.py."""
    return "module_conftest"


@rustest.fixture
def conftest_yield():
    """Yield fixture in conftest.py."""
    setup_value = "setup"
    yield setup_value
    # Teardown happens here
