"""Regression tests for async fixture bugs.

This file tests specific bug scenarios that were previously failing:
1. Session-scoped async fixtures with proper event loop management (Bug #2)

Note: Class-based autouse fixtures with dependencies are not currently supported
in the same way as pytest. Module-level autouse fixtures work correctly.
"""

import sys

# Skip this entire module when running with pytest
# These tests use rustest's async fixtures which require rustest runner
if "pytest" in sys.argv[0]:
    import pytest
    pytest.skip("This test file requires rustest runner (rustest-only tests)", allow_module_level=True)

from rustest import fixture, mark


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
