"""Tests for fixture parametrization with pytest-compatible API.

This file uses pytest-style imports to test compatibility mode.
"""

import sys

# Skip this entire module when running with pytest
# This test file is specifically for testing rustest's pytest compatibility layer
# and must be run with rustest itself, not pytest
if "pytest" in sys.argv[0]:
    import pytest
    pytest.skip("This test file requires rustest runner (rustest-only tests)", allow_module_level=True)

# Use rustest's pytest compatibility layer
from rustest.compat import pytest


# ==============================================================================
# Basic pytest-style fixture parametrization
# ==============================================================================


@pytest.fixture(params=[1, 2, 3])
def pytest_number(request):
    """Pytest-style parametrized fixture."""
    return request.param


def test_pytest_basic_param(pytest_number):
    """Test basic pytest-style parametrized fixture."""
    assert pytest_number in [1, 2, 3]


# ==============================================================================
# Pytest fixture with custom IDs
# ==============================================================================


@pytest.fixture(params=["prod", "dev", "test"], ids=["production", "development", "testing"])
def env_name(request):
    """Pytest-style fixture with custom IDs."""
    return request.param


def test_pytest_custom_ids(env_name):
    """Test pytest-style fixture with custom IDs."""
    assert env_name in ["prod", "dev", "test"]


# ==============================================================================
# Pytest.param for fixtures
# ==============================================================================


@pytest.fixture(params=[
    pytest.param(10, id="ten"),
    pytest.param(20, id="twenty"),
])
def pytest_param_fixture(request):
    """Fixture using pytest.param for custom IDs."""
    return request.param


def test_pytest_param_in_fixture(pytest_param_fixture):
    """Test fixture using pytest.param."""
    assert pytest_param_fixture in [10, 20]


# ==============================================================================
# Combined with @pytest.mark.parametrize
# ==============================================================================


@pytest.mark.parametrize("multiplier", [2, 3])
def test_fixture_with_mark_parametrize(pytest_number, multiplier):
    """Test combination of fixture params and @pytest.mark.parametrize."""
    result = pytest_number * multiplier
    assert result in [2, 3, 4, 6, 6, 9]  # all possible products


# ==============================================================================
# Scoped fixtures with params
# ==============================================================================


@pytest.fixture(scope="module", params=["alpha", "beta"])
def module_param(request):
    """Module-scoped pytest fixture with params."""
    return request.param


def test_module_scoped_param(module_param):
    """Test module-scoped parametrized fixture."""
    assert module_param in ["alpha", "beta"]


# ==============================================================================
# Yield fixture with params
# ==============================================================================


@pytest.fixture(params=["resource_a", "resource_b"])
def pytest_yield_param(request):
    """Pytest yield fixture with params."""
    # Setup
    resource = {"name": request.param, "active": True}
    yield resource
    # Teardown
    resource["active"] = False


def test_pytest_yield_param(pytest_yield_param):
    """Test pytest yield fixture with params."""
    assert pytest_yield_param["active"] is True
    assert pytest_yield_param["name"] in ["resource_a", "resource_b"]


# ==============================================================================
# Request.param verification
# ==============================================================================


@pytest.fixture(params=["verify_me"])
def verify_request_param(request):
    """Verify request.param is accessible."""
    # Explicitly verify that request.param has the expected value
    param_value = request.param
    assert param_value == "verify_me", f"Expected 'verify_me', got {param_value!r}"
    return param_value


def test_request_param_access(verify_request_param):
    """Test that request.param is correctly accessible."""
    assert verify_request_param == "verify_me"


# ==============================================================================
# Fixture depending on parametrized fixture
# ==============================================================================


@pytest.fixture
def dependent_on_param(pytest_number):
    """Non-parametrized fixture depending on parametrized one."""
    return pytest_number ** 2


def test_dependent_fixture(dependent_on_param):
    """Test fixture dependency on parametrized fixture."""
    assert dependent_on_param in [1, 4, 9]


# ==============================================================================
# Multiple parametrized fixtures
# ==============================================================================


@pytest.fixture(params=["GET", "POST"])
def http_method(request):
    """Parametrized fixture with HTTP methods."""
    return request.param


@pytest.fixture(params=[200, 404])
def status_code(request):
    """Parametrized fixture with status codes."""
    return request.param


def test_multiple_params(http_method, status_code):
    """Test multiple parametrized fixtures (cartesian product).

    Should produce 4 test cases: GET/200, GET/404, POST/200, POST/404
    """
    assert http_method in ["GET", "POST"]
    assert status_code in [200, 404]


# ==============================================================================
# Complex objects as params
# ==============================================================================


class DatabaseConfig:
    """Sample config class for testing."""
    def __init__(self, name, port):
        self.name = name
        self.port = port


@pytest.fixture(params=[
    DatabaseConfig("mysql", 3306),
    DatabaseConfig("postgres", 5432),
], ids=["mysql", "postgres"])
def db_config(request):
    """Parametrized fixture with class instances."""
    return request.param


def test_class_instance_params(db_config):
    """Test parametrized fixture with class instances."""
    assert db_config.name in ["mysql", "postgres"]
    if db_config.name == "mysql":
        assert db_config.port == 3306
    else:
        assert db_config.port == 5432


# ==============================================================================
# Test autouse with params (edge case)
# ==============================================================================


# Counters for tracking autouse fixture calls
_autouse_calls = []


@pytest.fixture(autouse=True, params=["auto1", "auto2"])
def autouse_parametrized(request):
    """Autouse fixture with params - creates test variants."""
    _autouse_calls.append(request.param)
    return request.param


def test_with_autouse_param():
    """Test that runs with autouse parametrized fixture.

    Note: This test will be run once for each param value.
    """
    # The autouse fixture runs but we don't have direct access to its value
    # unless we also request it as a parameter
    pass


def test_with_autouse_param_explicit(autouse_parametrized):
    """Test that explicitly requests the autouse parametrized fixture."""
    assert autouse_parametrized in ["auto1", "auto2"]
