"""Benchmark: Tests using fixtures."""

from rustest import fixture


def test_simple_number_fixture(simple_number):
    assert simple_number == 42


def test_simple_list_fixture(simple_list):
    assert len(simple_list) == 5
    assert sum(simple_list) == 15


def test_simple_dict_fixture(simple_dict):
    assert simple_dict["name"] == "test"
    assert simple_dict["value"] == 100
    assert simple_dict["active"] is True


def test_computed_value_fixture(computed_value):
    assert computed_value == 4950


def test_large_list_fixture(large_list):
    assert len(large_list) == 1000
    assert large_list[0] == 0
    assert large_list[-1] == 999


def test_nested_data_fixture(nested_data):
    assert len(nested_data["users"]) == 3
    assert nested_data["metadata"]["total"] == 3
    assert nested_data["users"][0]["name"] == "Alice"


def test_multiple_fixtures_1(simple_number, simple_list):
    assert simple_number in simple_list
    assert len(simple_list) == 5


def test_multiple_fixtures_2(simple_dict, computed_value):
    assert simple_dict["value"] < computed_value


def test_multiple_fixtures_3(simple_list, large_list):
    assert len(simple_list) < len(large_list)


def test_nested_fixtures_1(base_value):
    assert base_value == 10


def test_nested_fixtures_2(doubled):
    assert doubled == 20


def test_nested_fixtures_3(tripled):
    assert tripled == 30


def test_nested_fixtures_4(combined):
    assert combined == 50


def test_nested_fixtures_5(base_value, doubled, tripled):
    assert base_value * 2 == doubled
    assert base_value * 3 == tripled
    assert doubled + tripled == 50


def test_nested_fixtures_6(combined, base_value):
    assert combined == base_value * 5


@fixture
def local_fixture():
    """A local fixture defined in the test file."""
    return [10, 20, 30]


def test_local_fixture(local_fixture):
    assert sum(local_fixture) == 60


def test_combined_local_and_global(local_fixture, simple_list):
    assert len(local_fixture) == 3
    assert len(simple_list) == 5


@fixture
def fixture_with_computation():
    """Fixture that does more computation."""
    return {i: i**2 for i in range(50)}


def test_fixture_with_computation(fixture_with_computation):
    assert len(fixture_with_computation) == 50
    assert fixture_with_computation[5] == 25
    assert fixture_with_computation[10] == 100


def test_multiple_complex_fixtures(nested_data, fixture_with_computation, large_list):
    assert len(nested_data["users"]) == 3
    assert len(fixture_with_computation) == 50
    assert len(large_list) == 1000
