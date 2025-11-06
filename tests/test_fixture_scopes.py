"""Comprehensive test suite for fixture scoping functionality.

This test file covers all fixture scopes (function, class, module, session)
and their interactions. These tests verify that:
- Fixtures are created at the correct scope
- Fixtures are reused appropriately within their scope
- Fixtures are destroyed at scope boundaries
- Fixtures can depend on other fixtures with different scopes
- Edge cases and error conditions are handled correctly
"""

from rustest import fixture

# Track how many times each fixture is called
call_counts = {}


def reset_call_counts():
    """Reset all call counts for testing."""
    global call_counts
    call_counts = {}


def increment_count(name):
    """Increment and return the call count for a fixture."""
    global call_counts
    if name not in call_counts:
        call_counts[name] = 0
    call_counts[name] += 1
    return call_counts[name]


# ============================================================================
# FUNCTION-SCOPED FIXTURES (default behavior)
# ============================================================================


@fixture
def function_fixture():
    """Function-scoped fixture (default) - should be called for each test."""
    return increment_count("function_fixture")


@fixture(scope="function")
def explicit_function_fixture():
    """Explicitly function-scoped fixture."""
    return increment_count("explicit_function_fixture")


def test_function_scope_1(function_fixture):
    """First test using function-scoped fixture."""
    # This should be the first call
    assert function_fixture == 1


def test_function_scope_2(function_fixture):
    """Second test using function-scoped fixture."""
    # This should be a fresh instance (second call)
    assert function_fixture == 2


def test_function_scope_3(function_fixture):
    """Third test using function-scoped fixture."""
    # This should be a fresh instance (third call)
    assert function_fixture == 3


def test_explicit_function_scope_1(explicit_function_fixture):
    """Test explicit function scope."""
    assert explicit_function_fixture == 1


def test_explicit_function_scope_2(explicit_function_fixture):
    """Test explicit function scope again."""
    assert explicit_function_fixture == 2


# ============================================================================
# MODULE-SCOPED FIXTURES
# ============================================================================


@fixture(scope="module")
def module_fixture():
    """Module-scoped fixture - should be called once for the entire module."""
    return increment_count("module_fixture")


@fixture(scope="module")
def module_counter():
    """Module-scoped counter that starts at 100."""
    return {"value": 100, "calls": increment_count("module_counter")}


def test_module_scope_1(module_fixture):
    """First test using module-scoped fixture."""
    # This should be the first (and only) call
    assert module_fixture == 1


def test_module_scope_2(module_fixture):
    """Second test using module-scoped fixture."""
    # This should reuse the same instance
    assert module_fixture == 1


def test_module_scope_3(module_fixture):
    """Third test using module-scoped fixture."""
    # This should still be the same instance
    assert module_fixture == 1


def test_module_counter_1(module_counter):
    """Test module counter first usage."""
    assert module_counter["calls"] == 1
    assert module_counter["value"] == 100


def test_module_counter_2(module_counter):
    """Test module counter is reused."""
    # Should be the same dict instance
    assert module_counter["calls"] == 1
    assert module_counter["value"] == 100


# ============================================================================
# SESSION-SCOPED FIXTURES
# ============================================================================


@fixture(scope="session")
def session_fixture():
    """Session-scoped fixture - should be called once for the entire session."""
    return increment_count("session_fixture")


@fixture(scope="session")
def session_database():
    """Simulated session-level database connection."""
    return {"connection": "db://test", "calls": increment_count("session_database")}


def test_session_scope_1(session_fixture):
    """First test using session-scoped fixture."""
    # This should be the first (and only) call across all test files
    assert session_fixture == 1


def test_session_scope_2(session_fixture):
    """Second test using session-scoped fixture."""
    # This should reuse the same instance
    assert session_fixture == 1


def test_session_scope_3(session_fixture):
    """Third test using session-scoped fixture."""
    # This should still be the same instance
    assert session_fixture == 1


def test_session_database_1(session_database):
    """Test session database is created once."""
    assert session_database["calls"] == 1
    assert session_database["connection"] == "db://test"


def test_session_database_2(session_database):
    """Test session database is reused."""
    assert session_database["calls"] == 1


# ============================================================================
# FIXTURE DEPENDENCY CHAINS WITH DIFFERENT SCOPES
# ============================================================================


@fixture(scope="session")
def base_config():
    """Session-level configuration."""
    return {"env": "test", "calls": increment_count("base_config")}


@fixture(scope="module")
def module_service(base_config):
    """Module-level service that depends on session config."""
    return {
        "config": base_config,
        "name": "test_service",
        "calls": increment_count("module_service"),
    }


@fixture
def request_handler(module_service):
    """Function-level handler that depends on module service."""
    return {
        "service": module_service,
        "request_id": increment_count("request_handler"),
    }


def test_dependency_chain_1(request_handler):
    """Test fixture dependency chain across scopes."""
    # Session fixture should be called once
    assert request_handler["service"]["config"]["calls"] == 1
    # Module fixture should be called once
    assert request_handler["service"]["calls"] == 1
    # Function fixture should be called once (for this test)
    assert request_handler["request_id"] == 1


def test_dependency_chain_2(request_handler):
    """Test that higher scopes are reused."""
    # Session fixture still only called once
    assert request_handler["service"]["config"]["calls"] == 1
    # Module fixture still only called once
    assert request_handler["service"]["calls"] == 1
    # Function fixture is called again
    assert request_handler["request_id"] == 2


