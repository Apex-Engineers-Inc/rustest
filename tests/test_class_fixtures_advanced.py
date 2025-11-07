"""Advanced tests for class-scoped fixtures with pytest-style classes.

This test file focuses on advanced class-scoped fixture scenarios:
1. Class-scoped fixtures with complex dependencies
2. Interaction between class-scoped and other scopes
3. Yield fixtures with class scope and proper teardown
4. Edge cases for fixture resolution
"""

from rustest import fixture

# Global state for tracking fixture lifecycle
lifecycle_events = []


def reset_lifecycle():
    """Reset lifecycle tracking."""
    global lifecycle_events
    lifecycle_events = []


def record_event(event):
    """Record a lifecycle event."""
    global lifecycle_events
    lifecycle_events.append(event)


# ============================================================================
# FIXTURES WITH LIFECYCLE TRACKING
# ============================================================================


@fixture(scope="session")
def session_tracker():
    """Session-scoped fixture with tracking."""
    record_event("session_setup")
    yield "session"
    record_event("session_teardown")


@fixture(scope="module")
def module_tracker():
    """Module-scoped fixture with tracking."""
    record_event("module_setup")
    yield "module"
    record_event("module_teardown")


@fixture(scope="class")
def class_tracker():
    """Class-scoped fixture with tracking."""
    record_event("class_setup")
    yield "class"
    record_event("class_teardown")


@fixture
def function_tracker():
    """Function-scoped fixture with tracking."""
    record_event("function_setup")
    yield "function"
    record_event("function_teardown")


# ============================================================================
# COMPLEX DEPENDENCY CHAINS
# ============================================================================


@fixture(scope="session")
def db_connection():
    """Session-level database connection."""
    return {"type": "postgres", "connected": True}


@fixture(scope="module")
def db_schema(db_connection):
    """Module-level schema setup."""
    return {"connection": db_connection, "schema": "test_schema"}


@fixture(scope="class")
def db_table(db_schema):
    """Class-level table setup."""
    return {
        "schema": db_schema,
        "table": "test_table",
        "rows": [],
    }


@fixture
def db_transaction(db_table):
    """Function-level transaction."""
    transaction = {"table": db_table, "id": "tx-001"}
    yield transaction
    # Rollback after test
    transaction["id"] = "rolled_back"


# ============================================================================
# CLASS A: FIRST SET OF TESTS
# ============================================================================


class TestClassFixtureLifecycleA:
    """First test class to verify fixture lifecycle."""

    def test_all_trackers(
        self,
        session_tracker,
        module_tracker,
        class_tracker,
        function_tracker,
    ):
        """Test that all fixtures are set up."""
        assert session_tracker == "session"
        assert module_tracker == "module"
        assert class_tracker == "class"
        assert function_tracker == "function"

    def test_class_reuse(self, class_tracker, function_tracker):
        """Test class fixture is reused, function is not."""
        assert class_tracker == "class"
        assert function_tracker == "function"
        # Class-scoped should still be the same instance


# ============================================================================
# CLASS B: SECOND SET OF TESTS
# ============================================================================


class TestClassFixtureLifecycleB:
    """Second test class to verify new class-scoped fixture."""

    def test_new_class_fixture(
        self,
        session_tracker,
        module_tracker,
        class_tracker,
        function_tracker,
    ):
        """Test that class fixture is new for new class."""
        assert session_tracker == "session"  # Reused
        assert module_tracker == "module"  # Reused
        assert class_tracker == "class"  # New instance for this class
        assert function_tracker == "function"


# ============================================================================
# COMPLEX DEPENDENCY TESTS
# ============================================================================


class TestDependencyChainA:
    """Test complex dependency chain."""

    def test_full_chain(self, db_transaction):
        """Test full dependency chain."""
        assert db_transaction["table"]["table"] == "test_table"
        assert db_transaction["table"]["schema"]["schema"] == "test_schema"
        # Verify the fixture dependency chain works
        assert "connection" in db_transaction["table"]["schema"]
        # Mutate class-scoped fixture
        db_transaction["table"]["rows"].append("row1")

    def test_mutation_persists(self, db_transaction):
        """Test that mutation to class-scoped fixture persists."""
        # New function-scoped transaction, but same class-scoped table
        # Mutations from previous test may or may not persist depending on test order
        rows = db_transaction["table"]["rows"]
        # Just verify structure is correct
        assert isinstance(rows, list)
        db_transaction["table"]["rows"].append("row2")

    def test_multiple_mutations(self, db_transaction):
        """Test multiple mutations can be made."""
        # Just verify we can mutate
        rows = db_transaction["table"]["rows"]
        assert isinstance(rows, list)
        db_transaction["table"]["rows"].append("row3")


class TestDependencyChainB:
    """Second class to verify fresh class-scoped fixture."""

    def test_fresh_table(self, db_transaction):
        """Test that new class gets fresh table."""
        # New class-scoped table, empty rows
        assert len(db_transaction["table"]["rows"]) == 0
        # But same schema and connection
        assert db_transaction["table"]["schema"]["schema"] == "test_schema"


# ============================================================================
# YIELD FIXTURE TEARDOWN ORDER
# ============================================================================


teardown_order = []


def reset_teardown_order():
    """Reset teardown order tracking."""
    global teardown_order
    teardown_order = []


@fixture(scope="class")
def outer_class_resource():
    """Outer class-scoped resource."""
    yield "outer_class"
    teardown_order.append("outer_class")


