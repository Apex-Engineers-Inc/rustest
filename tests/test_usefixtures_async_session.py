"""Test that @mark.usefixtures properly affects async test gathering.

This test verifies that tests using @mark.usefixtures("session_fixture") with
an async session-scoped fixture are correctly excluded from gathering and
share the session loop.

NOTE: This test is designed to test rustest-specific behavior and should be
skipped when run under pytest.
"""

import asyncio
import sys

import pytest

from rustest import fixture, mark

# Skip these tests when run under pytest (they test rustest-specific behavior)
pytestmark = pytest.mark.skipif(
    "pytest" in sys.modules
    and "rustest" not in sys.modules.get("__main__", "").__class__.__module__,
    reason="These tests verify rustest-specific usefixtures behavior",
)

# Storage for loop IDs
fixture_loop_id: int | None = None
test_loop_ids: list[int] = []


@fixture(scope="session")
async def async_session_resource():
    """Async session-scoped fixture.

    Tests that request this fixture (via usefixtures or parameter) should NOT
    be gathered because they need to share the session-scoped event loop.
    """
    global fixture_loop_id
    fixture_loop_id = id(asyncio.get_running_loop())
    yield "session_resource"


@mark.usefixtures("async_session_resource")
async def test_usefixtures_session_1():
    """Test using usefixtures to request async session fixture."""
    loop_id = id(asyncio.get_running_loop())
    test_loop_ids.append(loop_id)
    # Verify we're using the same loop as the fixture
    assert loop_id == fixture_loop_id, (
        f"Test should use same loop as session fixture. "
        f"Test loop: {loop_id}, Fixture loop: {fixture_loop_id}"
    )


@mark.usefixtures("async_session_resource")
async def test_usefixtures_session_2():
    """Another test using usefixtures to request async session fixture."""
    loop_id = id(asyncio.get_running_loop())
    test_loop_ids.append(loop_id)
    # Verify we're using the same loop as the fixture
    assert loop_id == fixture_loop_id, (
        f"Test should use same loop as session fixture. "
        f"Test loop: {loop_id}, Fixture loop: {fixture_loop_id}"
    )


def test_verify_usefixtures_loop_sharing():
    """Verify tests with usefixtures share the fixture's loop."""
    assert len(test_loop_ids) == 2, f"Expected 2 loop IDs, got {len(test_loop_ids)}"
    # Both tests should share the session fixture's loop
    assert test_loop_ids[0] == test_loop_ids[1], (
        f"Tests with usefixtures should share loops, got {test_loop_ids[0]} and {test_loop_ids[1]}"
    )
    assert test_loop_ids[0] == fixture_loop_id, "Tests should use the session fixture's loop"
