"""Integration tests using multiple conftest fixtures."""
import rustest


@rustest.fixture
def local_fixture(sample_data):
    """Local fixture that uses conftest fixture."""
    return f"local_with_{sample_data['count']}_items"


def test_db_stack(db_connection, db_session, db_cursor):
    """Test using the full database fixture stack."""
    assert db_connection["connected"] is True
    assert db_session["transaction"] == "open"
    assert db_cursor["position"] == 0
    assert db_cursor["session"] is db_session
    assert db_session["connection"] is db_connection


def test_sample_data(sample_data):
    """Test using simple data fixture."""
    assert sample_data["count"] == 2
    assert "alice" in sample_data["users"]


def test_data_processor(data_processor):
    """Test using fixture with callable."""
    result = data_processor(str.upper)
    assert result == ["ALICE", "BOB"]


def test_authenticated_user(authenticated_user):
    """Test using yield fixture for auth."""
    assert authenticated_user["authenticated"] is True
    assert authenticated_user["name"] == "testuser"


def test_mixed_fixtures(db_cursor, sample_data, authenticated_user, local_fixture):
    """Test using mix of conftest and local fixtures."""
    assert db_cursor["position"] == 0
    assert sample_data["count"] == 2
    assert authenticated_user["authenticated"] is True
    assert local_fixture == "local_with_2_items"


def test_local_uses_conftest(local_fixture):
    """Test that local fixture can depend on conftest fixture."""
    assert "local_with_2_items" == local_fixture


@rustest.parametrize("value", [1, 2, 3])
def test_parametrized_with_conftest(value, sample_data):
    """Test parametrization works with conftest fixtures."""
    assert value in [1, 2, 3]
    assert sample_data["count"] == 2
