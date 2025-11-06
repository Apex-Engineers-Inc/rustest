"""Conftest with fixtures of different scopes."""
import rustest


@rustest.fixture(scope="function")
def function_fixture():
    """Function-scoped fixture - created for each test."""
    return "function_value"


@rustest.fixture(scope="class")
def class_fixture():
    """Class-scoped fixture - shared within a test class."""
    return "class_value"


@rustest.fixture(scope="module")
def module_fixture():
    """Module-scoped fixture - shared within a module."""
    return "module_value"


@rustest.fixture(scope="session")
def session_fixture():
    """Session-scoped fixture - shared across all tests."""
    return "session_value"


@rustest.fixture
def fixture_with_dep(session_fixture):
    """Fixture that depends on session fixture."""
    return f"depends_on_{session_fixture}"
