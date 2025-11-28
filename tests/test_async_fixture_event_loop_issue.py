"""Test case to reproduce async fixture event loop issue.

This test reproduces the bug where function-scoped async fixtures
that interact with session-scoped async fixtures cause
"Task got Future attached to a different loop" errors.

The scenario is:
1. Session-scoped async fixture creates a database client
2. Function-scoped async fixture uses that client to create database entries
3. The function fixture runs in a different event loop, causing errors
"""

import asyncio
import sys

# Skip when running with pytest
if "pytest" in sys.argv[0]:
    import pytest
    pytest.skip("This test file requires rustest runner", allow_module_level=True)

from rustest import fixture


# Simulated database client that tracks which event loop it's using
class DatabaseClient:
    """Simulated database client that tracks event loop."""

    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.connected = True
        self.created_items = []

    async def create_item(self, name: str):
        """Create a database item.

        This simulates an async operation that needs to run in the same
        event loop as the client was created in.
        """
        current_loop = asyncio.get_event_loop()

        # This is where the error would occur in real code:
        # If we're in a different loop than the client was created in,
        # any tasks/futures from the client's loop won't work here
        if current_loop != self.loop:
            raise RuntimeError(
                f"Task got Future attached to a different loop: "
                f"client loop {id(self.loop)} != current loop {id(current_loop)}"
            )

        # Simulate async database operation
        await asyncio.sleep(0)
        item = {"name": name, "id": len(self.created_items) + 1}
        self.created_items.append(item)
        return item

    async def get_items(self):
        """Get all items from the database."""
        current_loop = asyncio.get_event_loop()

        if current_loop != self.loop:
            raise RuntimeError(
                f"Task got Future attached to a different loop: "
                f"client loop {id(self.loop)} != current loop {id(current_loop)}"
            )

        await asyncio.sleep(0)
        return self.created_items.copy()


# Session-scoped async fixture that creates the database client
@fixture(scope="session")
async def database_client():
    """Session-scoped database client.

    This simulates a session-scoped fixture like engineering_client
    that creates a database connection in a session event loop.
    """
    client = DatabaseClient()
    yield client
    # Cleanup
    client.connected = False


# Function-scoped async fixture that uses the database client
@fixture
async def test_item(database_client: DatabaseClient):
    """Function-scoped fixture that creates a test item.

    This simulates a function-scoped fixture that needs to interact
    with the session-scoped database client to set up test data.

    This is where the bug occurs: the function fixture runs in a
    different event loop than the session fixture.
    """
    # Try to create a test item using the session client
    item = await database_client.create_item("test_item")
    yield item
    # Cleanup would happen here


# Test using the function-scoped fixture
async def test_with_function_fixture(test_item):
    """Test that uses a function-scoped async fixture.

    This should work but currently fails with:
    RuntimeError: Task got Future attached to a different loop
    """
    assert test_item["name"] == "test_item"
    assert test_item["id"] == 1


# Test using the session fixture directly
async def test_with_session_fixture_direct(database_client: DatabaseClient):
    """Test that uses the session fixture directly.

    This should work fine since we're using the session event loop.
    """
    item = await database_client.create_item("direct_item")
    assert item["name"] == "direct_item"


# Test mixing both
async def test_mixed_fixtures(database_client: DatabaseClient, test_item):
    """Test mixing session and function async fixtures.

    This tests whether function fixtures can safely interact with
    session fixtures.
    """
    # The test_item should have been created successfully
    assert test_item["name"] == "test_item"

    # We should be able to query the database directly
    items = await database_client.get_items()

    # Should contain the item created by the fixture
    item_names = [item["name"] for item in items]
    assert "test_item" in item_names
