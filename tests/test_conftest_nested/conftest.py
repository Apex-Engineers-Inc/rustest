"""Root level conftest.py."""
import rustest


@rustest.fixture
def root_fixture():
    """Fixture from root conftest.py."""
    return "root"


@rustest.fixture
def overridable_fixture():
    """Fixture that will be overridden by child conftest."""
    return "from_root"


@rustest.fixture
def root_only():
    """Fixture only in root conftest."""
    return "root_only_value"


@rustest.fixture(scope="session")
def session_fixture():
    """Session-scoped fixture from root conftest."""
    return "session_from_root"
