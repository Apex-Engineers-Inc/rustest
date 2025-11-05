"""Benchmark: Tests combining fixtures and parametrization."""

from rustest import fixture, parametrize


@parametrize("multiplier", [(2,), (3,), (4,), (5,)])
def test_multiply_simple_number(simple_number, multiplier):
    assert simple_number * multiplier == 42 * multiplier


@parametrize("index", [(0,), (1,), (2,), (3,), (4,)])
def test_simple_list_access(simple_list, index):
    assert simple_list[index] == index + 1


@parametrize("key", [("name",), ("value",), ("active",)])
def test_simple_dict_keys(simple_dict, key):
    assert key in simple_dict


@parametrize("factor", [(1,), (2,), (3,)])
def test_computed_value_operations(computed_value, factor):
    assert computed_value * factor == 4950 * factor


@parametrize("n", [(10,), (50,), (100,), (500,)])
def test_large_list_slicing(large_list, n):
    assert len(large_list[:n]) == n


@parametrize("user_id", [(0,), (1,), (2,)])
def test_nested_data_users(nested_data, user_id):
    assert nested_data["users"][user_id]["id"] == user_id + 1


@parametrize("multiplier", [(1,), (2,), (3,), (4,)])
def test_base_value_parametrized(base_value, multiplier):
    assert base_value * multiplier == 10 * multiplier


@parametrize("x", [(5,), (10,), (15,)])
def test_doubled_parametrized(doubled, x):
    assert doubled + x in [25, 30, 35]


@parametrize("divisor", [(2,), (5,), (10,)])
def test_combined_parametrized(combined, divisor):
    assert combined % divisor == 50 % divisor


@fixture
def string_fixture():
    return "hello world"


@parametrize("word", [("hello",), ("world",), ("test",)])
def test_string_contains(string_fixture, word):
    if word in ["hello", "world"]:
        assert word in string_fixture
    else:
        assert word not in string_fixture


@fixture
def numbers_fixture():
    return [10, 20, 30, 40, 50]


@parametrize("value,expected", [(10, True), (25, False), (30, True), (100, False)])
def test_value_in_list(numbers_fixture, value, expected):
    assert (value in numbers_fixture) == expected


@parametrize("start,end", [(0, 2), (1, 3), (2, 4)])
def test_list_slicing_parametrized(simple_list, start, end):
    result = simple_list[start:end]
    assert len(result) == end - start


@parametrize("power", [(1,), (2,), (3,)])
def test_nested_fixture_computation(combined, power):
    result = combined**power
    assert result == 50**power


@parametrize("offset", [(0,), (10,), (20,), (30,)])
def test_multiple_fixtures_parametrized(simple_number, computed_value, offset):
    result = simple_number + computed_value + offset
    assert result == 42 + 4950 + offset


@fixture
def complex_data():
    return {"values": [1, 2, 3, 4, 5], "multipliers": [2, 3, 4], "offset": 10}


@parametrize("index", [(0,), (1,), (2,)])
def test_complex_fixture_access(complex_data, index):
    assert complex_data["values"][index] == index + 1
    assert complex_data["multipliers"][index] == index + 2
