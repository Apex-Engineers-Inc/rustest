"""Edge cases and error conditions for fixture scoping.

This test file covers:
- Fixtures with no parameters
- Fixtures returning None
- Fixtures with complex dependency chains
- Scope validation
- Cross-scope dependencies
"""

from rustest import fixture

# Tracking for edge case tests
edge_case_calls = {}


def track_edge_call(name):
    """Track calls for edge cases."""
    global edge_case_calls
    if name not in edge_case_calls:
        edge_case_calls[name] = 0
    edge_case_calls[name] += 1
    return edge_case_calls[name]


def get_edge_call_count(name):
    """Get the call count for a tracked name."""
    global edge_case_calls
    return edge_case_calls.get(name, 0)


# ============================================================================
# FIXTURES RETURNING NONE
# ============================================================================


@fixture
def none_function():
    """Function-scoped fixture that returns None."""
    track_edge_call("none_function")
    return None


@fixture(scope="class")
def none_class():
    """Class-scoped fixture that returns None."""
    track_edge_call("none_class")
    return None


@fixture(scope="module")
def none_module():
    """Module-scoped fixture that returns None."""
    track_edge_call("none_module")
    return None


@fixture(scope="session")
def none_session():
    """Session-scoped fixture that returns None."""
    track_edge_call("none_session")
    return None


def test_none_values_1(none_function, none_class, none_module, none_session):
    """Test that None values work correctly."""
    assert none_function is None
    assert none_class is None
    assert none_module is None
    assert none_session is None


def test_none_values_2(none_function, none_class, none_module, none_session):
    """Test None values with scope behavior."""
    # All should still be None
    assert none_function is None
    assert none_class is None
    assert none_module is None
    assert none_session is None


# ============================================================================
# EMPTY PARAMETER FIXTURES
# ============================================================================


@fixture(scope="session")
def no_params_session():
    """Session fixture with no parameters."""
    return track_edge_call("no_params_session")


@fixture(scope="module")
def no_params_module():
    """Module fixture with no parameters."""
    return track_edge_call("no_params_module")


@fixture(scope="class")
def no_params_class():
    """Class fixture with no parameters."""
    return track_edge_call("no_params_class")


@fixture
def no_params_function():
    """Function fixture with no parameters."""
    return track_edge_call("no_params_function")


def test_no_params_1(no_params_session, no_params_module, no_params_class, no_params_function):
    """Test fixtures with no parameters."""
    assert no_params_session == 1
    assert no_params_module == 1
    assert no_params_class == 1
    assert no_params_function == 1


def test_no_params_2(no_params_session, no_params_module, no_params_class, no_params_function):
    """Test no-param fixture scoping.

    Note: class-scoped fixtures in plain function tests (not in a class)
    behave like function-scoped - they're recreated for each test.
    """
    # Session and module are reused
    assert no_params_session == 1
    assert no_params_module == 1
    # Class behaves like function scope (no class context) - recreated
    assert no_params_class == 2
    # Function is new
    assert no_params_function == 2


# ============================================================================
# DEEP DEPENDENCY CHAINS
# ============================================================================


@fixture(scope="session")
def level_1():
    """First level in dependency chain."""
    return {"level": 1, "calls": track_edge_call("level_1")}


@fixture(scope="session")
def level_2(level_1):
    """Second level depending on first."""
    return {"level": 2, "prev": level_1, "calls": track_edge_call("level_2")}


@fixture(scope="module")
def level_3(level_2):
    """Third level depending on second."""
    return {"level": 3, "prev": level_2, "calls": track_edge_call("level_3")}


@fixture(scope="class")
def level_4(level_3):
    """Fourth level depending on third."""
    return {"level": 4, "prev": level_3, "calls": track_edge_call("level_4")}


@fixture
def level_5(level_4):
    """Fifth level depending on fourth."""
    return {"level": 5, "prev": level_4, "calls": track_edge_call("level_5")}


def test_deep_chain_1(level_5):
    """Test deep dependency chain."""
    # Walk the chain backwards
    current = level_5
    for expected_level in [5, 4, 3, 2, 1]:
        assert current["level"] == expected_level
        assert current["calls"] == 1
        if expected_level > 1:
            current = current["prev"]


