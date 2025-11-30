"""Test that async session autouse fixtures prevent gathering.

This test verifies that tests depending on an async session-scoped autouse
fixture are correctly excluded from gathering and share the session loop.

NOTE: This test is designed to test rustest's specific autouse fixture
behavior and should be skipped when run under pytest.
"""

import asyncio
import sys

import pytest

from rustest import fixture

# Skip these tests when run under pytest (they test rustest-specific behavior)
pytestmark = pytest.mark.skipif(
    "pytest" in sys.modules
    and "rustest" not in sys.modules.get("__main__", "").__class__.__module__,
    reason="These tests verify rustest-specific async autouse fixture behavior",
)

# Storage for loop IDs
session_fixture_loop_id: int | None = None
test_loop_ids: list[int] = []


@fixture(scope="session", autouse=True)
async def async_session_autouse():
    """Async session-scoped autouse fixture.

    Tests that have this fixture in scope should NOT be gathered,
    because they need to share the session-scoped event loop.
    """
    global session_fixture_loop_id
    session_fixture_loop_id = id(asyncio.get_running_loop())
    yield "session_value"


async def test_with_session_autouse_1():
    """Test that uses session autouse fixture - should NOT be gathered."""
    loop_id = id(asyncio.get_running_loop())
    test_loop_ids.append(loop_id)
    # Verify we're using the same loop as the fixture
    assert loop_id == session_fixture_loop_id, (
        f"Test should use same loop as session fixture. "
        f"Test loop: {loop_id}, Fixture loop: {session_fixture_loop_id}"
    )


async def test_with_session_autouse_2():
    """Another test using session autouse - should share fixture's loop."""
    loop_id = id(asyncio.get_running_loop())
    test_loop_ids.append(loop_id)
    # Verify we're using the same loop as the fixture
    assert loop_id == session_fixture_loop_id, (
        f"Test should use same loop as session fixture. "
        f"Test loop: {loop_id}, Fixture loop: {session_fixture_loop_id}"
    )


def test_verify_session_loop_sharing():
    """Verify tests with session autouse share the fixture's loop."""
    assert len(test_loop_ids) == 2, f"Expected 2 loop IDs, got {len(test_loop_ids)}"
    # Both tests should share the session fixture's loop
    assert test_loop_ids[0] == test_loop_ids[1], (
        f"Tests with session autouse should share loops, "
        f"got {test_loop_ids[0]} and {test_loop_ids[1]}"
    )
    assert test_loop_ids[0] == session_fixture_loop_id, (
        "Tests should use the session fixture's loop"
    )
