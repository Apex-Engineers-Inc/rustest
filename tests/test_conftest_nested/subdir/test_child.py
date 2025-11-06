"""Tests at child level of nested conftest structure."""


def test_child_fixture(child_fixture):
    """Test can access child level fixture."""
    assert child_fixture == "child"


def test_root_fixture_from_child(root_fixture):
    """Test can access parent (root) level fixture."""
    assert root_fixture == "root"


def test_overridable_uses_child(overridable_fixture):
    """At child level, gets child version of fixture (shadowing)."""
    assert overridable_fixture == "from_child"


def test_child_with_root_dep(child_with_root_dep):
    """Test fixture dependency across conftest levels."""
    assert child_with_root_dep == "child_uses_root"


def test_session_fixture_accessible(nested_session_fixture):
    """Session fixture from root is accessible in child."""
    assert nested_session_fixture == "session_from_root"


def test_root_only_accessible(root_only):
    """Root-only fixture is accessible from child."""
    assert root_only == "root_only_value"


def test_another_overridable(another_overridable):
    """At child level, gets child version."""
    assert another_overridable == "from_child_level"