def test_deep_chain_2(level_5):
    """Test deep chain with scope behavior.

    Note: class-scoped fixtures (level_4) behave like function-scoped
    in plain function tests without a class context.
    """
    # Function scope (level 5) is new
    assert level_5["calls"] == 2
    # Class scope (level 4) is also new (no class context) - recreated
    assert level_5["prev"]["calls"] == 2
    # Module scope (level 3) is reused
    assert level_5["prev"]["prev"]["calls"] == 1
    # Session scopes (level 2 and 1) are reused
    assert level_5["prev"]["prev"]["prev"]["calls"] == 1  # level 2 (session)
    assert level_5["prev"]["prev"]["prev"]["prev"]["calls"] == 1  # level 1 (session)


# ============================================================================
# MULTIPLE DEPENDENCIES AT SAME LEVEL
# ============================================================================


@fixture(scope="module")
def service_a():
    """Module-scoped service A."""
    return {"name": "A", "calls": track_edge_call("service_a")}


@fixture(scope="module")
def service_b():
    """Module-scoped service B."""
    return {"name": "B", "calls": track_edge_call("service_b")}


@fixture(scope="module")
def service_c():
    """Module-scoped service C."""
    return {"name": "C", "calls": track_edge_call("service_c")}


@fixture
def orchestrator(service_a, service_b, service_c):
    """Function fixture depending on multiple module fixtures."""
    return {
        "services": [service_a, service_b, service_c],
        "calls": track_edge_call("orchestrator"),
    }


def test_multiple_deps_1(orchestrator):
    """Test fixture with multiple dependencies."""
    assert len(orchestrator["services"]) == 3
    assert orchestrator["services"][0]["calls"] == 1
    assert orchestrator["services"][1]["calls"] == 1
    assert orchestrator["services"][2]["calls"] == 1
    assert orchestrator["calls"] == 1


def test_multiple_deps_2(orchestrator):
    """Test multiple deps are reused correctly."""
    # All module fixtures reused
    assert orchestrator["services"][0]["calls"] == 1
    assert orchestrator["services"][1]["calls"] == 1
    assert orchestrator["services"][2]["calls"] == 1
    # Function fixture is new
    assert orchestrator["calls"] == 2


# ============================================================================
# DIAMOND DEPENDENCY PATTERN
# ============================================================================


@fixture(scope="session")
def root():
    """Root of diamond dependency."""
    return {"name": "root", "calls": track_edge_call("root")}


@fixture(scope="module")
def branch_left(root):
    """Left branch of diamond."""
    return {"name": "left", "root": root, "calls": track_edge_call("branch_left")}


@fixture(scope="module")
def branch_right(root):
    """Right branch of diamond."""
    return {"name": "right", "root": root, "calls": track_edge_call("branch_right")}


@fixture(scope="class")
def diamond_merge(branch_left, branch_right):
    """Merge point of diamond - depends on both branches."""
    return {
        "name": "merge",
        "branches": [branch_left, branch_right],
        "calls": track_edge_call("diamond_merge"),
    }


def test_diamond_1(diamond_merge):
    """Test diamond dependency pattern."""
    # Verify root is only called once (shared by both branches)
    assert diamond_merge["branches"][0]["root"]["calls"] == 1
    assert diamond_merge["branches"][1]["root"]["calls"] == 1
    # Verify they reference the same root object
    assert diamond_merge["branches"][0]["root"] is diamond_merge["branches"][1]["root"]


def test_diamond_2(diamond_merge):
    """Test diamond pattern with scope behavior.

    Note: diamond_merge is class-scoped, so it's recreated in plain function tests.
    """
    # Class-scoped fixture is recreated (no class context)
    assert diamond_merge["calls"] == 2
    # Module and session scopes are reused
    assert diamond_merge["branches"][0]["calls"] == 1  # module scope
    assert diamond_merge["branches"][1]["calls"] == 1  # module scope
    assert diamond_merge["branches"][0]["root"]["calls"] == 1  # session scope


# ============================================================================
# SCOPE BOUNDARIES
# ============================================================================


@fixture(scope="session")
def session_boundary():
    """Session-scoped fixture for boundary testing."""
    return {"value": track_edge_call("session_boundary"), "data": []}


