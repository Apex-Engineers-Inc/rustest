"""Root level conftest.py."""
import rustest as testlib


@testlib.fixture
def root_fixture():
    """Fixture from root conftest.py."""
    return "root"


@testlib.fixture
def overridable_fixture():
    """Fixture that will be overridden by child conftest."""
    return "from_root"


@testlib.fixture
def root_only():
    """Fixture only in root conftest."""
    return "root_only_value"


@testlib.fixture(scope="session")
def nested_session_fixture():
    """Session-scoped fixture from nested test conftest."""
    return "session_from_root"