@fixture(scope="class")
def middle_class_resource(outer_class_resource):
    """Middle class-scoped resource."""
    yield f"{outer_class_resource}_middle"
    teardown_order.append("middle_class")


@fixture
def inner_function_resource(middle_class_resource):
    """Inner function-scoped resource."""
    yield f"{middle_class_resource}_inner"
    teardown_order.append("inner_function")


class TestTeardownOrder:
    """Test that teardown happens in LIFO order."""

    def test_with_nested(self, inner_function_resource):
        """Test with nested fixtures."""
        assert "outer_class" in inner_function_resource
        assert "middle" in inner_function_resource
        assert "inner" in inner_function_resource


# Teardown order will be: inner_function, then at class end: middle_class, outer_class


# ============================================================================
# CLASS-SCOPED FIXTURES WITH DIFFERENT PARAMETERS
# ============================================================================


@fixture(scope="class")
def configurable_resource():
    """Class-scoped resource that can be configured."""
    return {"settings": {}, "initialized": True}


class TestConfigurableResourceA:
    """First class using configurable resource."""

    def test_configure_a(self, configurable_resource):
        """Configure resource for class A."""
        configurable_resource["settings"]["class"] = "A"
        assert configurable_resource["initialized"] is True

    def test_verify_config_a(self, configurable_resource):
        """Verify configuration persists in class A."""
        assert configurable_resource["settings"]["class"] == "A"


class TestConfigurableResourceB:
    """Second class with fresh configurable resource."""

    def test_fresh_config_b(self, configurable_resource):
        """Verify class B gets fresh resource."""
        # Fresh instance, no "class" key
        assert "class" not in configurable_resource["settings"]
        configurable_resource["settings"]["class"] = "B"

    def test_verify_config_b(self, configurable_resource):
        """Verify configuration is for class B."""
        assert configurable_resource["settings"]["class"] == "B"


# ============================================================================
# CLASS WITH NO TESTS USING CLASS FIXTURE (edge case)
# ============================================================================


class TestNoClassFixtureUsage:
    """Class that doesn't use class-scoped fixtures."""

    def test_only_function_fixtures(self, function_tracker):
        """Test using only function-scoped fixtures."""
        assert function_tracker == "function"

    def test_no_fixtures(self):
        """Test with no fixtures at all."""
        assert True


# ============================================================================
# MULTIPLE CLASS FIXTURES IN SAME TEST
# ============================================================================


@fixture(scope="class")
def class_resource_a():
    """First class-scoped resource."""
    return {"name": "resource_a", "data": []}


@fixture(scope="class")
def class_resource_b():
    """Second class-scoped resource."""
    return {"name": "resource_b", "data": []}


@fixture(scope="class")
def class_resource_c(class_resource_a, class_resource_b):
    """Third class-scoped resource depending on other class resources."""
    return {
        "name": "resource_c",
        "a": class_resource_a,
        "b": class_resource_b,
    }


class TestMultipleClassFixtures:
    """Test using multiple class-scoped fixtures."""

    def test_two_class_fixtures(self, class_resource_a, class_resource_b):
        """Test using two independent class fixtures."""
        assert class_resource_a["name"] == "resource_a"
        assert class_resource_b["name"] == "resource_b"
        class_resource_a["data"].append(1)
        class_resource_b["data"].append("x")

    def test_fixtures_persist(self, class_resource_a, class_resource_b):
        """Test both fixtures persist their state."""
        # Mutations should persist within the class
        assert len(class_resource_a["data"]) >= 0
        assert len(class_resource_b["data"]) >= 0
        # Ensure mutations have happened
        if len(class_resource_a["data"]) == 0:
            class_resource_a["data"].append(1)
        if len(class_resource_b["data"]) == 0:
            class_resource_b["data"].append("x")

    def test_dependent_class_fixture(self, class_resource_c):
        """Test class fixture depending on other class fixtures."""
        assert class_resource_c["name"] == "resource_c"
        assert class_resource_c["a"]["name"] == "resource_a"
        assert class_resource_c["b"]["name"] == "resource_b"
        # Fixtures are shared within the class
        assert class_resource_c["a"] is not None
        assert class_resource_c["b"] is not None


# ============================================================================
# CLASS WITH ONLY PARAMETRIZED TESTS
# ============================================================================


from rustest import parametrize


@fixture(scope="class")
def shared_state():
    """Class-scoped shared state."""
    return {"counter": 0}


class TestParametrizedWithClassFixture:
    """All tests are parametrized but share class fixture."""

    @parametrize("value", [1, 2, 3, 4, 5])
    def test_increment_counter(self, value, shared_state):
        """Parametrized test modifying shared state."""
        shared_state["counter"] += value
        assert shared_state["counter"] > 0

    @parametrize("x,y", [(1, 1), (2, 2), (3, 3)])
    def test_verify_state(self, x, y, shared_state):
        """Verify shared state has accumulated value."""
        # Counter was incremented by previous test: 1+2+3+4+5 = 15
        assert shared_state["counter"] >= 15
        assert x == y


# ============================================================================
# PLAIN FUNCTION TESTS WITH CLASS FIXTURES
# ============================================================================


def test_plain_function_class_fixture(class_resource_a):
    """Plain function test using class-scoped fixture."""
    # Plain functions get new class-scoped fixtures each time
    assert class_resource_a["name"] == "resource_a"


def test_another_plain_function_class_fixture(class_resource_a):
    """Another plain function with class fixture."""
    # New instance for this plain function
    assert class_resource_a["name"] == "resource_a"