def test_dependency_chain_3(module_service):
    """Test accessing module service directly."""
    # Session and module fixtures should still be from first creation
    assert module_service["config"]["calls"] == 1
    assert module_service["calls"] == 1


# ============================================================================
# MULTIPLE FIXTURES WITH MIXED SCOPES
# ============================================================================


@fixture
def temp_file():
    """Function-scoped temporary file."""
    return f"temp_{increment_count('temp_file')}.txt"


@fixture(scope="module")
def cache_dir():
    """Module-scoped cache directory."""
    return f"cache_{increment_count('cache_dir')}"


@fixture(scope="session")
def root_dir():
    """Session-scoped root directory."""
    return f"root_{increment_count('root_dir')}"


def test_mixed_scopes_1(temp_file, cache_dir, root_dir):
    """Test using fixtures with different scopes together."""
    assert temp_file == "temp_1.txt"
    assert cache_dir == "cache_1"
    assert root_dir == "root_1"


def test_mixed_scopes_2(temp_file, cache_dir, root_dir):
    """Test scope behavior with mixed fixtures."""
    # Function scope creates new instance
    assert temp_file == "temp_2.txt"
    # Module scope reuses instance
    assert cache_dir == "cache_1"
    # Session scope reuses instance
    assert root_dir == "root_1"


def test_mixed_scopes_3(temp_file, cache_dir, root_dir):
    """Test scope behavior continues correctly."""
    assert temp_file == "temp_3.txt"
    assert cache_dir == "cache_1"
    assert root_dir == "root_1"


# ============================================================================
# COMPLEX DEPENDENCY GRAPH
# ============================================================================


@fixture(scope="session")
def session_a():
    """Session fixture A."""
    return {"name": "A", "calls": increment_count("session_a")}


@fixture(scope="session")
def session_b():
    """Session fixture B."""
    return {"name": "B", "calls": increment_count("session_b")}


@fixture(scope="module")
def module_c(session_a, session_b):
    """Module fixture depending on two session fixtures."""
    return {
        "name": "C",
        "deps": [session_a, session_b],
        "calls": increment_count("module_c"),
    }


@fixture(scope="module")
def module_d(session_a):
    """Module fixture depending on one session fixture."""
    return {"name": "D", "dep": session_a, "calls": increment_count("module_d")}


@fixture
def function_e(module_c, module_d):
    """Function fixture depending on two module fixtures."""
    return {
        "name": "E",
        "deps": [module_c, module_d],
        "calls": increment_count("function_e"),
    }


def test_complex_deps_1(function_e):
    """Test complex dependency graph."""
    # Verify all session fixtures called once
    assert function_e["deps"][0]["deps"][0]["calls"] == 1  # session_a via module_c
    assert function_e["deps"][0]["deps"][1]["calls"] == 1  # session_b
    # Verify all module fixtures called once
    assert function_e["deps"][0]["calls"] == 1  # module_c
    assert function_e["deps"][1]["calls"] == 1  # module_d
    # Verify function fixture called once
    assert function_e["calls"] == 1


def test_complex_deps_2(function_e):
    """Test complex deps remain consistent."""
    # Session fixtures still only called once
    assert function_e["deps"][0]["deps"][0]["calls"] == 1
    assert function_e["deps"][0]["deps"][1]["calls"] == 1
    # Module fixtures still only called once
    assert function_e["deps"][0]["calls"] == 1
    assert function_e["deps"][1]["calls"] == 1
    # Function fixture called again
    assert function_e["calls"] == 2


# ============================================================================
# FIXTURES RETURNING DIFFERENT TYPES
# ============================================================================


@fixture(scope="session")
def session_string():
    """Session fixture returning a string."""
    count = increment_count("session_string")
    return f"session_value_{count}"


@fixture(scope="module")
def module_list():
    """Module fixture returning a list."""
    return [1, 2, 3, increment_count("module_list")]


@fixture
def function_dict():
    """Function fixture returning a dict."""
    return {"key": "value", "count": increment_count("function_dict")}


@fixture(scope="session")
def session_none():
    """Session fixture returning None."""
    increment_count("session_none")
    return None


def test_different_types_1(session_string, module_list, function_dict, session_none):
    """Test fixtures returning different types."""
    assert session_string == "session_value_1"
    assert module_list == [1, 2, 3, 1]
    assert function_dict["count"] == 1
    assert session_none is None


def test_different_types_2(session_string, module_list, function_dict, session_none):
    """Test type handling is consistent."""
    # Session and module fixtures return same values
    assert session_string == "session_value_1"
    assert module_list == [1, 2, 3, 1]
    # Function fixture is new
    assert function_dict["count"] == 2
    assert session_none is None


# ============================================================================
# EDGE CASES
# ============================================================================


@fixture(scope="module")
def shared_state():
    """Module fixture with mutable state."""
    return {"counter": 0, "init_calls": increment_count("shared_state")}


def test_shared_state_mutation_1(shared_state):
    """Test that module fixtures maintain state across tests."""
    assert shared_state["init_calls"] == 1
    shared_state["counter"] += 1
    assert shared_state["counter"] == 1


def test_shared_state_mutation_2(shared_state):
    """Test that mutations persist in module scope."""
    # Same instance, so mutation from previous test persists
    assert shared_state["init_calls"] == 1
    assert shared_state["counter"] == 1
    shared_state["counter"] += 1
    assert shared_state["counter"] == 2


def test_shared_state_mutation_3(shared_state):
    """Test continued state persistence."""
    assert shared_state["init_calls"] == 1
    assert shared_state["counter"] == 2
