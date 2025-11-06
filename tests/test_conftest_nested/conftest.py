"""Root level conftest.py."""
# Support both pytest and rustest
try:
    import pytest as testlib
except ImportError:
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
def session_fixture():
    """Session-scoped fixture from root conftest."""
    return "session_from_root"
