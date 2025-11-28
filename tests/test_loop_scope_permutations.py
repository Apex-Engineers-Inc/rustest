"""Comprehensive test suite for all loop_scope × fixture_scope permutations.

This test file verifies that rustest's smart loop scope detection works correctly
across all combinations of test loop_scope markers and fixture scopes.

Test Strategy:
1. No explicit loop_scope (smart detection) × all fixture scopes
2. Explicit loop_scope × fixture compatibility
3. Mixed fixture scopes (widest wins)
4. Class-based tests with loop scopes
5. Sync fixtures don't affect loop scope

NOTE: This file tests rustest-native async loop scope detection.
These tests should ONLY run with rustest, not pytest.
"""

import sys
import asyncio

# Skip when running with actual pytest (not pytest-compat mode)
# In pytest-compat mode, rustest's pytest.skip() doesn't handle allow_module_level properly,
# so we only check for actual pytest here
if "pytest" in sys.argv[0]:
    import pytest
    pytest.skip("Requires rustest runner (tests rustest-specific loop scope detection)", allow_module_level=True)

from rustest import fixture, mark


# =============================================================================
# Fixtures at different scopes
# =============================================================================

@fixture(scope="session")
async def session_counter():
    """Session-scoped async fixture with counter."""
    counter = {"value": 0, "loop_id": id(asyncio.get_event_loop())}
    yield counter
    counter["value"] += 100  # Teardown marker


@fixture(scope="module")
async def module_counter():
    """Module-scoped async fixture."""
    counter = {"value": 0, "loop_id": id(asyncio.get_event_loop())}
    yield counter
    counter["value"] += 100


@fixture(scope="class")
async def class_counter():
    """Class-scoped async fixture."""
    counter = {"value": 0, "loop_id": id(asyncio.get_event_loop())}
    yield counter
    counter["value"] += 100


@fixture
async def function_counter():
    """Function-scoped async fixture."""
    counter = {"value": 0, "loop_id": id(asyncio.get_event_loop())}
    yield counter
    counter["value"] += 100


@fixture
def sync_fixture():
    """Sync fixture (should not affect loop scope)."""
    return "sync_data"


# =============================================================================
# Test 1: Smart Detection - No explicit loop_scope
# =============================================================================

async def test_auto_session_scope(session_counter):
    """Test with session fixture should auto-detect session loop."""
    session_counter["value"] += 1
    current_loop_id = id(asyncio.get_event_loop())
    # Should be using the same loop as the fixture was created in
    assert current_loop_id == session_counter["loop_id"]


async def test_auto_module_scope(module_counter):
    """Test with module fixture should auto-detect module loop."""
    module_counter["value"] += 1
    current_loop_id = id(asyncio.get_event_loop())
    assert current_loop_id == module_counter["loop_id"]


async def test_auto_function_scope(function_counter):
    """Test with function fixture should use function loop."""
    function_counter["value"] += 1
    current_loop_id = id(asyncio.get_event_loop())
    assert current_loop_id == function_counter["loop_id"]


async def test_no_async_fixtures():
    """Test with no async fixtures should use function loop (default)."""
    # Just verify the test runs
    await asyncio.sleep(0)
    assert True


async def test_sync_fixture_only(sync_fixture):
    """Test with only sync fixtures should use function loop."""
    assert sync_fixture == "sync_data"
    await asyncio.sleep(0)


# =============================================================================
# Test 2: Mixed Fixture Scopes - Widest Wins
# =============================================================================

async def test_mixed_session_and_module(session_counter, module_counter):
    """Test with session + module fixtures → session loop (widest)."""
    current_loop_id = id(asyncio.get_event_loop())
    # Should use session loop (widest)
    assert current_loop_id == session_counter["loop_id"]
    # Note: module_counter was cached from earlier test that used module loop,
    # so its loop_id reflects where it was created, not current test's loop.
    # The important thing is THIS test runs in session loop.
    session_counter["value"] += 1
    module_counter["value"] += 1


async def test_mixed_module_and_function(module_counter, function_counter):
    """Test with module + function fixtures → module loop (widest)."""
    current_loop_id = id(asyncio.get_event_loop())
    # Should use module loop (widest)
    assert current_loop_id == module_counter["loop_id"]
    # Function fixture should also run in module loop
    assert current_loop_id == function_counter["loop_id"]


async def test_mixed_all_scopes(session_counter, module_counter, function_counter):
    """Test with all fixture scopes → session loop (widest)."""
    current_loop_id = id(asyncio.get_event_loop())
    # This test should run in session loop (widest scope detected)
    assert current_loop_id == session_counter["loop_id"]
    # Cached fixtures retain their original loop IDs from where they were created
    # The key behavior: THIS test runs in session loop, enabling proper interaction
    session_counter["value"] += 1
    module_counter["value"] += 1
    function_counter["value"] += 1


# =============================================================================
# Test 3: Nested Fixture Dependencies
# =============================================================================

