"""Root conftest.py with fixtures using the name parameter."""
import rustest as testlib


@testlib.fixture(name="db")
def _database_connection():
    """A fixture with a custom name 'db'."""
    return {"connected": True, "name": "test_db"}


@testlib.fixture(name="api_client")
def _create_api_client():
    """A fixture with a custom name 'api_client'."""
    return {"endpoint": "http://test.api.local", "auth": "bearer"}


@testlib.fixture
def regular_fixture():
    """A regular fixture without custom name."""
    return "regular_value"


@testlib.fixture(name="custom_name")
def different_function_name():
    """Fixture where function name differs from registered name."""
    return "custom_name_value"
