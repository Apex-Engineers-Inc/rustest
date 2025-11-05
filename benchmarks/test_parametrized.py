"""Benchmark: Tests using parametrization."""

from rustest import parametrize


@parametrize("value,expected", [(1, 2), (2, 4), (3, 6), (4, 8), (5, 10)])
def test_double(value, expected):
    assert value * 2 == expected


@parametrize("value,expected", [(2, 4), (3, 9), (4, 16), (5, 25), (10, 100)])
def test_square(value, expected):
    assert value**2 == expected


@parametrize("input_str", [("hello",), ("world",), ("test",), ("python",), ("rust",)])
def test_string_upper(input_str):
    assert input_str.upper() == input_str.upper()
    assert isinstance(input_str.upper(), str)


@parametrize(
    "lst,expected",
    [
        ([1, 2, 3], 6),
        ([10, 20, 30], 60),
        ([5, 10, 15], 30),
        ([1, 1, 1], 3),
        ([100], 100),
    ],
)
def test_sum_list(lst, expected):
    assert sum(lst) == expected


@parametrize(
    "a,b,expected",
    [
        (1, 1, 2),
        (2, 3, 5),
        (10, 20, 30),
        (100, 200, 300),
        (5, 5, 10),
    ],
)
def test_addition(a, b, expected):
    assert a + b == expected


@parametrize(
    "value,expected",
    [(0, True), (1, False), (2, True), (3, False), (4, True)],
    ids=["zero", "one", "two", "three", "four"],
)
def test_is_even(value, expected):
    assert (value % 2 == 0) == expected


@parametrize(
    "text,char,count",
    [
        ("hello", "l", 2),
        ("python", "p", 1),
        ("mississippi", "s", 4),
        ("test", "t", 2),
        ("aaa", "a", 3),
    ],
)
def test_count_chars(text, char, count):
    assert text.count(char) == count


@parametrize(
    "range_end,expected_len",
    [(10, 10), (20, 20), (50, 50), (100, 100), (5, 5)],
)
def test_list_comprehension(range_end, expected_len):
    result = [x * 2 for x in range(range_end)]
    assert len(result) == expected_len


@parametrize(
    "numbers,expected",
    [
        ([1, 2, 3, 4, 5], 15),
        ([10, 20, 30], 60),
        ([1] * 10, 10),
        (list(range(11)), 55),
        ([100, 200, 300], 600),
    ],
)
def test_sum_computation(numbers, expected):
    assert sum(numbers) == expected


@parametrize(
    "start,end,step",
    [(0, 10, 1), (0, 20, 2), (1, 10, 1), (5, 15, 1), (0, 100, 10)],
)
def test_range_operations(start, end, step):
    result = list(range(start, end, step))
    assert len(result) == (end - start) // step


@parametrize(
    "dict_data,key,expected",
    [
        ({"a": 1, "b": 2}, "a", 1),
        ({"x": 10, "y": 20}, "y", 20),
        ({"name": "test"}, "name", "test"),
        ({"value": 42}, "value", 42),
        ({"result": True}, "result", True),
    ],
)
def test_dict_access(dict_data, key, expected):
    assert dict_data[key] == expected


@parametrize(
    "x,y,operator,expected",
    [
        (10, 5, "add", 15),
        (10, 5, "sub", 5),
        (10, 5, "mul", 50),
        (10, 5, "div", 2),
        (10, 3, "mod", 1),
    ],
)
def test_operations(x, y, operator, expected):
    if operator == "add":
        assert x + y == expected
    elif operator == "sub":
        assert x - y == expected
    elif operator == "mul":
        assert x * y == expected
    elif operator == "div":
        assert x // y == expected
    elif operator == "mod":
        assert x % y == expected
