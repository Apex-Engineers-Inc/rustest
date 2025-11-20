"""Pytest-native fixture parametrization tests.

This file uses real pytest imports so it can be run with pytest directly,
while also serving as validation that rustest's fixture parametrization is compatible.
"""

import pytest


# ==============================================================================
# Basic fixture parametrization
# ==============================================================================


@pytest.fixture(params=[1, 2, 3])
def number(request):
    """Basic parametrized fixture."""
    return request.param


def test_basic_param(number):
    """Test basic parametrized fixture."""
    assert number in [1, 2, 3]


# ==============================================================================
# Fixture with custom IDs
# ==============================================================================


@pytest.fixture(params=["prod", "dev", "test"], ids=["production", "development", "testing"])
def env(request):
    """Fixture with custom IDs."""
    return request.param


def test_custom_ids(env):
    """Test fixture with custom IDs."""
    assert env in ["prod", "dev", "test"]


# ==============================================================================
# pytest.param for fixtures
# ==============================================================================


@pytest.fixture(params=[
    pytest.param(10, id="ten"),
    pytest.param(20, id="twenty"),
])
def score(request):
    """Fixture using pytest.param()."""
    return request.param


def test_pytest_param(score):
    """Test fixture using pytest.param()."""
    assert score in [10, 20]


# ==============================================================================
# Multiple parametrized fixtures (cartesian product)
# ==============================================================================


@pytest.fixture(params=[1, 2])
def multiplier(request):
    """First parametrized fixture."""
    return request.param


@pytest.fixture(params=[10, 20])
def base(request):
    """Second parametrized fixture."""
    return request.param


def test_multiple_params(multiplier, base):
    """Test with multiple parametrized fixtures (cartesian product)."""
    result = multiplier * base
    assert result in [10, 20, 40]  # 1*10, 1*20, 2*20, 2*10=20


# ==============================================================================
# Fixture dependency chains
# ==============================================================================


@pytest.fixture(params=[5, 10])
def base_value(request):
    """Base parametrized fixture."""
    return request.param


@pytest.fixture
def doubled(base_value):
    """Dependent fixture that uses parametrized fixture."""
    return base_value * 2


def test_dependent_fixtures(base_value, doubled):
    """Test fixture depending on parametrized fixture."""
    assert doubled == base_value * 2


@pytest.fixture
def quadrupled(doubled):
    """Fixture depending on dependent fixture."""
    return doubled * 2


def test_double_dependent(base_value, quadrupled):
    """Test fixture depending on chain of fixtures."""
    assert quadrupled == base_value * 4


# ==============================================================================
# Module-scoped parametrized fixtures
# ==============================================================================


@pytest.fixture(params=[100, 200], scope="module")
def module_param(request):
    """Module-scoped parametrized fixture."""
    return request.param


def test_module_scope_1(module_param):
    """Test module-scoped parametrized fixture (test 1)."""
    assert module_param in [100, 200]


def test_module_scope_2(module_param):
    """Test module-scoped parametrized fixture (test 2)."""
    assert module_param > 0


# ==============================================================================
# Yield fixtures with parametrization
# ==============================================================================


@pytest.fixture(params=["setup_a", "setup_b"])
def setup_teardown(request):
    """Yield fixture with parametrization."""
    value = request.param
    # Setup
    yield value
    # Teardown (nothing to do in this test)


def test_yield_param(setup_teardown):
    """Test yield fixture with parametrization."""
    assert setup_teardown in ["setup_a", "setup_b"]


# ==============================================================================
# Class-based tests with parametrized fixtures
# ==============================================================================


@pytest.fixture(params=["apple", "banana"])
def fruit(request):
    """Fixture for class-based tests."""
    return request.param


class TestFruits:
    """Test class using parametrized fixtures."""

    def test_fruit_name(self, fruit):
        """Test fruit fixture in class."""
        assert fruit in ["apple", "banana"]

    def test_fruit_length(self, fruit):
        """Test fruit length in class."""
        assert len(fruit) > 0


# ==============================================================================
# Complex value types
# ==============================================================================


@pytest.fixture(params=[
    [1, 2, 3],
    [4, 5, 6],
])
def number_list(request):
    """Fixture with list parameters."""
    return request.param


def test_list_param(number_list):
    """Test fixture with list parameter."""
    assert len(number_list) == 3
    assert sum(number_list) in [6, 15]


@pytest.fixture(params=[
    {"name": "Alice", "age": 30},
    {"name": "Bob", "age": 25},
])
def user(request):
    """Fixture with dict parameters."""
    return request.param


def test_dict_param(user):
    """Test fixture with dict parameter."""
    assert "name" in user
    assert "age" in user
    assert user["age"] > 0


# ==============================================================================
# Combining with test parametrization
# ==============================================================================


@pytest.fixture(params=[2, 3])
def fixture_multiplier(request):
    """Parametrized fixture to combine with test parametrization."""
    return request.param


@pytest.mark.parametrize("value", [10, 20])
def test_combined_parametrization(fixture_multiplier, value):
    """Test combining fixture and test parametrization."""
    result = fixture_multiplier * value
    # 2*10=20, 2*20=40, 3*10=30, 3*20=60
    assert result in [20, 30, 40, 60]


# ==============================================================================
# Callable IDs
# ==============================================================================


def id_func(param):
    """Custom ID function for parametrized fixture."""
    if isinstance(param, int):
        return f"value_{param}"
    return str(param)


@pytest.fixture(params=[100, 200, 300], ids=id_func)
def custom_id_fixture(request):
    """Fixture with callable IDs."""
    return request.param


def test_callable_ids(custom_id_fixture):
    """Test fixture with callable IDs."""
    assert custom_id_fixture in [100, 200, 300]


# ==============================================================================
# None parameter
# ==============================================================================


@pytest.fixture(params=[None, "value", 42])
def optional_value(request):
    """Fixture with None as a parameter."""
    return request.param


def test_none_param(optional_value):
    """Test fixture with None parameter."""
    assert optional_value is None or optional_value in ["value", 42]


# ==============================================================================
# Boolean parameters
# ==============================================================================


@pytest.fixture(params=[True, False])
def flag(request):
    """Fixture with boolean parameters."""
    return request.param


def test_bool_param(flag):
    """Test fixture with boolean parameter."""
    assert isinstance(flag, bool)
