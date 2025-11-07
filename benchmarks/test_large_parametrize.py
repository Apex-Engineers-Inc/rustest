"""Stress-test parametrization with a large argument matrix."""

from rustest import parametrize


@parametrize("value", list(range(10_000)))
def test_linear_parametrize(value):
    """Ensure arithmetic remains correct across a large dataset."""
    assert value + value == 2 * value
