"""Second file for testing cross-file fixture behavior.

This file tries to use fixtures from cross_file_test_a.py to verify that
fixtures are module-local and cannot be accessed across files (unless
conftest.py support is added in the future).
"""

from rustest import fixture

# Counter to track fixture calls
cross_file_b_calls = {}


def track_b(name):
    """Track fixture calls in file B."""
    if name not in cross_file_b_calls:
        cross_file_b_calls[name] = 0
    cross_file_b_calls[name] += 1
    return cross_file_b_calls[name]


@fixture
def fixture_from_file_b():
    """A fixture defined in file B (function scope)."""
    return {"file": "B", "scope": "function", "calls": track_b("fixture_from_file_b")}


@fixture(scope="module")
def module_fixture_from_b():
    """A module-scoped fixture defined in file B."""
    return {"file": "B", "scope": "module", "calls": track_b("module_fixture_from_b")}


@fixture(scope="session")
def session_fixture_from_b():
    """A session-scoped fixture defined in file B."""
    return {
        "file": "B",
        "scope": "session",
        "calls": track_b("session_fixture_from_b"),
    }


@fixture
def common_name():
    """A fixture with a common name - defined in file B.

    This has the same name as a fixture in cross_file_test_a.py, but they
    should be completely isolated.
    """
    return {"defined_in": "file_B", "calls": track_b("common_name")}


def test_local_fixture_b(fixture_from_file_b):
    """Test using a local fixture from file B."""
    assert fixture_from_file_b["file"] == "B"
    assert fixture_from_file_b["scope"] == "function"


def test_module_fixture_b(module_fixture_from_b):
    """Test using a module-scoped fixture from file B."""
    assert module_fixture_from_b["file"] == "B"
    assert module_fixture_from_b["scope"] == "module"


def test_session_fixture_b(session_fixture_from_b):
    """Test using a session-scoped fixture from file B."""
    assert session_fixture_from_b["file"] == "B"
    assert session_fixture_from_b["scope"] == "session"


def test_common_name_in_b(common_name):
    """Test that we get the fixture defined in THIS file, not from file A."""
    assert common_name["defined_in"] == "file_B"


def test_multiple_fixtures_b(fixture_from_file_b, module_fixture_from_b, session_fixture_from_b):
    """Test using multiple local fixtures together."""
    assert fixture_from_file_b["file"] == "B"
    assert module_fixture_from_b["file"] == "B"
    assert session_fixture_from_b["file"] == "B"


# NOTE: The following test would FAIL because fixtures from file A
# are not accessible from file B (no conftest.py support yet)
#
# def test_cross_file_access(fixture_from_file_a):
#     """This would fail - cannot access fixtures from other files."""
#     pass


def test_isolation_verification(common_name):
    """Verify that fixtures with the same name are isolated between files."""
    # This should be the file_B version, not file_A version
    assert common_name["defined_in"] == "file_B"
    # The call count should be for THIS file only
    assert common_name["calls"] >= 1
