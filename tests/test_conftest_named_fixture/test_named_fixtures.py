"""Test that fixtures with custom names from conftest.py are discoverable."""


def test_custom_named_db_fixture(db):
    """Test that we can use the 'db' fixture from conftest."""
    assert db["connected"] is True
    assert db["name"] == "test_db"


def test_custom_named_api_client(api_client):
    """Test that we can use the 'api_client' fixture from conftest."""
    assert api_client["endpoint"] == "http://test.api.local"
    assert api_client["auth"] == "bearer"


def test_regular_fixture(regular_fixture):
    """Test that regular fixtures still work."""
    assert regular_fixture == "regular_value"


def test_combined_fixtures(db, api_client, regular_fixture):
    """Test multiple fixtures together."""
    assert db["connected"] is True
    assert api_client["endpoint"] == "http://test.api.local"
    assert regular_fixture == "regular_value"


def test_custom_name_different_from_function(custom_name):
    """Test fixture where registered name differs from function name."""
    assert custom_name == "custom_name_value"
