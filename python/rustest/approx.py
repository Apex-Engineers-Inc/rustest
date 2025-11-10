"""Approximate comparison for floating-point numbers.

This module provides the `approx` class for comparing floating-point numbers
with a tolerance, similar to ``pytest.approx``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Union, cast

ApproxScalar = Union[float, int, complex]
ApproxValue = Union[ApproxScalar, Sequence["ApproxValue"], Mapping[str, "ApproxValue"]]


class approx:
    """Assert that two numbers (or collections of numbers) are equal to each other
    within some tolerance.

    This is similar to pytest.approx and is useful for comparing floating-point
    numbers that may have small rounding errors.

    Usage:
        assert 0.1 + 0.2 == approx(0.3)
        assert 0.1 + 0.2 == approx(0.3, rel=1e-6)
        assert 0.1 + 0.2 == approx(0.3, abs=1e-9)
        assert [0.1 + 0.2, 0.3] == approx([0.3, 0.3])
        assert {"a": 0.1 + 0.2} == approx({"a": 0.3})

    Args:
        expected: The expected value to compare against
        rel: The relative tolerance (default: 1e-6)
        abs: The absolute tolerance (default: 1e-12)

    By default, numbers are considered close if the difference between them is
    less than or equal to:
        abs(expected * rel) + abs_tolerance
    """

    def __init__(
        self,
        expected: ApproxValue,
        rel: float = 1e-6,
        abs: float = 1e-12,
    ) -> None:
        """Initialize approx with expected value and tolerances.

        Args:
            expected: The expected value to compare against
            rel: The relative tolerance (default: 1e-6)
            abs: The absolute tolerance (default: 1e-12)
        """
        super().__init__()
        self.expected = expected
        self.rel = rel
        self.abs = abs

    def __repr__(self) -> str:
        """Return a string representation of the approx object."""
        return f"approx({self.expected!r}, rel={self.rel}, abs={self.abs})"

    def __eq__(self, actual: Any) -> bool:
        """Compare actual value with expected value within tolerance.

        Args:
            actual: The actual value to compare

        Returns:
            True if the values are approximately equal, False otherwise
        """
        return self._approx_compare(actual, self.expected)

    def _approx_compare(self, actual: Any, expected: Any) -> bool:
        """Recursively compare actual and expected values.

        Args:
            actual: The actual value
            expected: The expected value

        Returns:
            True if values are approximately equal, False otherwise
        """
        # Handle None
        if actual is None or expected is None:
            return actual == expected

        # Handle dictionaries
        if isinstance(expected, Mapping):
            expected_mapping = cast(Mapping[str, ApproxValue], expected)
            if not isinstance(actual, Mapping):
                return False
            actual_mapping = cast(Mapping[str, ApproxValue], actual)
            if set(actual_mapping.keys()) != set(expected_mapping.keys()):
                return False
            return all(
                self._approx_compare(actual_mapping[key], expected_mapping[key])
                for key in expected_mapping
            )

        # Handle sequences (lists, tuples, etc.) but not strings
        if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
            expected_sequence = cast(Sequence[ApproxValue], expected)
            if not (
                isinstance(actual, Sequence)
                and not isinstance(actual, (str, bytes, bytearray))
                and type(actual) is type(expected)
            ):
                return False
            actual_sequence = cast(Sequence[ApproxValue], actual)
            if len(actual_sequence) != len(expected_sequence):
                return False
            return all(
                self._approx_compare(actual_item, expected_item)
                for actual_item, expected_item in zip(actual_sequence, expected_sequence)
            )

        # Handle numbers (float, int, complex)
        if isinstance(expected, (float, int, complex)) and isinstance(
            actual, (float, int, complex)
        ):
            return self._is_close(actual, expected)

        # For other types, use exact equality
        return actual == expected

    def _is_close(
        self, actual: Union[float, int, complex], expected: Union[float, int, complex]
    ) -> bool:
        """Check if two numbers are close within tolerance.

        Uses the formula: |actual - expected| <= max(rel * max(|actual|, |expected|), abs)

        Args:
            actual: The actual number
            expected: The expected number

        Returns:
            True if numbers are close, False otherwise
        """
        # Handle infinities and NaN
        if isinstance(actual, complex) or isinstance(expected, complex):
            # For complex numbers, compare real and imaginary parts separately
            if isinstance(actual, complex) and isinstance(expected, complex):
                return self._is_close(actual.real, expected.real) and self._is_close(
                    actual.imag, expected.imag
                )
            # One is complex, the other is not
            if isinstance(actual, complex):
                return self._is_close(actual.real, expected) and abs(actual.imag) <= self.abs
            else:  # expected is complex
                return self._is_close(actual, expected.real) and abs(expected.imag) <= self.abs

        # Convert to float for comparison
        actual_float = float(actual)
        expected_float = float(expected)

        # Handle special float values
        if actual_float == expected_float:
            # This handles infinities and zeros
            return True

        # Check for NaN - NaN should never be equal to anything
        import math

        if math.isnan(actual_float) or math.isnan(expected_float):
            return False

        # Check for infinities
        if math.isinf(actual_float) or math.isinf(expected_float):
            return actual_float == expected_float

        # Calculate tolerance
        abs_diff = abs(actual_float - expected_float)
        tolerance = max(self.rel * max(abs(actual_float), abs(expected_float)), self.abs)

        return abs_diff <= tolerance
