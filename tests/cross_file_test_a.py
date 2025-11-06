"""First file for testing cross-file fixture behavior.

This file defines some fixtures and tests to see if they can be accessed
from other files (they shouldn't be without conftest.py support).
"""

from rustest import fixture

# Counter to track fixture calls
cross_file_a_calls = {}


def track_a(name):
    """Track fixture calls in file A."""
    if name not in cross_file_a_calls:
        cross_file_a_calls[name] = 0
    cross_file_a_calls[name] += 1
    return cross_file_a_calls[name]


@fixture
def fixture_from_file_a():
    """A fixture defined in file A (function scope)."""
    return {"file": "A", "scope": "function", "calls": track_a("fixture_from_file_a")}


@fixture(scope="module")
def module_fixture_from_a():
    """A module-scoped fixture defined in file A."""
    return {"file": "A", "scope": "module", "calls": track_a("module_fixture_from_a")}


@fixture(scope="session")
def session_fixture_from_a():
    """A session-scoped fixture defined in file A."""
    return {
        "file": "A",
        "scope": "session",
        "calls": track_a("session_fixture_from_a"),
    }


@fixture
def common_name():
    """A fixture with a common name - defined in file A."""
    return {"defined_in": "file_A", "calls": track_a("common_name")}


def test_local_fixture_a(fixture_from_file_a):
    """Test using a local fixture from file A."""
    assert fixture_from_file_a["file"] == "A"
    assert fixture_from_file_a["scope"] == "function"


def test_module_fixture_a(module_fixture_from_a):
    """Test using a module-scoped fixture from file A."""
    assert module_fixture_from_a["file"] == "A"
    assert module_fixture_from_a["scope"] == "module"


def test_session_fixture_a(session_fixture_from_a):
    """Test using a session-scoped fixture from file A."""
    assert session_fixture_from_a["file"] == "A"
    assert session_fixture_from_a["scope"] == "session"


def test_common_name_in_a(common_name):
    """Test that we get the fixture defined in THIS file."""
    assert common_name["defined_in"] == "file_A"


def test_multiple_fixtures_a(fixture_from_file_a, module_fixture_from_a, session_fixture_from_a):
    """Test using multiple local fixtures together."""
    assert fixture_from_file_a["file"] == "A"
    assert module_fixture_from_a["file"] == "A"
    assert session_fixture_from_a["file"] == "A"
