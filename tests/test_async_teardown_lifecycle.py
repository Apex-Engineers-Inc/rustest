"""Regression tests for async event loop lifecycle and fixture teardown.

Tests that async fixture teardown (the code after `yield`) runs to completion,
and that event loops are properly shut down — not just cancelled and abandoned.

Previously, close_event_loop() cancelled pending tasks but didn't await their
completion before closing the loop. This caused:
- Async fixture teardown code never running (resource leaks)
- Database connections not returned to pools
- "Future attached to a different loop" errors
- GC-triggered cleanup warnings for leaked connections

See: GitHub issue #122
"""

import sys

# Skip this entire module when running with pytest
# These tests use rustest's async fixtures which require rustest runner
if "pytest" in sys.argv[0]:
    import pytest

    pytest.skip(
        "This test file requires rustest runner (rustest-only tests)",
        allow_module_level=True,
    )

from rustest import fixture, mark


# ============================================================================
# Track async fixture lifecycle to verify teardown runs
# ============================================================================

_teardown_tracker: dict[str, bool] = {}


def _reset_tracker() -> None:
    _teardown_tracker.clear()


# ============================================================================
# Function-scoped async generator fixtures
# ============================================================================


@fixture(scope="function")
async def tracked_function_resource():
    """Function-scoped async fixture that tracks teardown completion."""
    resource_id = "func_resource"
    _teardown_tracker[resource_id] = False  # Mark as not yet torn down
    yield resource_id
    # This teardown MUST run — previously it was skipped when the event loop
    # was closed without awaiting task cancellation.
    _teardown_tracker[resource_id] = True


async def test_function_async_teardown_runs(tracked_function_resource):
    """Verify function-scoped async fixture provides its value."""
    assert tracked_function_resource == "func_resource"


async def test_previous_function_teardown_completed():
    """Verify the previous test's function fixture teardown actually ran.

    This is the core regression test: if close_event_loop doesn't await
    task cancellation, the teardown after yield never executes.
    """
    assert _teardown_tracker.get("func_resource") is True, (
        "Function-scoped async fixture teardown did not run! "
        "This indicates close_event_loop is not properly awaiting task cancellation."
    )


# ============================================================================
# Module-scoped async generator fixtures with teardown tracking
# ============================================================================

_module_teardown_events: list[str] = []


@fixture(scope="module")
async def tracked_module_resource():
    """Module-scoped async fixture with tracked teardown."""
    _module_teardown_events.append("setup")
    yield {"name": "module_resource", "active": True}
    _module_teardown_events.append("teardown")


async def test_module_fixture_setup(tracked_module_resource):
    """First test using module fixture."""
    assert tracked_module_resource["name"] == "module_resource"
    assert tracked_module_resource["active"] is True
    assert "setup" in _module_teardown_events


async def test_module_fixture_shared(tracked_module_resource):
    """Second test using same module fixture (should be shared, not re-created)."""
    assert tracked_module_resource["name"] == "module_resource"
    # Teardown should NOT have run yet since we're still in the same module
    assert "teardown" not in _module_teardown_events


# ============================================================================
# Multiple function-scoped async fixtures per test
# ============================================================================

_multi_fixture_teardown: list[str] = []


@fixture(scope="function")
async def resource_a():
    """First function-scoped async fixture."""
    _multi_fixture_teardown.append("a_setup")
    yield "resource_a"
    _multi_fixture_teardown.append("a_teardown")


@fixture(scope="function")
async def resource_b():
    """Second function-scoped async fixture."""
    _multi_fixture_teardown.append("b_setup")
    yield "resource_b"
    _multi_fixture_teardown.append("b_teardown")


async def test_multiple_async_fixtures(resource_a, resource_b):
    """Test with multiple function-scoped async fixtures."""
    assert resource_a == "resource_a"
    assert resource_b == "resource_b"


async def test_multiple_fixtures_teardown_completed():
    """Verify both fixtures from previous test were torn down."""
    assert "a_teardown" in _multi_fixture_teardown, (
        "Async fixture 'resource_a' teardown did not run"
    )
    assert "b_teardown" in _multi_fixture_teardown, (
        "Async fixture 'resource_b' teardown did not run"
    )


# ============================================================================
# Async fixture with simulated resource cleanup (like DB connections)
# ============================================================================

_cleanup_log: list[str] = []


@fixture(scope="function")
async def simulated_db_connection():
    """Simulates an async DB connection with proper cleanup.

    This pattern is common in SQLAlchemy/asyncpg test suites where
    connections must be properly closed to avoid pool exhaustion.
    """
    import asyncio

    # Simulate connection setup
    _cleanup_log.append("connection_opened")
    connection = {"status": "open", "queries": 0}
    yield connection
    # Simulate connection teardown (this is what was leaking before)
    connection["status"] = "closed"
    _cleanup_log.append("connection_closed")
    # Simulate async cleanup work (like returning connection to pool)
    await asyncio.sleep(0)
    _cleanup_log.append("pool_returned")


async def test_db_connection_works(simulated_db_connection):
    """Use the simulated connection."""
    assert simulated_db_connection["status"] == "open"
    simulated_db_connection["queries"] += 1


async def test_db_connection_cleanup_completed():
    """Verify the connection was properly closed and returned to pool."""
    assert "connection_closed" in _cleanup_log, "DB connection was not closed during teardown"
    assert "pool_returned" in _cleanup_log, (
        "Connection was not returned to pool — "
        "event loop was likely closed before teardown completed"
    )


# ============================================================================
# Rapid event loop creation/destruction (stress test)
# ============================================================================


@fixture(scope="function")
async def ephemeral_resource():
    """Short-lived async resource to stress-test event loop lifecycle."""
    import asyncio

    await asyncio.sleep(0)  # Force a real async operation
    yield "ephemeral"
    await asyncio.sleep(0)  # Async teardown


@mark.asyncio
class TestEventLoopStress:
    """Rapidly create and destroy function-scoped event loops."""

    async def test_stress_0(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_1(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_2(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_3(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_4(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_5(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_6(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_7(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_8(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"

    async def test_stress_9(self, ephemeral_resource):
        assert ephemeral_resource == "ephemeral"
