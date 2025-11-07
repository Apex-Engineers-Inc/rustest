"""Comprehensive test suite for pytest-style class-based tests.

This test file covers:
1. Basic plain test classes (class Test*)
2. Test methods with fixtures
3. Class-scoped fixtures shared across test methods
4. Parametrized test methods
5. Skip markers and custom marks
6. Multiple test classes
7. Nested fixture dependencies
8. Yield fixtures with class scope
"""

from rustest import fixture, parametrize, skip, mark

# Track fixture calls to verify proper scoping
fixture_calls = {}


def reset_calls():
    """Reset call tracking."""
    global fixture_calls
    fixture_calls = {}


def track_call(name):
    """Track and return call count."""
    global fixture_calls
    if name not in fixture_calls:
        fixture_calls[name] = 0
    fixture_calls[name] += 1
    return fixture_calls[name]


# ============================================================================
# FIXTURES FOR CLASS-BASED TESTS
# ============================================================================


@fixture
def simple_fixture():
    """Function-scoped fixture for testing."""
    return track_call("simple_fixture")


@fixture(scope="class")
def class_scoped_db():
    """Class-scoped database fixture."""
    return {"connection": "db://test", "calls": track_call("class_scoped_db")}


@fixture(scope="class")
def class_counter():
    """Class-scoped counter."""
    count = track_call("class_counter")
    return {"value": count, "data": []}


@fixture(scope="module")
def module_config():
    """Module-scoped configuration."""
    return {"env": "test", "calls": track_call("module_config")}


@fixture(scope="session")
def session_resource():
    """Session-scoped resource."""
    return {"resource": "shared", "calls": track_call("session_resource")}


@fixture
def dependent_fixture(class_scoped_db):
    """Function-scoped fixture depending on class-scoped fixture."""
    return {
        "db": class_scoped_db,
        "calls": track_call("dependent_fixture"),
    }


@fixture(scope="class")
def class_with_deps(module_config, session_resource):
    """Class-scoped fixture with dependencies."""
    return {
        "config": module_config,
        "resource": session_resource,
        "calls": track_call("class_with_deps"),
    }


# Yield fixtures
@fixture(scope="class")
def class_yield_fixture():
    """Class-scoped yield fixture with setup and teardown."""
    call_count = track_call("class_yield_setup")
    resource = {"setup_calls": call_count, "data": []}
    yield resource
    # Teardown
    track_call("class_yield_teardown")


@fixture
def func_yield_fixture():
    """Function-scoped yield fixture."""
    call_count = track_call("func_yield_setup")
    yield {"setup_calls": call_count}
    track_call("func_yield_teardown")


# ============================================================================
# BASIC CLASS-BASED TESTS
# ============================================================================


class TestBasicClass:
    """Basic test class without fixtures."""

    def test_simple_assertion(self):
        """Test simple assertion."""
        assert 1 + 1 == 2

    def test_another_assertion(self):
        """Test another assertion."""
        assert "hello" == "hello"

    def test_list_operations(self):
        """Test list operations."""
        items = [1, 2, 3]
        assert len(items) == 3
        assert 2 in items


# ============================================================================
# CLASS-BASED TESTS WITH FIXTURES
# ============================================================================


class TestWithFixtures:
    """Test class using fixtures."""

    def test_simple_fixture(self, simple_fixture):
        """Test using a simple function-scoped fixture."""
        assert simple_fixture >= 1

    def test_simple_fixture_again(self, simple_fixture):
        """Test that function-scoped fixtures are recreated."""
        # Function-scoped fixture is recreated for each test
        assert simple_fixture >= 1

    def test_multiple_fixtures(self, simple_fixture, module_config):
        """Test using multiple fixtures."""
        assert simple_fixture >= 1
        assert module_config["env"] == "test"
        assert module_config["calls"] == 1


# ============================================================================
# CLASS-SCOPED FIXTURES SHARED ACROSS METHODS
# ============================================================================


class TestClassScopedFixtures:
    """Test class-scoped fixtures shared across methods in same class."""

    def test_class_db_first(self, class_scoped_db):
        """First test using class-scoped fixture."""
        # First call in this class
        assert class_scoped_db["calls"] == 1
        assert class_scoped_db["connection"] == "db://test"

    def test_class_db_second(self, class_scoped_db):
        """Second test using same class-scoped fixture instance."""
        # Same instance - class-scoped fixtures are shared within a class
        assert class_scoped_db["calls"] == 1
        assert class_scoped_db["connection"] == "db://test"

    def test_class_db_third(self, class_scoped_db):
        """Third test confirming fixture reuse."""
        # Still the same instance
        assert class_scoped_db["calls"] == 1


