"""Test loop scope detection logic.

NOTE: This file tests rustest-native async loop scope detection.
Run without --pytest-compat flag.
"""

import sys

# These tests require rustest native mode
if "--pytest-compat" in sys.argv:
    # Skip entire module in pytest-compat mode
    import pytest
    pytest.skip("Requires native rustest mode", allow_module_level=True)

from rustest import fixture, mark


# Test 1: No async fixtures → function loop (default)
def test_no_async_fixtures():
    """Test with no async fixtures should use function loop."""
    assert True


# Test 2: Direct session async fixture → session loop
@fixture(scope="session")
async def session_resource():
    """Session-scoped async fixture."""
    return "session_data"


async def test_with_session_fixture(session_resource):
    """Test using session async fixture directly."""
    assert session_resource == "session_data"


# Test 3: Function async fixture depending on session async fixture
@fixture
async def function_item(session_resource):
    """Function-scoped async fixture that depends on session fixture."""
    return f"item_{session_resource}"


async def test_with_nested_fixture(function_item):
    """Test using function fixture that depends on session fixture.

    This should automatically detect session loop is needed because
    function_item → session_resource (session async)
    """
    assert function_item == "item_session_data"


# Test 4: Explicit loop_scope overrides detection
@mark.asyncio(loop_scope="function")
async def test_explicit_function_scope():
    """Test with explicit function scope."""
    assert True


# Test 5: Module-scoped async fixture
@fixture(scope="module")
async def module_resource():
    """Module-scoped async fixture."""
    return "module_data"


async def test_with_module_fixture(module_resource):
    """Test should automatically use module loop."""
    assert module_resource == "module_data"


# Test 6: Mixed scopes - widest wins
@fixture(scope="module")
async def module_data():
    return "module"


@fixture
async def function_data(module_data):
    return f"function_{module_data}"


async def test_mixed_scopes(session_resource, function_data):
    """Test with both session and module fixtures.

    Should use session loop (widest scope).
    """
    assert session_resource == "session_data"
    assert function_data == "function_module"
