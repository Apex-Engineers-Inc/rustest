"""Tests for pytest.fail() function."""

from __future__ import annotations

import pytest

from rustest import fail, Failed, raises


def test_fail_raises_failed_exception():
    """Test that fail() raises Failed exception."""
    with raises(Failed):
        fail()


def test_fail_with_message():
    """Test that fail() includes the message in the exception."""
    with raises(Failed, match="Custom failure message"):
        fail("Custom failure message")


def test_fail_without_message():
    """Test that fail() can be called without a message."""
    with raises(Failed):
        fail("")


def test_fail_stops_test_execution():
    """Test that fail() stops test execution immediately."""
    executed_after_fail = False

    with raises(Failed):
        fail("Test failed")
        executed_after_fail = True  # noqa: F841 - This should never execute

    # Verify the code after fail() was not executed
    assert not executed_after_fail


def test_fail_in_conditional():
    """Test fail() used in conditional logic."""

    def validate_data(data: dict[str, object]) -> None:
        if "required_field" not in data:
            fail("Missing required field")

    with raises(Failed, match="Missing required field"):
        validate_data({})


def test_fail_with_detailed_message():
    """Test fail() with a detailed error message."""
    error_details = {"code": 404, "message": "Not Found"}

    with raises(Failed, match="Operation failed.*404.*Not Found"):
        fail(f"Operation failed: {error_details}")


def test_fail_pytrace_parameter():
    """``pytrace`` reaches the exception, rather than being accepted and dropped.

    It was accepted and dropped until the Phase 4 convergence wave, and this test asserted
    only that the call did not raise a ``TypeError`` -- which a signature with the parameter
    misspelled would also have satisfied. ``Failed.pytrace`` is the channel
    ``_worker.py::_module_outcome_error_message`` reads to decide between pytest's two
    collection-error renderings, so the value has to survive the call.
    """
    with raises(Failed) as traced:
        fail("Test failure", pytrace=True)
    assert traced.value.pytrace is True

    with raises(Failed) as untraced:
        fail("Test failure", pytrace=False)
    assert untraced.value.pytrace is False

    with raises(Failed) as defaulted:
        fail("Test failure")
    assert defaulted.value.pytrace is True


def test_failed_exception_is_a_base_exception():
    """`Failed` is a **BaseException**, as pytest's is.

    `_pytest/outcomes.py` declares `class OutcomeException(BaseException)` and
    `class Failed(OutcomeException)` so that a test body's `except Exception:` cannot
    swallow the runner's own "this test failed" signal. Measured before the change: a
    `raises` block that did not raise, wrapped in `except Exception`, reported **passed**
    under rustest and **failed** under pytest.
    """
    assert issubclass(Failed, BaseException)
    assert not issubclass(Failed, Exception)


def test_failed_exception_message():
    """Test that Failed exception carries the message."""
    try:
        fail("Error message")
    except Failed as e:
        assert str(e) == "Error message"
    else:
        pytest.fail("Expected Failed exception")


def test_fail_vs_assert():
    """Test that fail() is different from assert False."""
    # Both should fail the test, but fail() raises Failed, not AssertionError

    with raises(Failed):
        fail("Explicit failure")

    with raises(AssertionError):
        assert False, "Assertion failure"


def test_multiple_fail_conditions():
    """Test fail() in multiple conditional branches."""

    def check_value(value: int) -> None:
        if value < 0:
            fail("Value must be non-negative")
        if value > 100:
            fail("Value must be <= 100")
        if value == 50:
            fail("Value 50 is not allowed")

    # Test each condition
    with raises(Failed, match="non-negative"):
        check_value(-1)

    with raises(Failed, match="<= 100"):
        check_value(101)

    with raises(Failed, match="not allowed"):
        check_value(50)

    # Valid values should not raise
    check_value(25)
    check_value(75)


def test_fail_with_empty_string():
    """Test fail() with empty string message."""
    with raises(Failed) as exc_info:
        fail("")

    assert str(exc_info.value) == ""


def test_fail_with_multiline_message():
    """Test fail() with multiline message."""
    message = """
    Line 1: Something went wrong
    Line 2: More details here
    Line 3: Additional context
    """

    with raises(Failed) as exc_info:
        fail(message)

    assert "Line 1" in str(exc_info.value)
    assert "Line 2" in str(exc_info.value)
    assert "Line 3" in str(exc_info.value)