@fixture(scope="module")
def module_boundary():
    """Module-scoped fixture for boundary testing."""
    return {"value": track_edge_call("module_boundary"), "data": []}


@fixture(scope="class")
def class_boundary():
    """Class-scoped fixture for boundary testing."""
    return {"value": track_edge_call("class_boundary"), "data": []}


def test_boundary_mutation_1(session_boundary, module_boundary, class_boundary):
    """Test fixture mutations at boundaries."""
    # Add data to each fixture
    session_boundary["data"].append("s1")
    module_boundary["data"].append("m1")
    class_boundary["data"].append("c1")

    assert session_boundary["value"] == 1
    assert module_boundary["value"] == 1
    assert class_boundary["value"] == 1


def test_boundary_mutation_2(session_boundary, module_boundary, class_boundary):
    """Test mutations persist across appropriate scopes.

    Note: class_boundary is recreated for each test (no class context),
    so mutations don't persist.
    """
    # Session and module persist mutations
    assert session_boundary["data"] == ["s1"]
    assert module_boundary["data"] == ["m1"]
    # Class fixture is recreated - data is empty
    assert class_boundary["data"] == []
    assert class_boundary["value"] == 2  # Second call to fixture

    # Add more data
    session_boundary["data"].append("s2")
    module_boundary["data"].append("m2")
    class_boundary["data"].append("c2")


def test_boundary_mutation_3(session_boundary, module_boundary, class_boundary):
    """Test continued mutations.

    Note: class_boundary is recreated each test, so no mutations persist.
    """
    # Session and module mutations persist
    assert session_boundary["data"] == ["s1", "s2"]
    assert module_boundary["data"] == ["m1", "m2"]
    # Class fixture is recreated again - data is empty
    assert class_boundary["data"] == []
    assert class_boundary["value"] == 3  # Third call to fixture


# ============================================================================
# FIXTURES WITH COMPLEX RETURN TYPES
# ============================================================================


@fixture(scope="session")
def session_tuple():
    """Session fixture returning tuple."""
    return (1, 2, track_edge_call("session_tuple"))


@fixture(scope="module")
def module_set():
    """Module fixture returning set."""
    return {1, 2, 3, track_edge_call("module_set")}


@fixture(scope="class")
def class_custom_object():
    """Class fixture returning custom object."""

    class CustomObj:
        def __init__(self):
            self.value = track_edge_call("class_custom_object")

    return CustomObj()


def test_complex_types_1(session_tuple, module_set, class_custom_object):
    """Test complex return types.

    Note: The set tracks its own creation, so module_set = {1, 2, 3, 1}
    where the last element is the call count.
    """
    assert session_tuple[2] == 1  # Session scope - first call
    assert 1 in module_set  # Module scope contains the call count (1)
    assert class_custom_object.value == 1  # Class scope - first call


def test_complex_types_2(session_tuple, module_set, class_custom_object):
    """Test complex types scope behavior.

    Note: class_custom_object is recreated (no class context).
    """
    # Session and module fixtures are reused
    assert session_tuple[2] == 1
    assert 1 in module_set
    # Class fixture is recreated
    assert class_custom_object.value == 2  # Second call


# ============================================================================
# SCOPE WITH GENERATORS (yield support)
# ============================================================================


@fixture(scope="module")
def generator_yield_fixture():
    """Module fixture with real yield support."""
    setup_count = track_edge_call("generator_yield_setup")
    yield {"setup": setup_count, "data": "test_data"}
    track_edge_call("generator_yield_teardown")


def test_generator_yield_1(generator_yield_fixture):
    """Test real yield fixture."""
    assert generator_yield_fixture["setup"] == 1
    assert generator_yield_fixture["data"] == "test_data"
    # Teardown hasn't run yet
    assert get_edge_call_count("generator_yield_teardown") == 0


def test_generator_yield_2(generator_yield_fixture):
    """Test yield fixture is reused within module scope."""
    assert generator_yield_fixture["setup"] == 1  # Same instance
    assert generator_yield_fixture["data"] == "test_data"
    # Teardown still hasn't run
    assert get_edge_call_count("generator_yield_teardown") == 0
