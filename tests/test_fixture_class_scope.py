"""Test suite for class-scoped fixtures.

Class-scoped fixtures should be shared across all test methods in a class
but should be different instances for different classes.
"""

import unittest

from rustest import fixture

# Track fixture calls
class_fixture_calls = {}


def reset_class_calls():
    """Reset call tracking."""
    global class_fixture_calls
    class_fixture_calls = {}


def track_call(name):
    """Track and return call count."""
    global class_fixture_calls
    if name not in class_fixture_calls:
        class_fixture_calls[name] = 0
    class_fixture_calls[name] += 1
    return class_fixture_calls[name]


# ============================================================================
# CLASS-SCOPED FIXTURES
# ============================================================================


@fixture(scope="class")
def class_database():
    """Class-scoped database connection."""
    return {"connection": "db://class", "calls": track_call("class_database")}


@fixture(scope="class")
def class_counter():
    """Class-scoped counter."""
    return {"value": track_call("class_counter")}


@fixture(scope="session")
def session_config():
    """Session-scoped configuration."""
    return {"env": "test", "calls": track_call("session_config")}


@fixture(scope="class")
def class_service(session_config):
    """Class-scoped service depending on session config."""
    return {
        "config": session_config,
        "name": "service",
        "calls": track_call("class_service"),
    }


# ============================================================================
# TEST CLASSES FOR CLASS SCOPE
# ============================================================================


class TestClassScopeA(unittest.TestCase):
    """First test class using class-scoped fixtures."""

    def test_class_db_1(self, class_database=None):
        """First test in class A using class_database."""
        # Note: unittest.TestCase methods need default args for fixtures
        # This is a known limitation - we'll need to handle this
        # For now, these tests verify the concept
        pass

    def test_class_db_2(self, class_database=None):
        """Second test in class A using class_database."""
        pass


class TestClassScopeB(unittest.TestCase):
    """Second test class using class-scoped fixtures."""

    def test_class_db_1(self, class_database=None):
        """First test in class B using class_database."""
        pass


# ============================================================================
# REGULAR FUNCTION TESTS WITH CLASS SCOPE
# ============================================================================
# Note: Class-scoped fixtures work differently with function-based tests
# since functions don't belong to classes. In this case, each "class" is None,
# so all function tests share the same class-scoped fixture.


def test_class_scope_func_1(class_counter):
    """First function test with class-scoped fixture."""
    # All function tests (not in a class) share the same class scope (None)
    assert class_counter["value"] == 1


def test_class_scope_func_2(class_counter):
    """Second function test with class-scoped fixture."""
    # Should reuse the same instance as test_class_scope_func_1
    assert class_counter["value"] == 1


def test_class_scope_func_3(class_counter):
    """Third function test with class-scoped fixture."""
    # Should still be the same instance
    assert class_counter["value"] == 1


# ============================================================================
# CLASS-SCOPED WITH DEPENDENCIES
# ============================================================================


def test_class_service_1(class_service):
    """Test class-scoped fixture with session dependency."""
    # Session config should be called once
    assert class_service["config"]["calls"] == 1
    # Class service should be called once (for this class context)
    assert class_service["calls"] == 1


def test_class_service_2(class_service):
    """Test class service is reused in same class context."""
    # Session config still only called once
    assert class_service["config"]["calls"] == 1
    # Class service still only called once
    assert class_service["calls"] == 1


# ============================================================================
# MIXED SCOPES WITH CLASS
# ============================================================================


@fixture
def func_fixture():
    """Function-scoped fixture."""
    return track_call("func_fixture")


@fixture(scope="module")
def mod_fixture():
    """Module-scoped fixture."""
    return track_call("mod_fixture")


def test_mixed_with_class_1(func_fixture, class_counter, mod_fixture, session_config):
    """Test all scope types together."""
    # Each scope behaves as expected
    assert func_fixture == 1
    assert class_counter["value"] == 1
    assert mod_fixture == 1
    assert session_config["calls"] == 1


def test_mixed_with_class_2(func_fixture, class_counter, mod_fixture, session_config):
    """Test scope interactions."""
    # Function scope is new
    assert func_fixture == 2
    # Class scope is reused (same class context = None)
    assert class_counter["value"] == 1
    # Module scope is reused
    assert mod_fixture == 1
    # Session scope is reused
    assert session_config["calls"] == 1


# ============================================================================
# CLASS SCOPE WITH MUTATIONS
# ============================================================================


@fixture(scope="class")
def mutable_class_state():
    """Class-scoped mutable state."""
    return {"mutations": [], "init": track_call("mutable_class_state")}


def test_class_mutation_1(mutable_class_state):
    """Test class-scoped state can be mutated."""
    assert mutable_class_state["init"] == 1
    assert len(mutable_class_state["mutations"]) == 0
    mutable_class_state["mutations"].append("test1")
    assert len(mutable_class_state["mutations"]) == 1


def test_class_mutation_2(mutable_class_state):
    """Test mutations persist within class scope."""
    # Same instance, mutations persist
    assert mutable_class_state["init"] == 1
    assert len(mutable_class_state["mutations"]) == 1
    assert mutable_class_state["mutations"][0] == "test1"
    mutable_class_state["mutations"].append("test2")


def test_class_mutation_3(mutable_class_state):
    """Test continued persistence."""
    assert mutable_class_state["init"] == 1
    assert len(mutable_class_state["mutations"]) == 2
    assert mutable_class_state["mutations"] == ["test1", "test2"]


# ============================================================================
# COMPLEX CLASS DEPENDENCIES
# ============================================================================


@fixture(scope="session")
def session_base():
    """Session-level base."""
    return {"level": "session", "calls": track_call("session_base")}


@fixture(scope="module")
def module_layer(session_base):
    """Module-level layer."""
    return {
        "level": "module",
        "base": session_base,
        "calls": track_call("module_layer"),
    }


@fixture(scope="class")
def class_layer(module_layer):
    """Class-level layer."""
    return {
        "level": "class",
        "module": module_layer,
        "calls": track_call("class_layer"),
    }


@fixture
def function_layer(class_layer):
    """Function-level layer."""
    return {
        "level": "function",
        "class": class_layer,
        "calls": track_call("function_layer"),
    }


def test_layered_deps_1(function_layer):
    """Test all layers are created correctly."""
    assert function_layer["level"] == "function"
    assert function_layer["calls"] == 1
    assert function_layer["class"]["level"] == "class"
    assert function_layer["class"]["calls"] == 1
    assert function_layer["class"]["module"]["level"] == "module"
    assert function_layer["class"]["module"]["calls"] == 1
    assert function_layer["class"]["module"]["base"]["level"] == "session"
    assert function_layer["class"]["module"]["base"]["calls"] == 1


def test_layered_deps_2(function_layer):
    """Test layer reuse."""
    # Function layer is new
    assert function_layer["calls"] == 2
    # Class layer is reused
    assert function_layer["class"]["calls"] == 1
    # Module layer is reused
    assert function_layer["class"]["module"]["calls"] == 1
    # Session layer is reused
    assert function_layer["class"]["module"]["base"]["calls"] == 1


# ============================================================================
# CLASS SCOPE ISOLATION TEST
# ============================================================================


@fixture(scope="class")
def class_id():
    """Class-scoped ID - should be same within a class context."""
    return track_call("class_id")


def test_isolation_1(class_id):
    """First test for isolation verification."""
    # These function tests all have class_name=None, so they share class scope
    assert class_id == 1


def test_isolation_2(class_id):
    """Second test for isolation verification."""
    assert class_id == 1


def test_isolation_3(class_id):
    """Third test for isolation verification."""
    assert class_id == 1
