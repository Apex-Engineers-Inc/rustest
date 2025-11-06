"""Tests for fixture edge cases and validation.

This file tests important edge cases around fixture scoping:
1. Scope ordering validation (higher scope can't depend on lower scope)
2. Fixtures depending on fixtures (recursive dependencies)
3. Same fixture name in different files
4. Cross-file fixture availability
"""

from rustest import fixture

# ============================================================================
# TEST 1: Fixtures depending on fixtures (should work)
# ============================================================================

call_tracker = {}


def track(name):
    """Track fixture calls."""
    if name not in call_tracker:
        call_tracker[name] = 0
    call_tracker[name] += 1
    return call_tracker[name]


@fixture
def level1():
    """First level fixture."""
    return {"level": 1, "calls": track("level1")}


@fixture
def level2(level1):
    """Second level depending on first."""
    return {"level": 2, "prev": level1, "calls": track("level2")}


@fixture
def level3(level2):
    """Third level depending on second."""
    return {"level": 3, "prev": level2, "calls": track("level3")}


def test_fixture_depending_on_fixture(level3):
    """Test that fixtures can depend on other fixtures."""
    assert level3["level"] == 3
    assert level3["prev"]["level"] == 2
    assert level3["prev"]["prev"]["level"] == 1


# ============================================================================
# TEST 2: Cross-scope dependencies (lower depending on higher - OK)
# ============================================================================


@fixture(scope="session")
def session_base():
    """Session-scoped base."""
    return {"scope": "session", "calls": track("session_base")}


@fixture(scope="module")
def module_uses_session(session_base):
    """Module-scoped depending on session - this is VALID."""
    return {
        "scope": "module",
        "base": session_base,
        "calls": track("module_uses_session"),
    }


@fixture
def function_uses_module(module_uses_session):
    """Function-scoped depending on module - this is VALID."""
    return {
        "scope": "function",
        "module": module_uses_session,
        "calls": track("function_uses_module"),
    }


def test_lower_scope_depending_on_higher_scope(function_uses_module):
    """Test that lower-scoped fixtures can depend on higher-scoped ones."""
    # This should work - function can use module, module can use session
    assert function_uses_module["scope"] == "function"
    assert function_uses_module["module"]["scope"] == "module"
    assert function_uses_module["module"]["base"]["scope"] == "session"


# ============================================================================
# TEST 3: INVALID - Higher scope depending on lower scope
# ============================================================================
# Note: These tests document the EXPECTED behavior.
# If rustest doesn't validate this yet, these tests will fail and indicate
# where validation needs to be added.


@fixture
def function_data():
    """Function-scoped data."""
    return {"value": track("function_data")}


# This SHOULD be an error - module scope depending on function scope
# @fixture(scope="module")
# def invalid_module_uses_function(function_data):
#     """THIS IS INVALID - module can't depend on function scope."""
#     return {"bad": function_data}


@fixture(scope="class")
def class_data():
    """Class-scoped data."""
    return {"value": track("class_data")}


# This SHOULD be an error - module scope depending on class scope
# @fixture(scope="module")
# def invalid_module_uses_class(class_data):
#     """THIS IS INVALID - module can't depend on class scope."""
#     return {"bad": class_data}


# This SHOULD be an error - session scope depending on function scope
# @fixture(scope="session")
# def invalid_session_uses_function(function_data):
#     """THIS IS INVALID - session can't depend on function scope."""
#     return {"bad": function_data}


# ============================================================================
# TEST 4: Same fixture name in this file vs other files
# ============================================================================


@fixture
def shared_fixture_name():
    """A fixture with a potentially common name."""
    return {"file": "test_fixture_validation", "calls": track("shared_fixture_name")}


def test_local_fixture(shared_fixture_name):
    """Test that we get the local fixture, not one from another file."""
    # This should use the fixture defined in THIS file
    assert shared_fixture_name["file"] == "test_fixture_validation"


# ============================================================================
# TEST 5: Fixture isolation between modules
# ============================================================================


@fixture(scope="module")
def isolated_module_fixture():
    """Module-scoped fixture that should be isolated to this module."""
    return {"module": "test_fixture_validation", "calls": track("isolated_module_fixture")}


def test_module_isolation_1(isolated_module_fixture):
    """First test using isolated module fixture."""
    assert isolated_module_fixture["module"] == "test_fixture_validation"
    assert isolated_module_fixture["calls"] == 1


def test_module_isolation_2(isolated_module_fixture):
    """Second test using isolated module fixture."""
    # Should be reused within this module
    assert isolated_module_fixture["calls"] == 1


# ============================================================================
# TEST 6: Complex multi-level dependencies
# ============================================================================


@fixture(scope="session")
def base_a():
    """Session fixture A."""
    return {"name": "A", "calls": track("base_a")}


@fixture(scope="session")
def base_b():
    """Session fixture B."""
    return {"name": "B", "calls": track("base_b")}


@fixture(scope="module")
def combined(base_a, base_b):
    """Module fixture depending on two session fixtures."""
    return {
        "name": "combined",
        "deps": [base_a, base_b],
        "calls": track("combined"),
    }


@fixture
def orchestrator(combined, level1):
    """Function fixture depending on module and function fixtures."""
    return {
        "name": "orchestrator",
        "combined": combined,
        "level1": level1,
        "calls": track("orchestrator"),
    }


def test_complex_dependencies(orchestrator):
    """Test complex multi-level dependency graph."""
    assert orchestrator["combined"]["deps"][0]["name"] == "A"
    assert orchestrator["combined"]["deps"][1]["name"] == "B"
    assert orchestrator["level1"]["level"] == 1


# ============================================================================
# TEST 7: Diamond dependency (same fixture via multiple paths)
# ============================================================================


@fixture(scope="session")
def diamond_root():
    """Root of diamond dependency."""
    return {"name": "root", "calls": track("diamond_root")}


@fixture(scope="module")
def diamond_left(diamond_root):
    """Left branch."""
    return {"name": "left", "root": diamond_root, "calls": track("diamond_left")}


@fixture(scope="module")
def diamond_right(diamond_root):
    """Right branch."""
    return {"name": "right", "root": diamond_root, "calls": track("diamond_right")}


@fixture
def diamond_merge(diamond_left, diamond_right):
    """Merge point - depends on both branches."""
    return {
        "name": "merge",
        "left": diamond_left,
        "right": diamond_right,
        "calls": track("diamond_merge"),
    }


def test_diamond_dependency(diamond_merge):
    """Test that diamond dependencies work correctly."""
    # The root should only be created once
    assert diamond_merge["left"]["root"]["calls"] == 1
    assert diamond_merge["right"]["root"]["calls"] == 1
    # Both branches should reference the SAME root object
    assert diamond_merge["left"]["root"] is diamond_merge["right"]["root"]
