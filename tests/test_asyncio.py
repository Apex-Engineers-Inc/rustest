"""Comprehensive tests for asyncio support via @mark.asyncio."""

import asyncio
import sys

# Skip this entire module when running with pytest
# These tests use rustest's @mark.asyncio which requires rustest runner
if "pytest" in sys.argv[0]:
    import pytest

    pytest.skip(
        "This test file requires rustest runner (rustest-only tests)",
        allow_module_level=True,
    )

from rustest import mark, raises, fixture
from rustest.compat import pytest as rustest_pytest


# Basic async test
@mark.asyncio
async def test_basic_async():
    """Test basic async function execution."""
    await asyncio.sleep(0.001)
    assert True


@mark.asyncio
async def test_async_with_assertion():
    """Test async function with assertion."""
    result = await async_add(1, 2)
    assert result == 3


@mark.asyncio
async def test_async_with_multiple_awaits():
    """Test async function with multiple await calls."""
    result1 = await async_add(1, 2)
    result2 = await async_add(3, 4)
    result3 = await async_add(result1, result2)
    assert result3 == 10


# Test with loop_scope parameter
@mark.asyncio(loop_scope="function")
async def test_function_scope():
    """Test async with explicit function scope."""
    result = await async_multiply(3, 4)
    assert result == 12


@mark.asyncio(loop_scope="module")
async def test_module_scope():
    """Test async with module scope."""
    result = await async_multiply(5, 6)
    assert result == 30


# Test async with fixtures
@fixture
def sync_value():
    """Regular synchronous fixture."""
    return 42


@mark.asyncio
async def test_async_with_sync_fixture(sync_value):
    """Test async function using synchronous fixture."""
    result = await async_add(sync_value, 8)
    assert result == 50


# Test async with parametrize
from rustest import parametrize


@mark.asyncio
@parametrize("x,y,expected", [(1, 2, 3), (5, 5, 10), (10, 20, 30)])
async def test_async_parametrized(x, y, expected):
    """Test async function with parametrization."""
    result = await async_add(x, y)
    assert result == expected


# Test async with multiple marks
@mark.asyncio
@mark.slow
async def test_async_with_multiple_marks():
    """Test async function with multiple marks."""
    await asyncio.sleep(0.01)
    result = await async_multiply(7, 8)
    assert result == 56


# Test async exception handling
@mark.asyncio
async def test_async_exception():
    """Test that async exceptions are properly raised."""
    with raises(ValueError, match="negative"):
        await async_divide(10, -1)


@mark.asyncio
async def test_async_zero_division():
    """Test async zero division error."""
    with raises(ZeroDivisionError):
        await async_divide(10, 0)


# Test async with assertion failure
@mark.asyncio
async def test_async_assertion_failure():
    """Test that async assertion failures are caught."""
    result = await async_add(1, 1)
    # This should pass
    assert result == 2


# Test concurrent async operations
@mark.asyncio
async def test_async_gather():
    """Test async with asyncio.gather for concurrent operations."""
    results = await asyncio.gather(
        async_add(1, 2), async_add(3, 4), async_add(5, 6)
    )
    assert results == [3, 7, 11]


# Test async with create_task
@mark.asyncio
async def test_async_create_task():
    """Test async with asyncio.create_task."""
    task1 = asyncio.create_task(async_add(10, 20))
    task2 = asyncio.create_task(async_multiply(5, 5))
    result1 = await task1
    result2 = await task2
    assert result1 == 30
    assert result2 == 25


# Test async context manager
@mark.asyncio
async def test_async_context_manager():
    """Test async with async context manager."""
    async with AsyncContextManager() as value:
        assert value == "context_value"


# Test async generator
@mark.asyncio
async def test_async_generator():
    """Test async with async generator."""
    results = []
    async for value in async_range(5):
        results.append(value)
    assert results == [0, 1, 2, 3, 4]


# Test nested async calls
@mark.asyncio
async def test_nested_async_calls():
    """Test deeply nested async calls."""
    result = await async_fibonacci(10)
    assert result == 55


# Test async with timeout
@mark.asyncio
async def test_async_with_timeout():
    """Test async operation with timeout."""
    result = await asyncio.wait_for(async_add(1, 2), timeout=1.0)
    assert result == 3


# Test class-based async tests
@mark.asyncio(loop_scope="class")
class TestAsyncClass:
    """Test class with async methods."""

    async def test_async_method_one(self):
        """First async test method in class."""
        result = await async_add(1, 1)
        assert result == 2

    async def test_async_method_two(self):
        """Second async test method in class."""
        result = await async_multiply(3, 3)
        assert result == 9


# Test mixed sync and async in class (async methods need mark)
class TestMixedClass:
    """Test class with both sync and async methods."""

    def test_sync_method(self):
        """Synchronous test method."""
        assert 1 + 1 == 2

    @mark.asyncio
    async def test_async_method(self):
        """Async test method."""
        result = await async_add(2, 2)
        assert result == 4


