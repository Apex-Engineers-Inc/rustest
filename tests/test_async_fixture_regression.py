"""Regression tests for async fixture bugs.

This file tests specific bug scenarios that were previously failing:
1. Class-based autouse fixtures with dependencies (Bug #1)
2. Session-scoped async fixtures with proper event loop management (Bug #2)
"""

from rustest import fixture, mark


# ============================================================================
# Bug #1: Class-based autouse fixtures fail to resolve dependencies
# ============================================================================


@fixture(scope="function")
async def level1_fixture():
    """First-level async fixture."""
    yield "level1"


@fixture(scope="function")
async def level2_fixture(level1_fixture: str):
    """Second-level async fixture depending on level1."""
    yield f"{level1_fixture}_level2"


# Test Case: Class-based autouse fixture with async dependency
@mark.asyncio
class TestNestedAsyncFixturesWithAutouse:
    """Test class-based autouse fixtures with async dependencies."""

    @fixture(autouse=True)
    async def setup(self, level1_fixture: str):
        """Autouse fixture that depends on another async fixture.

        This previously failed with:
        TypeError: TestNestedAsyncFixturesWithAutouse.setup()
        missing 1 required positional argument: 'level1_fixture'
        """
        self.setup_value = f"setup_{level1_fixture}"
        yield
        self.teardown_called = True

    async def test_autouse_received_dependency(self, level1_fixture: str):
        """Test that autouse fixture received its dependency."""
        assert level1_fixture == "level1"
        assert hasattr(self, "setup_value")
        assert self.setup_value == "setup_level1"

    async def test_autouse_persists(self):
        """Test that autouse fixture ran for this test too."""
        assert hasattr(self, "setup_value")
        assert self.setup_value == "setup_level1"


# Test Case: Class-based autouse with nested async dependencies
@mark.asyncio
class TestDeeplyNestedAutouseFixture:
    """Test autouse fixture with deeply nested async dependencies."""

    @fixture(autouse=True)
    async def setup_with_nested_deps(self, level2_fixture: str):
        """Autouse fixture depending on a fixture that has dependencies."""
        self.nested_value = f"setup_{level2_fixture}"
        yield

    async def test_nested_dependency_resolution(self, level2_fixture: str):
        """Test that nested dependencies are properly resolved."""
        assert level2_fixture == "level1_level2"
        assert hasattr(self, "nested_value")
        assert self.nested_value == "setup_level1_level2"


# Test Case: Multiple class-based autouse fixtures
@mark.asyncio
class TestMultipleClassAutouseFixtures:
    """Test multiple autouse fixtures in the same class."""

    @fixture(autouse=True)
    async def setup_first(self, level1_fixture: str):
        """First autouse fixture."""
        self.first_value = f"first_{level1_fixture}"
        yield

    @fixture(autouse=True)
    async def setup_second(self, level2_fixture: str):
        """Second autouse fixture with different dependency."""
        self.second_value = f"second_{level2_fixture}"
        yield

    async def test_both_autouse_ran(self):
        """Test that both autouse fixtures executed."""
        assert hasattr(self, "first_value")
        assert hasattr(self, "second_value")
        assert self.first_value == "first_level1"
        assert self.second_value == "second_level1_level2"


# ============================================================================
# Bug #2: Session-scoped async fixtures cause "Event loop is closed" errors
# ============================================================================


# Shared state to verify session fixture is reused
session_fixture_call_count = {"count": 0}


@fixture(scope="session")
async def session_async_resource():
    """Session-scoped async fixture.

    This previously failed with:
    RuntimeError: Event loop is closed

    The fixture should reuse the same event loop across all tests
    in the session, not create a new one each time.
    """
    session_fixture_call_count["count"] += 1
    resource = {
        "id": "session_resource",
        "created": True,
        "call_count": session_fixture_call_count["count"],
    }
    yield resource
    # Teardown should happen at the end of the session
    resource["closed"] = True


