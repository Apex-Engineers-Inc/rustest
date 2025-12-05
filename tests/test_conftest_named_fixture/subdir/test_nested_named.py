"""Test that named fixtures work correctly in nested directories."""


def test_parent_named_fixture_in_subdir(db):
    """Test that we can use the 'db' fixture from parent conftest."""
    assert db["connected"] is True
    assert db["name"] == "test_db"


def test_child_named_fixture(child_named):
    """Test that child's named fixture works."""
    assert child_named == "child_named_value"


def test_child_depends_on_parent_named(child_uses_parent_named):
    """Test that child fixture can depend on parent's named fixture."""
    assert child_uses_parent_named == "child_using_test_db"


def test_all_named_fixtures_together(db, api_client, child_named, regular_fixture):
    """Test all named fixtures from both parent and child."""
    assert db["connected"] is True
    assert api_client["endpoint"] == "http://test.api.local"
    assert child_named == "child_named_value"
    assert regular_fixture == "regular_value"
