"""Comprehensive tests for fixture parametrization.

These tests verify that fixture parametrization works correctly with a pytest-compatible API.
"""

from rustest import fixture, parametrize, mark


# ==============================================================================
# Basic fixture parametrization tests
# ==============================================================================


@fixture(params=[1, 2, 3])
def number(request):
    """Basic parametrized fixture with integers."""
    return request.param


def test_basic_parametrized_fixture(number):
    """Test that basic parametrized fixture provides each value."""
    assert number in [1, 2, 3]
    assert isinstance(number, int)


@fixture(params=["apple", "banana", "cherry"])
def fruit(request):
    """Parametrized fixture with strings."""
    return request.param


def test_parametrized_fixture_strings(fruit):
    """Test parametrized fixture with string values."""
    assert fruit in ["apple", "banana", "cherry"]
    assert isinstance(fruit, str)


# ==============================================================================
# Custom IDs tests
# ==============================================================================


@fixture(params=[10, 20, 30], ids=["ten", "twenty", "thirty"])
def number_with_ids(request):
    """Parametrized fixture with custom IDs."""
    return request.param


def test_custom_ids(number_with_ids):
    """Test that custom IDs work correctly."""
    assert number_with_ids in [10, 20, 30]


@fixture(params=["a", "b"], ids=lambda x: f"letter_{x}")
def letter_callable_ids(request):
    """Parametrized fixture with callable IDs."""
    return request.param


def test_callable_ids(letter_callable_ids):
    """Test that callable IDs work correctly."""
    assert letter_callable_ids in ["a", "b"]


# ==============================================================================
# Multiple parametrized fixtures (cartesian product)
# ==============================================================================


@fixture(params=[1, 2])
def first_num(request):
    """First parametrized fixture for cartesian product test."""
    return request.param


@fixture(params=["x", "y"])
def letter(request):
    """Second parametrized fixture for cartesian product test."""
    return request.param


def test_multiple_parametrized_fixtures(first_num, letter):
    """Test cartesian product of two parametrized fixtures.

    This should produce 4 test cases: (1, x), (1, y), (2, x), (2, y)
    """
    assert first_num in [1, 2]
    assert letter in ["x", "y"]


@fixture(params=[True, False])
def bool_value(request):
    """Third parametrized fixture for three-way cartesian product."""
    return request.param


def test_three_parametrized_fixtures(first_num, letter, bool_value):
    """Test cartesian product of three parametrized fixtures.

    This should produce 8 test cases.
    """
    assert first_num in [1, 2]
    assert letter in ["x", "y"]
    assert bool_value in [True, False]


# ==============================================================================
# Combination with test parametrization
# ==============================================================================


@parametrize("multiplier", [10, 100])
def test_fixture_and_test_parametrization(number, multiplier):
    """Test combination of parametrized fixture and parametrized test.

    With number=[1,2,3] and multiplier=[10,100], this produces 6 test cases.
    """
    result = number * multiplier
    assert result in [10, 100, 20, 200, 30, 300]


# ==============================================================================
# Different fixture scopes with parametrization
# ==============================================================================


@fixture(scope="module", params=["config_a", "config_b"])
def module_config(request):
    """Module-scoped parametrized fixture."""
    return {"name": request.param, "value": len(request.param)}


def test_module_scoped_parametrized_fixture(module_config):
    """Test module-scoped parametrized fixture."""
    assert module_config["name"] in ["config_a", "config_b"]
    assert module_config["value"] > 0


@fixture(scope="function", params=[100, 200])
def function_scoped_param(request):
    """Function-scoped parametrized fixture."""
    return request.param


def test_function_scoped_parametrized(function_scoped_param):
    """Test function-scoped parametrized fixture."""
    assert function_scoped_param in [100, 200]


# ==============================================================================
# Yield fixtures with parametrization
# ==============================================================================


@fixture(params=["setup1", "setup2"])
def yielding_parametrized(request):
    """Yield fixture with parametrization."""
    setup_value = f"{request.param}_initialized"
    yield setup_value
    # Teardown code would go here


def test_yield_fixture_with_params(yielding_parametrized):
    """Test that yield fixtures work with parametrization."""
    assert yielding_parametrized in ["setup1_initialized", "setup2_initialized"]


# ==============================================================================
# Fixtures depending on parametrized fixtures
# ==============================================================================


@fixture
def dependent_fixture(number):
    """Fixture that depends on a parametrized fixture."""
    return number * 10