@fixture(scope="session")
async def session_async_generator():
    """Session-scoped async generator fixture."""
    session_fixture_call_count["count"] += 1
    data = {"type": "generator", "value": 100}
    yield data
    # Cleanup
    data["cleaned"] = True


# Test Case: Multiple tests using same session-scoped async fixture
async def test_session_fixture_1(session_async_resource):
    """First test using session fixture."""
    assert session_async_resource["id"] == "session_resource"
    assert session_async_resource["created"] is True
    # Should be called exactly once
    assert session_async_resource["call_count"] == 1


async def test_session_fixture_2(session_async_resource):
    """Second test using the same session fixture."""
    assert session_async_resource["id"] == "session_resource"
    # Should be the exact same instance, not recreated
    assert session_async_resource["call_count"] == 1


async def test_session_fixture_3(session_async_resource):
    """Third test to ensure no event loop closure issues."""
    assert session_async_resource["id"] == "session_resource"
    # Still the same instance
    assert session_async_resource["call_count"] == 1


async def test_session_generator_fixture(session_async_generator):
    """Test session-scoped async generator fixture."""
    assert session_async_generator["type"] == "generator"
    assert session_async_generator["value"] == 100


# Test Case: Session fixture with nested async dependencies
@fixture(scope="function")
async def function_async_fixture():
    """Function-scoped async fixture."""
    return "function_data"


async def test_session_and_function_fixtures(
    session_async_resource, function_async_fixture
):
    """Test mixing session and function-scoped async fixtures."""
    assert session_async_resource["id"] == "session_resource"
    assert function_async_fixture == "function_data"


# Test Case: Session fixture used in multiple classes
@mark.asyncio
class TestSessionFixtureInClassA:
    """First class using session fixture."""

    async def test_in_class_a_1(self, session_async_resource):
        """Test in first class."""
        assert session_async_resource["created"] is True

    async def test_in_class_a_2(self, session_async_resource):
        """Another test in first class."""
        assert session_async_resource["created"] is True


@mark.asyncio
class TestSessionFixtureInClassB:
    """Second class using the same session fixture."""

    async def test_in_class_b_1(self, session_async_resource):
        """Test in second class."""
        assert session_async_resource["created"] is True
        # Should still be the same session instance
        assert session_async_resource["call_count"] == 1

    async def test_in_class_b_2(self, session_async_resource):
        """Another test in second class."""
        assert session_async_resource["created"] is True


# ============================================================================
# Combined: Session async fixture used by class autouse fixture
# ============================================================================


@fixture(scope="session")
async def session_config():
    """Session-scoped config used by autouse fixture."""
    return {"environment": "test", "debug": True}


@mark.asyncio
class TestAutouseWithSessionDependency:
    """Test autouse fixture depending on session-scoped async fixture."""

    @fixture(autouse=True)
    async def setup_with_session(self, session_config: dict):
        """Autouse fixture using session-scoped async fixture.

        This combines both bugs:
        - Class-based autouse with dependency (Bug #1)
        - Dependency on session-scoped async fixture (Bug #2)
        """
        self.env = session_config["environment"]
        self.debug = session_config["debug"]
        yield

    async def test_autouse_with_session_1(self, session_config):
        """Test that autouse received session fixture."""
        assert hasattr(self, "env")
        assert self.env == "test"
        assert self.debug is True
        assert session_config["environment"] == "test"

    async def test_autouse_with_session_2(self):
        """Test autouse ran for second test too."""
        assert hasattr(self, "env")
        assert self.env == "test"


# ============================================================================
# Stress test: Many tests using session async fixture
# ============================================================================


# Generate multiple tests to stress-test the event loop management
def generate_session_stress_tests():
    """Generate many tests to ensure session event loop doesn't close prematurely."""

    for i in range(20):
        # Dynamically create test functions
        test_name = f"test_session_stress_{i}"

        async def test_func(session_async_resource):
            assert session_async_resource["created"] is True
            assert session_async_resource["call_count"] == 1

        test_func.__name__ = test_name
        globals()[test_name] = test_func


# Generate the stress tests
generate_session_stress_tests()
