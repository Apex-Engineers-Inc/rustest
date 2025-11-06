"""Tests for yield-based fixtures with setup/teardown."""

from rustest import fixture

# Track fixture lifecycle for verification
_lifecycle_events = []


def reset_lifecycle():
    """Reset lifecycle tracking between tests."""
    global _lifecycle_events
    _lifecycle_events = []


def track_event(event: str) -> None:
    """Track a lifecycle event."""
    _lifecycle_events.append(event)


def get_events() -> list[str]:
    """Get all tracked lifecycle events."""
    return _lifecycle_events.copy()


# ============================================================================
# FUNCTION-SCOPED YIELD FIXTURES
# ============================================================================


@fixture
def function_yield_fixture():
    """Function-scoped fixture with setup and teardown."""
    track_event("function_setup")
    yield "function_value"
    track_event("function_teardown")


def test_function_yield_1(function_yield_fixture):
    """Test that function-scoped yield fixture provides value."""
    assert function_yield_fixture == "function_value"
    assert "function_setup" in get_events()
    # Teardown hasn't run yet
    assert "function_teardown" not in get_events()


def test_function_yield_2(function_yield_fixture):
    """Test that function-scoped fixture is recreated for each test."""
    assert function_yield_fixture == "function_value"
    # Previous test's teardown should have run
    events = get_events()
    # This test should see: [prev_setup, prev_teardown, new_setup]
    setup_count = events.count("function_setup")
    teardown_count = events.count("function_teardown")
    assert setup_count >= 2  # At least 2 setups
    assert teardown_count >= 1  # At least 1 teardown from previous test


# ============================================================================
# MODULE-SCOPED YIELD FIXTURES
# ============================================================================


@fixture(scope="module")
def module_yield_fixture():
    """Module-scoped fixture with setup and teardown."""
    track_event("module_setup")
    yield "module_value"
    track_event("module_teardown")


def test_module_yield_1(module_yield_fixture):
    """Test that module-scoped yield fixture provides value."""
    # Don't reset - we want to see the setup event that already happened
    assert module_yield_fixture == "module_value"
    assert "module_setup" in get_events()
    assert "module_teardown" not in get_events()


def test_module_yield_2(module_yield_fixture):
    """Test that module-scoped fixture is reused."""
    assert module_yield_fixture == "module_value"
    events = get_events()
    # Should still be only 1 setup, no teardown yet
    assert events.count("module_setup") == 1
    assert "module_teardown" not in events


# ============================================================================
# CLASS-SCOPED YIELD FIXTURES
# ============================================================================


@fixture(scope="class")
def class_yield_fixture():
    """Class-scoped fixture with setup and teardown."""
    track_event("class_setup")
    yield "class_value"
    track_event("class_teardown")


class TestClassYieldFixture:
    """Test class for class-scoped yield fixtures."""

    def test_class_yield_1(self, class_yield_fixture):
        """Test class-scoped yield fixture."""
        # Don't reset - we want to see the setup event that already happened
        assert class_yield_fixture == "class_value"
        assert "class_setup" in get_events()
        assert "class_teardown" not in get_events()

    def test_class_yield_2(self, class_yield_fixture):
        """Test that class-scoped fixture is reused within class."""
        assert class_yield_fixture == "class_value"
        events = get_events()
        # Should still be only 1 setup
        assert events.count("class_setup") == 1
        assert "class_teardown" not in events


# ============================================================================
# SESSION-SCOPED YIELD FIXTURES
# ============================================================================


@fixture(scope="session")
def session_yield_fixture():
    """Session-scoped fixture with setup and teardown."""
    track_event("session_setup")
    yield "session_value"
    track_event("session_teardown")


def test_session_yield_1(session_yield_fixture):
    """Test session-scoped yield fixture."""
    assert session_yield_fixture == "session_value"


def test_session_yield_2(session_yield_fixture):
    """Test that session-scoped fixture is reused."""
    assert session_yield_fixture == "session_value"


# ============================================================================
# NESTED YIELD FIXTURES
# ============================================================================


@fixture
def outer_yield():
    """Outer fixture with setup and teardown."""
    track_event("outer_setup")
    yield "outer"
    track_event("outer_teardown")


