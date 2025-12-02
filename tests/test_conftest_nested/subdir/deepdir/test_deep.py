"""Tests at deep level of nested conftest structure."""

import os

import pytest

AUTOUSE_ENV_VAR = "RUSTEST_PARENT_AUTOUSE_LAST_TEST"
ASYNC_AUTOUSE_ENV_VAR = "RUSTEST_PARENT_ASYNC_AUTOUSE_LAST_TEST"

requires_rustest = pytest.mark.skipif(
    os.environ.get("RUSTEST_RUNNING") != "1",
    reason="async autouse fixtures supported only when running under rustest",
)


def test_deep_fixture(deep_fixture):
    """Test can access deep level fixture."""
    assert deep_fixture == "deep"


def test_child_fixture_from_deep(child_fixture):
    """Test can access parent (child) level fixture."""
    assert child_fixture == "child"


def test_root_fixture_from_deep(root_fixture):
    """Test can access grandparent (root) level fixture."""
    assert root_fixture == "root"


def test_another_overridable_uses_deep(another_overridable):
    """At deep level, gets deep version of fixture (shadowing from child)."""
    assert another_overridable == "from_deep_level"


def test_overridable_uses_child(overridable_fixture):
    """Gets child version since deep doesn't override this one."""
    assert overridable_fixture == "from_child"


def test_deep_with_chain(deep_with_chain):
    """Test fixture that depends on fixtures from multiple conftest levels."""
    assert deep_with_chain == "deep_child_root"


def test_session_fixture_in_deep(nested_session_fixture):
    """Session fixture from root is accessible in deep level."""
    assert nested_session_fixture == "session_from_root"


def test_root_only_in_deep(root_only):
    """Root-only fixture is accessible from deep level."""
    assert root_only == "root_only_value"


def test_root_autouse_runs_for_deep_dir():
    """Root-level autouse fixture should run even in deeper directories."""
    assert (
        os.environ.get(AUTOUSE_ENV_VAR) == "test_root_autouse_runs_for_deep_dir"
    ), "Parent autouse fixture did not run for deep directory tests"


@requires_rustest
def test_root_async_autouse_runs_for_deep_dir():
    """Root-level async autouse fixture should run even in deeper directories."""
    assert (
        os.environ.get(ASYNC_AUTOUSE_ENV_VAR) == "test_root_async_autouse_runs_for_deep_dir"
    ), "Parent async autouse fixture did not run for deep directory tests"
