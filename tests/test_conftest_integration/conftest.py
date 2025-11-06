"""Integration test conftest combining multiple features."""
import rustest as testlib


@testlib.fixture(scope="session")
def db_connection():
    """Simulate a database connection."""
    connection = {"connected": True, "queries": []}
    yield connection
    # Cleanup on teardown
    connection["connected"] = False


@testlib.fixture(scope="module")
def db_session(db_connection):
    """Simulate a database session."""
    session = {"connection": db_connection, "transaction": "open"}
    yield session
    session["transaction"] = "closed"


@testlib.fixture
def db_cursor(db_session):
    """Simulate a database cursor."""
    cursor = {"session": db_session, "position": 0}
    yield cursor
    cursor["position"] = -1


@testlib.fixture
def sample_data():
    """Provide sample test data."""
    return {"users": ["alice", "bob"], "count": 2}


@testlib.fixture
def data_processor(sample_data):
    """Process sample data."""
    def process(transform):
        return [transform(x) for x in sample_data["users"]]
    return process


@testlib.fixture
def authenticated_user():
    """Simulate an authenticated user."""
    user = {"name": "testuser", "authenticated": True}
    yield user
    user["authenticated"] = False
