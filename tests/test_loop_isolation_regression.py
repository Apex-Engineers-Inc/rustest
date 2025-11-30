"""Regression tests for event loop isolation under async test gathering.

These tests verify that:
1. Tests with loop_scope="function" get isolated loops (not gathered)
2. Tests with loop_scope="class" share loops within a class but not across classes
3. Tests with loop_scope="module" can be safely gathered

These tests would fail if the gather implementation incorrectly groups tests
that should have isolated event loops.

NOTE: These tests are designed to test rustest's specific loop isolation
behavior and should be skipped when run under pytest.
"""

import asyncio
import sys

import pytest

from rustest import mark

# Skip these tests when run under pytest (they test rustest-specific behavior)
pytestmark = pytest.mark.skipif(
    "pytest" in sys.modules
    and "rustest" not in sys.modules.get("__main__", "").__class__.__module__,
    reason="These tests verify rustest-specific loop isolation behavior",
)

# Global storage for loop IDs to compare across tests
function_scope_loop_ids: list[int] = []
class_a_loop_ids: list[int] = []
class_b_loop_ids: list[int] = []
module_scope_loop_ids: list[int] = []


# =============================================================================
# Test 1: Function-scoped tests should get isolated loops (NOT gathered)
# =============================================================================


@mark.asyncio(loop_scope="function")
async def test_function_scope_loop_1():
    """First function-scoped test - should get its own loop."""
    loop_id = id(asyncio.get_running_loop())
    function_scope_loop_ids.append(loop_id)


@mark.asyncio(loop_scope="function")
async def test_function_scope_loop_2():
    """Second function-scoped test - should get a DIFFERENT loop."""
    loop_id = id(asyncio.get_running_loop())
    function_scope_loop_ids.append(loop_id)


def test_function_scope_loops_are_different():
    """Verify that function-scoped tests got different loops.

    This test runs after the two function-scoped tests and verifies they
    received different event loops. If gathering incorrectly included
    function-scoped tests, they would share the same loop.
    """
    assert len(function_scope_loop_ids) == 2, (
        f"Expected 2 loop IDs, got {len(function_scope_loop_ids)}"
    )
    assert function_scope_loop_ids[0] != function_scope_loop_ids[1], (
        f"Function-scoped tests should have DIFFERENT loops, "
        f"but both got loop id {function_scope_loop_ids[0]}"
    )


# =============================================================================
# Test 2: Class-scoped tests should share loops within class, not across
# =============================================================================


class TestClassA:
    """First test class with class-scoped async tests."""

    @mark.asyncio(loop_scope="class")
    async def test_class_a_loop_1(self):
        """First test in class A."""
        loop_id = id(asyncio.get_running_loop())
        class_a_loop_ids.append(loop_id)

    @mark.asyncio(loop_scope="class")
    async def test_class_a_loop_2(self):
        """Second test in class A - should share loop with first."""
        loop_id = id(asyncio.get_running_loop())
        class_a_loop_ids.append(loop_id)

    def test_class_a_loops_shared(self):
        """Verify tests in class A share the same loop."""
        assert len(class_a_loop_ids) == 2, (
            f"Expected 2 loop IDs for class A, got {len(class_a_loop_ids)}"
        )
        assert class_a_loop_ids[0] == class_a_loop_ids[1], (
            f"Tests in class A should share the same loop, "
            f"got {class_a_loop_ids[0]} and {class_a_loop_ids[1]}"
        )


class TestClassB:
    """Second test class with class-scoped async tests."""

    @mark.asyncio(loop_scope="class")
    async def test_class_b_loop_1(self):
        """First test in class B - should have DIFFERENT loop from class A."""
        loop_id = id(asyncio.get_running_loop())
        class_b_loop_ids.append(loop_id)

    @mark.asyncio(loop_scope="class")
    async def test_class_b_loop_2(self):
        """Second test in class B - should share loop within class B."""
        loop_id = id(asyncio.get_running_loop())
        class_b_loop_ids.append(loop_id)

    def test_class_b_loops_shared(self):
        """Verify tests in class B share the same loop."""
        assert len(class_b_loop_ids) == 2, (
            f"Expected 2 loop IDs for class B, got {len(class_b_loop_ids)}"
        )
        assert class_b_loop_ids[0] == class_b_loop_ids[1], (
            f"Tests in class B should share the same loop, "
            f"got {class_b_loop_ids[0]} and {class_b_loop_ids[1]}"
        )


class TestClassIsolation:
    """Verify different classes have different loops."""

    def test_class_isolation(self):
        """Verify class A and class B have different loops."""
        # This runs after both TestClassA and TestClassB
        assert len(class_a_loop_ids) >= 1, "TestClassA tests haven't run yet"
        assert len(class_b_loop_ids) >= 1, "TestClassB tests haven't run yet"
        assert class_a_loop_ids[0] != class_b_loop_ids[0], (
            f"Class A and class B should have DIFFERENT loops, "
            f"but both got loop id {class_a_loop_ids[0]}"
        )


# =============================================================================
# Test 3: Module-scoped tests CAN be gathered (share loop)
# =============================================================================


@mark.asyncio(loop_scope="module")
async def test_module_scope_loop_1():
    """First module-scoped test - can be gathered."""
    loop_id = id(asyncio.get_running_loop())
    module_scope_loop_ids.append(loop_id)


@mark.asyncio(loop_scope="module")
async def test_module_scope_loop_2():
    """Second module-scoped test - can share loop with first."""
    loop_id = id(asyncio.get_running_loop())
    module_scope_loop_ids.append(loop_id)


def test_module_scope_loops_can_be_shared():
    """Verify module-scoped tests can share loops (gathering is allowed).

    Unlike function-scoped tests, module-scoped tests are allowed to be
    gathered and share the same event loop.
    """
    assert len(module_scope_loop_ids) == 2, (
        f"Expected 2 loop IDs for module scope, got {len(module_scope_loop_ids)}"
    )
    # Module-scoped tests CAN share a loop - they may or may not depending on
    # implementation details, so we just verify they ran successfully
    # The key point is they don't REQUIRE isolation like function-scoped tests