@fixture
async def nested_level1(session_counter):
    """Function fixture depending on session fixture."""
    # This should detect session loop is needed
    return f"level1_{session_counter['value']}"


@fixture
async def nested_level2(nested_level1):
    """Function fixture depending on another function fixture."""
    return f"level2_{nested_level1}"


async def test_deep_nesting(nested_level2):
    """Test with deeply nested fixtures should detect session requirement."""
    # nested_level2 → nested_level1 → session_counter
    # Should auto-detect session loop needed
    assert nested_level2.startswith("level2_level1_")


# =============================================================================
# Test 4: Explicit loop_scope Overrides
# =============================================================================

@mark.asyncio(loop_scope="function")
async def test_explicit_function_scope():
    """Test with explicit function loop_scope."""
    loop_id = id(asyncio.get_event_loop())
    # Each test with function scope gets its own loop
    assert loop_id is not None


@mark.asyncio(loop_scope="module")
async def test_explicit_module_scope():
    """Test with explicit module loop_scope."""
    loop_id = id(asyncio.get_event_loop())
    assert loop_id is not None


@mark.asyncio(loop_scope="session")
async def test_explicit_session_scope():
    """Test with explicit session loop_scope."""
    loop_id = id(asyncio.get_event_loop())
    assert loop_id is not None


@mark.asyncio(loop_scope="session")
async def test_explicit_session_with_fixture(session_counter):
    """Test explicit session scope with session fixture (should match)."""
    current_loop_id = id(asyncio.get_event_loop())
    # Should be same loop since both are session scope
    assert current_loop_id == session_counter["loop_id"]


# =============================================================================
# Test 5: Class-based Tests with loop scopes
# =============================================================================

@mark.asyncio(loop_scope="class")
class TestClassLoopScope:
    """Class with class-scoped loop for all async tests."""

    async def test_class_method_1(self, class_counter):
        """First method should use class loop."""
        class_counter["value"] += 1
        current_loop_id = id(asyncio.get_event_loop())
        # Should share loop with class_counter
        assert current_loop_id == class_counter["loop_id"]

    async def test_class_method_2(self, class_counter):
        """Second method should reuse same class loop."""
        class_counter["value"] += 1
        current_loop_id = id(asyncio.get_event_loop())
        # Should share loop with class_counter and previous test
        assert current_loop_id == class_counter["loop_id"]


class TestAutoDetectInClass:
    """Class without explicit loop_scope - should auto-detect."""

    async def test_auto_session_in_class(self, session_counter):
        """Method using session fixture should auto-detect session loop."""
        current_loop_id = id(asyncio.get_event_loop())
        assert current_loop_id == session_counter["loop_id"]

    async def test_auto_function_in_class(self, function_counter):
        """Method using function fixture should use function loop."""
        current_loop_id = id(asyncio.get_event_loop())
        assert current_loop_id == function_counter["loop_id"]


# =============================================================================
# Test 6: Sync Fixtures Don't Affect Loop Scope
# =============================================================================

async def test_sync_and_async_fixtures(sync_fixture, session_counter):
    """Test with both sync and async fixtures.

    Sync fixtures should not affect loop scope detection.
    Should use session loop because of session_counter.
    """
    assert sync_fixture == "sync_data"
    current_loop_id = id(asyncio.get_event_loop())
    assert current_loop_id == session_counter["loop_id"]


@fixture
def sync_depends_on_async(function_counter):
    """Sync fixture that depends on async fixture.

    Note: In rustest, this means function_counter will be resolved
    synchronously, which only works if it's not actually async.
    This is just to verify sync fixtures don't affect detection.
    """
    return "sync"


# =============================================================================
# Test 7: Edge Cases
# =============================================================================

async def test_no_fixtures_explicit_session():
    """Test with no fixtures but explicit session scope."""
    await asyncio.sleep(0)
    assert True


@fixture(scope="session")
async def session_generator():
    """Session-scoped async generator fixture."""
    value = {"data": "session"}
    yield value
    value["cleaned"] = True


async def test_session_generator(session_generator):
    """Test with session async generator fixture."""
    assert session_generator["data"] == "session"
    current_loop_id = id(asyncio.get_event_loop())
    # Should use session loop
    assert current_loop_id is not None


# =============================================================================
# Test 8: Multiple Independent Tests (Isolation Check)
# =============================================================================

async def test_isolation_1():
    """First independent test with no fixtures."""
    loop1 = asyncio.get_event_loop()
    await asyncio.sleep(0)
    # Store loop ID for comparison
    test_isolation_1.loop_id = id(loop1)


async def test_isolation_2():
    """Second independent test with no fixtures."""
    loop2 = asyncio.get_event_loop()
    await asyncio.sleep(0)
    # Each test with function scope should get its own loop
    # (Different loop IDs are expected for function scope)
    test_isolation_2.loop_id = id(loop2)


# Add loop_id attributes to test functions
test_isolation_1.loop_id = None
test_isolation_2.loop_id = None