# Test skipif with async
@mark.asyncio
@mark.skipif(sys.platform == "win32", reason="Test on Unix only")
async def test_async_skipif():
    """Test async with skipif mark."""
    result = await async_add(1, 2)
    assert result == 3


# Helper async functions for tests
async def async_add(x, y):
    """Helper async function that adds two numbers."""
    await asyncio.sleep(0.001)  # Simulate async operation
    return x + y


async def async_multiply(x, y):
    """Helper async function that multiplies two numbers."""
    await asyncio.sleep(0.001)
    return x * y


async def async_divide(x, y):
    """Helper async function that divides two numbers."""
    await asyncio.sleep(0.001)
    if y < 0:
        raise ValueError("negative divisor not allowed")
    return x / y


async def async_fibonacci(n):
    """Helper async function that calculates fibonacci number."""
    if n <= 1:
        return n
    await asyncio.sleep(0.0001)
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


class AsyncContextManager:
    """Async context manager for testing."""

    async def __aenter__(self):
        await asyncio.sleep(0.001)
        return "context_value"

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await asyncio.sleep(0.001)
        return None


async def async_range(n):
    """Async generator for testing."""
    for i in range(n):
        await asyncio.sleep(0.001)
        yield i


# --- Async fixture tests (from test_async_fixtures.py) ---


# ============================================================================
# Basic async fixtures
# ============================================================================


@rustest_pytest.fixture
async def async_value():
    """Simple async fixture that returns a value."""
    return 42


@rustest_pytest.fixture
async def async_generator_fixture():
    """Async generator fixture with setup and teardown."""
    # Setup
    value = {"initialized": True, "count": 0}
    yield value
    # Teardown
    value["count"] += 1


@rustest_pytest.fixture(scope="session")
async def async_session_fixture():
    """Session-scoped async fixture."""
    return "session_data"


async def test_async_fixture_basic(async_value):
    """Test that async fixtures are properly awaited."""
    assert async_value == 42


async def test_async_generator_fixture(async_generator_fixture):
    """Test that async generator fixtures work."""
    assert async_generator_fixture["initialized"] is True
    async_generator_fixture["count"] = 5


async def test_async_session_fixture(async_session_fixture):
    """Test session-scoped async fixtures."""
    assert async_session_fixture == "session_data"


async def test_multiple_async_fixtures(async_value, async_generator_fixture, async_session_fixture):
    """Test multiple async fixtures together."""
    assert async_value == 42
    assert async_generator_fixture["initialized"] is True
    assert async_session_fixture == "session_data"


# ============================================================================
# Async fixtures with dependencies on other async fixtures
# ============================================================================


@rustest_pytest.fixture
async def async_base():
    """Base async fixture."""
    return {"base": True, "value": 10}


@rustest_pytest.fixture
async def async_dependent(async_base):
    """Async fixture that depends on another async fixture."""
    return {
        "dependent": True,
        "base_value": async_base["value"],
        "multiplied": async_base["value"] * 2,
    }


@rustest_pytest.fixture
async def async_double_dependent(async_dependent, async_base):
    """Async fixture that depends on multiple async fixtures."""
    return {"double_dependent": True, "sum": async_base["value"] + async_dependent["multiplied"]}


async def test_async_fixture_dependency(async_dependent):
    """Test async fixture depending on another async fixture."""
    assert async_dependent["dependent"] is True
    assert async_dependent["base_value"] == 10
    assert async_dependent["multiplied"] == 20


async def test_async_fixture_multiple_dependencies(async_double_dependent):
    """Test async fixture with multiple async dependencies."""
    assert async_double_dependent["double_dependent"] is True
    assert async_double_dependent["sum"] == 30  # 10 + 20


# ============================================================================
# Mixed sync and async fixture dependencies
# ============================================================================


@rustest_pytest.fixture
def sync_fixture():
    """Regular sync fixture."""
    return {"sync": True, "number": 5}


@rustest_pytest.fixture
async def async_uses_sync(sync_fixture):
    """Async fixture that depends on a sync fixture."""
    return {
        "async": True,
        "sync_number": sync_fixture["number"],
        "doubled": sync_fixture["number"] * 2,
    }


@rustest_pytest.fixture
async def async_base_for_sync():
    """Async fixture used by sync fixture."""
    return {"async_base": True, "value": 100}


async def test_async_with_sync_dependency(async_uses_sync):
    """Test async fixture using sync fixture."""
    assert async_uses_sync["async"] is True
    assert async_uses_sync["sync_number"] == 5
    assert async_uses_sync["doubled"] == 10


async def test_mixed_fixtures(sync_fixture, async_uses_sync):
    """Test mixing sync and async fixtures in same test."""
    assert sync_fixture["sync"] is True
    assert async_uses_sync["async"] is True
    assert async_uses_sync["sync_number"] == sync_fixture["number"]