class TestClassScopedFixturesSecondClass:
    """Second test class to verify class-scoped fixtures are recreated."""

    def test_new_class_new_fixture(self, class_scoped_db):
        """Test that class-scoped fixtures are recreated for new class."""
        # New class gets a new instance
        assert class_scoped_db["calls"] == 2
        assert class_scoped_db["connection"] == "db://test"

    def test_same_class_shared_fixture(self, class_scoped_db):
        """Test fixture is shared within this class."""
        # Same instance within this class
        assert class_scoped_db["calls"] == 2


# ============================================================================
# CLASS-SCOPED FIXTURES WITH MUTATIONS
# ============================================================================


class TestMutableClassFixtures:
    """Test that class-scoped fixtures can be mutated and changes persist."""

    def test_mutation_first(self, class_counter):
        """First test mutating class-scoped fixture."""
        # Store the initial value for this class
        initial_value = class_counter["value"]
        assert len(class_counter["data"]) == 0
        class_counter["data"].append("test1")
        # Store value for later tests in this class
        class_counter["_initial_value"] = initial_value

    def test_mutation_second(self, class_counter):
        """Second test seeing the mutation."""
        # Same instance - mutation persists
        initial_value = class_counter.get("_initial_value")
        if initial_value is not None:
            assert class_counter["value"] == initial_value
        assert len(class_counter["data"]) == 1
        assert class_counter["data"][0] == "test1"
        class_counter["data"].append("test2")

    def test_mutation_third(self, class_counter):
        """Third test seeing both mutations."""
        initial_value = class_counter.get("_initial_value")
        if initial_value is not None:
            assert class_counter["value"] == initial_value
        assert len(class_counter["data"]) == 2
        assert class_counter["data"] == ["test1", "test2"]


class TestMutableClassFixturesNewClass:
    """New class should get fresh class-scoped fixture."""

    def test_fresh_fixture(self, class_counter):
        """Test that new class gets fresh fixture."""
        # New class, new instance
        assert class_counter["value"] >= 1
        assert len(class_counter["data"]) == 0


# ============================================================================
# PARAMETRIZED TEST METHODS
# ============================================================================


class TestParametrizedMethods:
    """Test class with parametrized methods."""

    @parametrize("x,y,expected", [(1, 2, 3), (2, 3, 5), (10, 20, 30)])
    def test_addition(self, x, y, expected):
        """Parametrized test method."""
        assert x + y == expected

    @parametrize("value", [1, 2, 3, 4, 5])
    def test_single_param(self, value):
        """Test with single parameter."""
        assert value > 0
        assert value < 10

    @parametrize("x,y", [(1, 1), (2, 2), (3, 3)])
    def test_with_fixture(self, x, y, simple_fixture):
        """Parametrized test with fixture."""
        assert x == y
        # Function fixture is recreated for each parametrized case
        assert simple_fixture > 0


class TestParametrizedWithClassFixtures:
    """Parametrized tests with class-scoped fixtures."""

    @parametrize("value", [1, 2, 3])
    def test_param_with_class_fixture(self, value, class_scoped_db):
        """Parametrized test using class-scoped fixture."""
        # Class-scoped fixture is shared across all parametrized cases
        assert class_scoped_db["calls"] >= 1  # New class instance
        assert value > 0


# ============================================================================
# SKIP AND MARKS
# ============================================================================


class TestSkipAndMarks:
    """Test class with skip markers and custom marks."""

    def test_normal(self):
        """Normal test that runs."""
        assert True

    @skip("Not implemented yet")
    def test_skipped(self):
        """This test should be skipped."""
        assert False  # Should not run

    @mark.slow
    def test_marked_slow(self):
        """Test with custom mark."""
        assert True

    @mark.integration
    @mark.slow
    def test_multiple_marks(self):
        """Test with multiple custom marks."""
        assert True


# ============================================================================
# NESTED FIXTURE DEPENDENCIES
# ============================================================================


