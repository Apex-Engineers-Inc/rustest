from rustest import parametrize


@parametrize("value,expected", [(2, 4), (3, 9), (4, 16)], ids=["double", "triple", "quad"])
def test_power(value: int, expected: int) -> None:
    assert value * value == expected
