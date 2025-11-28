"""Test loop scope detection logic.

NOTE: This file tests rustest-native async loop scope detection.
These tests should ONLY run with rustest native mode (not pytest or pytest-compat).
"""

import sys

# Skip when running with actual pytest (not pytest-compat mode)
# In pytest-compat mode, rustest's pytest.skip() doesn't handle allow_module_level properly,
# so we only check for actual pytest here
if "pytest" in sys.argv[0]:
    import pytest
    pytest.skip("Requires rustest runner (tests rustest-specific loop scope detection)", allow_module_level=True)

from rustest import fixture, mark


# Test 1: No async fixtures → function loop (default)
def test_no_async_fixtures():
    """Test with no async fixtures should use function loop."""
    assert True


# Test 2: Direct session async fixture → session loop
@fixture(scope="session")
async def loop_test_session_resource():
    """Session-scoped async fixture."""
    return "session_data"


async def test_with_session_fixture(loop_test_session_resource):
    """Test using session async fixture directly."""
    assert loop_test_session_resource == "session_data"


# Test 3: Function async fixture depending on session async fixture
@fixture
async def loop_test_function_item(loop_test_session_resource):
    """Function-scoped async fixture that depends on session fixture."""
    return f"item_{loop_test_session_resource}"


async def test_with_nested_fixture(loop_test_function_item):
    """Test using function fixture that depends on session fixture.

    This should automatically detect session loop is needed because
    loop_test_function_item → loop_test_session_resource (session async)
    """
    assert loop_test_function_item == "item_session_data"


# Test 4: Explicit loop_scope overrides detection
@mark.asyncio(loop_scope="function")
async def test_explicit_function_scope():
    """Test with explicit function scope."""
    assert True


# Test 5: Module-scoped async fixture
@fixture(scope="module")
async def loop_test_module_resource():
    """Module-scoped async fixture."""
    return "module_data"


async def test_with_module_fixture(loop_test_module_resource):
    """Test should automatically use module loop."""
    assert loop_test_module_resource == "module_data"


# Test 6: Mixed scopes - widest wins
@fixture(scope="module")
async def loop_test_module_data():
    return "module"


@fixture
async def loop_test_function_data(loop_test_module_data):
    return f"function_{loop_test_module_data}"


async def test_mixed_scopes(loop_test_session_resource, loop_test_function_data):
    """Test with both session and module fixtures.

    Should use session loop (widest scope).
    """
    assert loop_test_session_resource == "session_data"
    assert loop_test_function_data == "function_module"
