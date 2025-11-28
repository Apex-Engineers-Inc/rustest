"""Test async fixture support in pytest-compat mode.

This file uses pytest-style imports to test compatibility mode.
"""

import sys

# Skip this entire module when running with pytest
# This test file is specifically for testing rustest's pytest compatibility layer
# and must be run with rustest itself, not pytest
if "pytest" in sys.argv[0]:
    import pytest

    pytest.skip(
        "This test file requires rustest runner (rustest-only tests)",
        allow_module_level=True,
    )

# Use rustest's pytest compatibility layer
from rustest.compat import pytest


# ============================================================================
# Basic async fixtures
# ============================================================================


@pytest.fixture
async def async_value():
    """Simple async fixture that returns a value."""
    return 42


@pytest.fixture
async def async_generator_fixture():
    """Async generator fixture with setup and teardown."""
    # Setup
    value = {"initialized": True, "count": 0}
    yield value
    # Teardown
    value["count"] += 1


@pytest.fixture(scope="session")
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


@pytest.fixture
async def async_base():
    """Base async fixture."""
    return {"base": True, "value": 10}


@pytest.fixture
async def async_dependent(async_base):
    """Async fixture that depends on another async fixture."""
    return {
        "dependent": True,
        "base_value": async_base["value"],
        "multiplied": async_base["value"] * 2,
    }


@pytest.fixture
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


@pytest.fixture
def sync_fixture():
    """Regular sync fixture."""
    return {"sync": True, "number": 5}


@pytest.fixture
async def async_uses_sync(sync_fixture):
    """Async fixture that depends on a sync fixture."""
    return {
        "async": True,
        "sync_number": sync_fixture["number"],
        "doubled": sync_fixture["number"] * 2,
    }


@pytest.fixture
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


@pytest.fixture
async def async_gen_with_dependency(async_value):
    """Async generator fixture that depends on async fixture."""
    data = {"setup": True, "async_value": async_value}
    yield data
    data["teardown"] = True


@pytest.fixture
async def async_gen_base():
    """Base async generator fixture."""
    resource = {"allocated": True, "freed": False}
    yield resource
    resource["freed"] = True


@pytest.fixture
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


@pytest.fixture(params=[1, 2, 3])
async def async_parametrized(request):
    """Parametrized async fixture."""
    return {"param": request.param, "squared": request.param**2}


async def test_async_parametrized_fixture(async_parametrized):
    """Test parametrized async fixture."""
    param = async_parametrized["param"]
    assert async_parametrized["squared"] == param**2
    assert param in [1, 2, 3]


@pytest.fixture(params=["a", "b"])
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


@pytest.fixture(scope="module")
async def async_module_fixture():
    """Module-scoped async fixture."""
    return {"scope": "module", "data": [1, 2, 3]}


@pytest.fixture(scope="class")
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


@pytest.fixture
async def async_chain_1():
    """First in async chain."""
    return 1


@pytest.fixture
async def async_chain_2(async_chain_1):
    """Second in async chain."""
    return async_chain_1 + 1


@pytest.fixture
async def async_chain_3(async_chain_2):
    """Third in async chain."""
    return async_chain_2 + 1


@pytest.fixture
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


@pytest.fixture
async def async_for_sync_test():
    """Async fixture used in sync test."""
    return {"async_fixture": True, "value": 777}


# Note: This tests whether async fixtures can be used in sync tests
# In pytest, this typically doesn't work, but we're testing rustest behavior
def test_sync_test_with_async_fixture(async_for_sync_test):
    """Test sync test using async fixture."""
    assert async_for_sync_test["async_fixture"] is True
    assert async_for_sync_test["value"] == 777
