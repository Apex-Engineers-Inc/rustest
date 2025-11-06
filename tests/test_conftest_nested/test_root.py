"""Tests at root level of nested conftest structure."""


def test_root_fixture(root_fixture):
    """Test can access root level fixture."""
    assert root_fixture == "root"


def test_overridable_at_root(overridable_fixture):
    """At root level, gets root version of fixture."""
    assert overridable_fixture == "from_root"


def test_session_fixture(nested_session_fixture):
    """Test can access session-scoped fixture."""
    assert nested_session_fixture == "session_from_root"


def test_root_only(root_only):
    """Test can access root-only fixture."""
    assert root_only == "root_only_value"