def test_sync_test_with_sync_fixture(sync_fixture):
    """Test that sync tests still work with sync fixtures."""
    assert sync_fixture["sync"] is True
    assert sync_fixture["number"] == 5


# ============================================================================
# Async generator fixtures with dependencies
# ============================================================================


@rustest_pytest.fixture
async def async_gen_with_dependency(async_value):
    """Async generator fixture that depends on async fixture."""
    data = {"setup": True, "async_value": async_value}
    yield data
    data["teardown"] = True


@rustest_pytest.fixture
async def async_gen_base():
    """Base async generator fixture."""
    resource = {"allocated": True, "freed": False}
    yield resource
    resource["freed"] = True


@rustest_pytest.fixture
async def async_gen_dependent(async_gen_base):
    """Async generator that depends on another async generator."""
    derived = {"derived": True, "base_allocated": async_gen_base["allocated"]}
    yield derived
    derived["cleaned"] = True


async def test_async_gen_with_dependency(async_gen_with_dependency):
    """Test async generator with async fixture dependency."""
    assert async_gen_with_dependency["setup"] is True
    assert async_gen_with_dependency["async_value"] == 42


async def test_async_gen_dependent(async_gen_dependent):
    """Test async generator depending on another async generator."""
    assert async_gen_dependent["derived"] is True
    assert async_gen_dependent["base_allocated"] is True


# ============================================================================
# Parametrized async fixtures
# ============================================================================


@rustest_pytest.fixture(params=[1, 2, 3])
async def async_parametrized(request):
    """Parametrized async fixture."""
    return {"param": request.param, "squared": request.param**2}


async def test_async_parametrized_fixture(async_parametrized):
    """Test parametrized async fixture."""
    param = async_parametrized["param"]
    assert async_parametrized["squared"] == param**2
    assert param in [1, 2, 3]


@rustest_pytest.fixture(params=["a", "b"])
async def async_param_gen(request):
    """Parametrized async generator fixture."""
    value = {"letter": request.param, "used": False}
    yield value
    value["used"] = True


async def test_async_param_gen(async_param_gen):
    """Test parametrized async generator fixture."""
    assert async_param_gen["letter"] in ["a", "b"]
    assert async_param_gen["used"] is False


# ============================================================================
# Different scopes for async fixtures
# ============================================================================


@rustest_pytest.fixture(scope="module")
async def async_module_fixture():
    """Module-scoped async fixture."""
    return {"scope": "module", "data": [1, 2, 3]}


@rustest_pytest.fixture(scope="class")
async def async_class_fixture():
    """Class-scoped async fixture."""
    return {"scope": "class", "value": 999}


async def test_module_scope_1(async_module_fixture):
    """First test using module-scoped async fixture."""
    assert async_module_fixture["scope"] == "module"
    assert async_module_fixture["data"] == [1, 2, 3]


async def test_module_scope_2(async_module_fixture):
    """Second test using module-scoped async fixture."""
    assert async_module_fixture["scope"] == "module"
    # Module fixtures are shared across tests
    assert "data" in async_module_fixture


class TestAsyncClassScope:
    """Test class for class-scoped async fixtures."""

    async def test_class_fixture_1(self, async_class_fixture):
        """First test in class using class-scoped async fixture."""
        assert async_class_fixture["scope"] == "class"
        assert async_class_fixture["value"] == 999

    async def test_class_fixture_2(self, async_class_fixture):
        """Second test in class using class-scoped async fixture."""
        assert async_class_fixture["scope"] == "class"
        # Class fixtures are shared within the class
        assert "value" in async_class_fixture


# ============================================================================
# Complex dependency chains
# ============================================================================


@rustest_pytest.fixture
async def async_chain_1():
    """First in async chain."""
    return 1


@rustest_pytest.fixture
async def async_chain_2(async_chain_1):
    """Second in async chain."""
    return async_chain_1 + 1


@rustest_pytest.fixture
async def async_chain_3(async_chain_2):
    """Third in async chain."""
    return async_chain_2 + 1


@rustest_pytest.fixture
async def async_chain_4(async_chain_3, async_chain_1):
    """Fourth in async chain with multiple dependencies."""
    return async_chain_3 + async_chain_1


async def test_deep_async_chain(async_chain_4):
    """Test deep async fixture dependency chain."""
    # Chain: 1 -> 2 -> 3 -> 4 (also uses 1)
    # Values: 1 -> 2 -> 3 -> 4 (3 + 1)
    assert async_chain_4 == 4


# ============================================================================
# Async fixtures with sync tests (should work)
# ============================================================================


@rustest_pytest.fixture
async def async_for_sync_test():
    """Async fixture used in sync test."""
    return {"async_fixture": True, "value": 777}


# Note: This tests whether async fixtures can be used in sync tests
# In pytest, this typically doesn't work, but we're testing rustest behavior
def test_sync_test_with_async_fixture(async_for_sync_test):
    """Test sync test using async fixture."""
    assert async_for_sync_test["async_fixture"] is True
    assert async_for_sync_test["value"] == 777