def test_dependent_on_parametrized(dependent_fixture):
    """Test fixture that depends on parametrized fixture."""
    assert dependent_fixture in [10, 20, 30]


@fixture
def double_dependent(dependent_fixture):
    """Fixture that depends on a fixture that depends on parametrized fixture."""
    return dependent_fixture + 5


def test_double_dependent(double_dependent):
    """Test nested dependency on parametrized fixture."""
    assert double_dependent in [15, 25, 35]


# ==============================================================================
# Complex value types
# ==============================================================================


@fixture(params=[{"key": "a"}, {"key": "b"}])
def dict_param(request):
    """Parametrized fixture with dict values."""
    return request.param


def test_dict_params(dict_param):
    """Test parametrized fixture with dict values."""
    assert "key" in dict_param
    assert dict_param["key"] in ["a", "b"]


@fixture(params=[[1, 2], [3, 4]])
def list_param(request):
    """Parametrized fixture with list values."""
    return request.param


def test_list_params(list_param):
    """Test parametrized fixture with list values."""
    assert len(list_param) == 2
    assert sum(list_param) in [3, 7]


@fixture(params=[None, 0, "", False])
def falsy_param(request):
    """Parametrized fixture with falsy values."""
    return request.param


def test_falsy_params(falsy_param):
    """Test parametrized fixture with falsy values."""
    assert not falsy_param


# ==============================================================================
# Fixtures with computed values
# ==============================================================================


@fixture(params=range(3))
def range_param(request):
    """Parametrized fixture using range."""
    return request.param


def test_range_params(range_param):
    """Test parametrized fixture with range values."""
    assert range_param in [0, 1, 2]


@fixture(params=[2**i for i in range(4)])
def power_of_two(request):
    """Parametrized fixture with computed values."""
    return request.param


def test_powers_of_two(power_of_two):
    """Test parametrized fixture with computed values."""
    assert power_of_two in [1, 2, 4, 8]


# ==============================================================================
# Single param tests
# ==============================================================================


@fixture(params=["only_one"])
def single_param(request):
    """Parametrized fixture with a single value."""
    return request.param


def test_single_param_fixture(single_param):
    """Test fixture with single param value."""
    assert single_param == "only_one"


# ==============================================================================
# Tuple params
# ==============================================================================


@fixture(params=[(1, "a"), (2, "b")])
def tuple_param(request):
    """Parametrized fixture with tuple values."""
    return request.param


def test_tuple_params(tuple_param):
    """Test parametrized fixture with tuple values."""
    num, char = tuple_param
    assert (num, char) in [(1, "a"), (2, "b")]


# ==============================================================================
# Class-based tests with parametrized fixtures
# ==============================================================================


class TestWithParametrizedFixtures:
    """Test class using parametrized fixtures."""

    def test_in_class(self, number):
        """Test parametrized fixture in a class."""
        assert number in [1, 2, 3]

    def test_multiple_in_class(self, number, fruit):
        """Test multiple parametrized fixtures in a class."""
        assert number in [1, 2, 3]
        assert fruit in ["apple", "banana", "cherry"]


# ==============================================================================
# Edge cases
# ==============================================================================


@fixture(params=[float("inf"), float("-inf")])
def infinity_param(request):
    """Parametrized fixture with infinity values."""
    return request.param


def test_infinity_params(infinity_param):
    """Test parametrized fixture with infinity values."""
    import math
    assert math.isinf(infinity_param)


@fixture(params=["", "   ", "\t\n"])
def whitespace_param(request):
    """Parametrized fixture with whitespace strings."""
    return request.param


def test_whitespace_params(whitespace_param):
    """Test parametrized fixture with whitespace strings."""
    assert whitespace_param.strip() == ""


# ==============================================================================
# Verify param value access
# ==============================================================================


@fixture(params=[42])
def verify_param_value(request):
    """Verify that request.param has the correct value."""
    assert request.param == 42
    return request.param


def test_param_value_verification(verify_param_value):
    """Test that param value is correctly accessible."""
    assert verify_param_value == 42


# ==============================================================================
# Test with different id generation
# ==============================================================================


@fixture(params=[
    {"name": "Alice", "age": 30},
    {"name": "Bob", "age": 25},
])
def person_dict(request):
    """Fixture with dict params to test auto-generated IDs."""
    return request.param


def test_auto_generated_dict_ids(person_dict):
    """Test that dict params get reasonable auto-generated IDs."""
    assert "name" in person_dict
    assert "age" in person_dict
