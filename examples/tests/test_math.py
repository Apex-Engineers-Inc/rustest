from __future__ import annotations

from rustest import fixture, parametrize


@fixture
def base_number() -> int:
    return 10


@parametrize("value,expected", [(1, 11), (5, 15), (10, 20)], ids=["one", "five", "ten"])
def test_increment(base_number: int, value: int, expected: int) -> None:
    assert base_number + value == expected
