"""Regression test: autouse async fixtures should use correct event loop scope.

When a session-scoped async fixture creates a resource (like a database connection)
and a function-scoped autouse async fixture interacts with that resource,
both must share the same event loop to avoid "attached to a different loop" errors.

This covers both setup AND teardown: the yield-based autouse fixture runs its
teardown (after yield) on the test's effective event loop, not a new one.
"""

import sys

# Skip when running with pytest - this tests rustest-specific event loop management
if "pytest" in sys.argv[0]:
    import pytest
    pytest.skip("This test file requires rustest runner", allow_module_level=True)

from rustest import fixture


class AsyncResource:
    """Simulates an async resource bound to a specific event loop."""

    def __init__(self):
        import asyncio
        self._loop = asyncio.get_event_loop()
        self.items: list[str] = []

    async def add(self, item: str) -> None:
        import asyncio
        current_loop = asyncio.get_event_loop()
        if current_loop is not self._loop:
            raise RuntimeError(
                f"Resource used on different loop: created on {id(self._loop)}, "
                f"used on {id(current_loop)}"
            )
        self.items.append(item)

    async def get_count(self) -> int:
        import asyncio
        current_loop = asyncio.get_event_loop()
        if current_loop is not self._loop:
            raise RuntimeError(
                f"Resource used on different loop: created on {id(self._loop)}, "
                f"used on {id(current_loop)}"
            )
        return len(self.items)

    async def clear(self) -> None:
        """Clear items, verifying we're still on the original event loop.

        Called during fixture teardown - if the teardown runs on a different
        event loop (the bug this test guards against), this will raise.
        """
        import asyncio
        current_loop = asyncio.get_event_loop()
        if current_loop is not self._loop:
            raise RuntimeError(
                f"Teardown on different loop: created on {id(self._loop)}, "
                f"teardown on {id(current_loop)}"
            )
        self.items.clear()


@fixture(scope="session")
async def autouse_loop_shared_resource():
    """Session-scoped async fixture - creates a resource on the session event loop."""
    resource = AsyncResource()
    yield resource


@fixture(autouse=True)
async def touch_resource(autouse_loop_shared_resource: AsyncResource):
    """Function-scoped autouse fixture that interacts with the session resource.

    This is the key scenario: an autouse fixture calling async methods on a
    session-scoped resource must run on the same event loop as that resource.
    Without the fix, this raises 'attached to a different loop' errors.

    The teardown (after yield) also verifies event loop consistency - if
    function teardowns use the wrong event loop, clear() will raise.
    """
    await autouse_loop_shared_resource.add("setup")
    yield
    await autouse_loop_shared_resource.clear()


async def test_first_use(autouse_loop_shared_resource: AsyncResource):
    """First test using the shared resource."""
    await autouse_loop_shared_resource.add("first")
    count = await autouse_loop_shared_resource.get_count()
    assert count >= 2  # at least "setup" + "first"


async def test_second_use(autouse_loop_shared_resource: AsyncResource):
    """Second test - autouse fixture should have run without loop errors."""
    await autouse_loop_shared_resource.add("second")
    count = await autouse_loop_shared_resource.get_count()
    assert count >= 2  # at least "setup" + "second" (plus prior items)


async def test_third_use(autouse_loop_shared_resource: AsyncResource):
    """Third test - ensures consistent behavior across multiple tests."""
    await autouse_loop_shared_resource.add("third")
    count = await autouse_loop_shared_resource.get_count()
    assert count >= 2  # at least "setup" + "third" (plus prior items)
