"""Test README code examples to ensure they work correctly."""

from rustest import fixture, parametrize, mark, approx, raises
import asyncio


@fixture
def numbers() -> list[int]:
    return [1, 2, 3, 4, 5]


def test_sum(numbers: list[int]) -> None:
    """Test from README: sum with fixture."""
    assert sum(numbers) == approx(15)


@parametrize("value,expected", [(2, 4), (3, 9), (4, 16)])
def test_square(value: int, expected: int) -> None:
    """Test from README: parametrized square."""
    assert value**2 == expected


@mark.slow
def test_expensive_operation() -> None:
    """Test from README: marked slow test."""
    result = sum(range(1000000))
    assert result > 0


@mark.asyncio
async def test_async_operation() -> None:
    """Test from README: async test with @mark.asyncio."""
    # Example async operation
    await asyncio.sleep(0.001)
    result = 42
    assert result == 42


def test_division_by_zero() -> None:
    """Test from README: exception testing with raises()."""
    with raises(ZeroDivisionError, match="division by zero"):
        1 / 0
