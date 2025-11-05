"""Benchmark: Simple tests without fixtures or parametrization."""


def test_simple_math_1():
    assert 1 + 1 == 2


def test_simple_math_2():
    assert 2 + 2 == 4


def test_simple_math_3():
    assert 3 + 3 == 6


def test_simple_math_4():
    assert 4 + 4 == 8


def test_simple_math_5():
    assert 5 + 5 == 10


def test_simple_list_operations_1():
    lst = [1, 2, 3]
    assert len(lst) == 3


def test_simple_list_operations_2():
    lst = [1, 2, 3, 4, 5]
    assert sum(lst) == 15


def test_simple_list_operations_3():
    lst = list(range(10))
    assert len(lst) == 10


def test_simple_list_operations_4():
    lst = [x * 2 for x in range(5)]
    assert lst == [0, 2, 4, 6, 8]


def test_simple_list_operations_5():
    lst = [1, 2, 3]
    lst.append(4)
    assert len(lst) == 4


def test_simple_string_operations_1():
    s = "hello"
    assert s.upper() == "HELLO"


def test_simple_string_operations_2():
    s = "WORLD"
    assert s.lower() == "world"


def test_simple_string_operations_3():
    s = "hello world"
    assert s.split() == ["hello", "world"]


def test_simple_string_operations_4():
    s = "test"
    assert s * 3 == "testtesttest"


def test_simple_string_operations_5():
    s = "hello"
    assert s[:2] == "he"


def test_simple_dict_operations_1():
    d = {"a": 1, "b": 2}
    assert d["a"] == 1


def test_simple_dict_operations_2():
    d = {"x": 10, "y": 20}
    assert len(d) == 2


def test_simple_dict_operations_3():
    d = {}
    d["key"] = "value"
    assert "key" in d


def test_simple_dict_operations_4():
    d = {"a": 1, "b": 2, "c": 3}
    assert list(d.keys()) == ["a", "b", "c"]


def test_simple_dict_operations_5():
    d1 = {"a": 1}
    d2 = {"b": 2}
    d1.update(d2)
    assert len(d1) == 2


def test_simple_boolean_operations_1():
    assert True


def test_simple_boolean_operations_2():
    assert not False


def test_simple_boolean_operations_3():
    assert True and True


def test_simple_boolean_operations_4():
    assert True or False


def test_simple_boolean_operations_5():
    assert (1 < 2) and (2 < 3)


def test_computation_1():
    result = sum(range(100))
    assert result == 4950


def test_computation_2():
    result = [x**2 for x in range(10)]
    assert len(result) == 10


def test_computation_3():
    result = sum(x**2 for x in range(20))
    assert result == 2470


def test_computation_4():
    result = list(filter(lambda x: x % 2 == 0, range(20)))
    assert len(result) == 10


def test_computation_5():
    result = list(map(lambda x: x * 3, range(10)))
    assert sum(result) == 135