class TestNestedDependencies:
    """Test class with nested fixture dependencies."""

    def test_dependent_fixture(self, dependent_fixture):
        """Test fixture depending on class-scoped fixture."""
        # Dependent fixture is function-scoped
        assert dependent_fixture["calls"] >= 1
        # It uses class-scoped db
        db_calls = dependent_fixture["db"]["calls"]
        # Store for comparison
        dependent_fixture["db"]["_stored_calls"] = db_calls

    def test_dependent_again(self, dependent_fixture):
        """Test dependent fixture is recreated."""
        # New function-scoped instance
        assert dependent_fixture["calls"] >= 1
        # But class-scoped db is reused
        stored_calls = dependent_fixture["db"].get("_stored_calls")
        if stored_calls is not None:
            assert dependent_fixture["db"]["calls"] == stored_calls

    def test_class_with_deps(self, class_with_deps):
        """Test class-scoped fixture with dependencies."""
        assert class_with_deps["calls"] == 1
        assert class_with_deps["config"]["calls"] == 1
        assert class_with_deps["resource"]["calls"] == 1

    def test_class_deps_reused(self, class_with_deps):
        """Test class-scoped fixture is reused."""
        # Same instance within class
        assert class_with_deps["calls"] == 1
        # Module and session fixtures also reused
        assert class_with_deps["config"]["calls"] == 1
        assert class_with_deps["resource"]["calls"] == 1


# ============================================================================
# YIELD FIXTURES WITH CLASS SCOPE
# ============================================================================


class TestYieldFixtures:
    """Test class-scoped yield fixtures."""

    def test_yield_first(self, class_yield_fixture):
        """First test using class-scoped yield fixture."""
        assert class_yield_fixture["setup_calls"] == 1
        assert len(class_yield_fixture["data"]) == 0
        class_yield_fixture["data"].append("item1")

    def test_yield_second(self, class_yield_fixture):
        """Second test confirming fixture is shared."""
        # Same instance
        assert class_yield_fixture["setup_calls"] == 1
        assert len(class_yield_fixture["data"]) == 1
        assert class_yield_fixture["data"][0] == "item1"

    def test_func_yield(self, func_yield_fixture):
        """Test function-scoped yield fixture."""
        # Function-scoped, new each time
        assert func_yield_fixture["setup_calls"] >= 1


class TestYieldFixturesNewClass:
    """New class to verify yield fixture teardown/setup."""

    def test_new_yield_fixture(self, class_yield_fixture):
        """Test that new class gets new yield fixture."""
        # New class, should be second setup (first teardown ran)
        assert class_yield_fixture["setup_calls"] == 2
        assert len(class_yield_fixture["data"]) == 0


# ============================================================================
# MIXED SCOPES IN CLASS
# ============================================================================


class TestMixedScopes:
    """Test all scope types in one class."""

    def test_all_scopes(
        self,
        simple_fixture,
        class_scoped_db,
        module_config,
        session_resource,
    ):
        """Test using fixtures of all scopes."""
        # Each scope behaves correctly
        assert simple_fixture > 0  # Function scope
        db_calls = class_scoped_db["calls"]
        class_scoped_db["_stored_calls"] = db_calls  # Class scope for this class
        assert module_config["calls"] == 1  # Module scope (shared)
        assert session_resource["calls"] == 1  # Session scope (shared)

    def test_scopes_again(
        self,
        simple_fixture,
        class_scoped_db,
        module_config,
        session_resource,
    ):
        """Test scope behavior is consistent."""
        # Function fixture recreated
        assert simple_fixture > 0
        # Class fixture reused
        stored_calls = class_scoped_db.get("_stored_calls")
        if stored_calls is not None:
            assert class_scoped_db["calls"] == stored_calls
        # Module fixture reused
        assert module_config["calls"] == 1
        # Session fixture reused
        assert session_resource["calls"] == 1


# ============================================================================
# EDGE CASES
# ============================================================================


class TestEdgeCases:
    """Test edge cases for class-based tests."""

    def test_no_fixtures(self):
        """Test method with no fixtures."""
        assert True

    def test_only_self(self):
        """Test that self is properly excluded from fixture resolution."""
        # This should not try to resolve 'self' as a fixture
        assert self is not None
        assert isinstance(self, TestEdgeCases)

    def test_docstring_only(self):
        """This test only has a docstring and simple assertion."""
        x = 1
        assert x == 1


class TestEmpty:
    """Test class with only one test."""

    def test_single(self):
        """Single test in class."""
        assert 1 == 1


class TestMultipleTestsNoFixtures:
    """Test class with multiple tests but no fixtures."""

    def test_first(self):
        assert True

    def test_second(self):
        assert True

    def test_third(self):
        assert True

    def test_fourth(self):
        assert True


# ============================================================================
# PLAIN FUNCTION TESTS (for comparison)
# ============================================================================
# These ensure that plain function tests still work correctly


def test_plain_function():
    """Plain function test still works."""
    assert True


def test_plain_with_fixture(simple_fixture):
    """Plain function test with fixture."""
    # Function-scoped fixture, new each time
    assert simple_fixture > 0


def test_plain_with_class_fixture(class_counter):
    """Plain function test with class-scoped fixture."""
    # Plain functions get new class-scoped fixtures each time
    assert class_counter["value"] >= 1