@fixture
def inner_yield(outer_yield):
    """Inner fixture depending on outer, both with teardown."""
    track_event("inner_setup")
    yield f"inner_with_{outer_yield}"
    track_event("inner_teardown")


def test_nested_yield(inner_yield):
    """Test nested yield fixtures."""
    # Don't reset - we want to see the setup events that already happened
    assert inner_yield == "inner_with_outer"
    events = get_events()
    assert "outer_setup" in events
    assert "inner_setup" in events
    # Teardowns haven't run yet
    assert "outer_teardown" not in events
    assert "inner_teardown" not in events


def test_nested_yield_teardown_order():
    """Verify teardown order for nested fixtures (LIFO)."""
    # After the previous test, both teardowns should have run
    # Inner should teardown before outer (LIFO)
    events = get_events()
    if "inner_teardown" in events and "outer_teardown" in events:
        inner_idx = events.index("inner_teardown")
        outer_idx = events.index("outer_teardown")
        assert inner_idx < outer_idx, "Inner should teardown before outer"


# ============================================================================
# MIXED REGULAR AND YIELD FIXTURES
# ============================================================================


@fixture
def regular_fixture():
    """Regular fixture without yield."""
    track_event("regular_fixture")
    return "regular_value"


@fixture
def yield_fixture_mixed():
    """Yield fixture to mix with regular fixture."""
    track_event("yield_setup")
    yield "yield_value"
    track_event("yield_teardown")


def test_mixed_fixtures(regular_fixture, yield_fixture_mixed):
    """Test mixing regular and yield fixtures."""
    # Don't reset - we want to see the setup events that already happened
    assert regular_fixture == "regular_value"
    assert yield_fixture_mixed == "yield_value"
    events = get_events()
    assert "regular_fixture" in events
    assert "yield_setup" in events
    assert "yield_teardown" not in events


# ============================================================================
# YIELD FIXTURE WITH RESOURCE MANAGEMENT
# ============================================================================


class DatabaseMock:
    """Mock database for testing."""

    def __init__(self):
        self.connected = False
        self.transactions = []
        track_event("db_created")

    def connect(self):
        self.connected = True
        track_event("db_connected")

    def disconnect(self):
        self.connected = False
        track_event("db_disconnected")

    def execute(self, query: str):
        if not self.connected:
            raise RuntimeError("Database not connected")
        self.transactions.append(query)
        return f"result_{len(self.transactions)}"


@fixture
def database():
    """Fixture that manages database connection lifecycle."""
    db = DatabaseMock()
    db.connect()
    yield db
    db.disconnect()


def test_database_fixture_1(database):
    """Test database fixture provides connected database."""
    # Don't reset - we want to see the setup events that already happened
    assert database.connected
    result = database.execute("SELECT * FROM users")
    assert result == "result_1"
    assert "db_connected" in get_events()
    assert "db_disconnected" not in get_events()


def test_database_fixture_2(database):
    """Test database fixture is recreated with fresh state."""
    assert database.connected
    # Should be a new database instance with no previous transactions
    assert len(database.transactions) == 0
    result = database.execute("INSERT INTO users VALUES (1)")
    assert result == "result_1"  # First transaction in this test


# ============================================================================
# YIELD FIXTURE WITH MULTIPLE VALUES
# ============================================================================


@fixture
def tuple_yield():
    """Yield fixture that returns a tuple."""
    track_event("tuple_setup")
    yield (1, 2, 3)
    track_event("tuple_teardown")


def test_tuple_yield(tuple_yield):
    """Test yield fixture with tuple value."""
    # Don't reset - just test the fixture value
    assert tuple_yield == (1, 2, 3)
    assert len(tuple_yield) == 3


@fixture
def dict_yield():
    """Yield fixture that returns a dict."""
    track_event("dict_setup")
    yield {"key": "value", "number": 42}
    track_event("dict_teardown")


def test_dict_yield(dict_yield):
    """Test yield fixture with dict value."""
    # Don't reset - just test the fixture value
    assert dict_yield["key"] == "value"
    assert dict_yield["number"] == 42
