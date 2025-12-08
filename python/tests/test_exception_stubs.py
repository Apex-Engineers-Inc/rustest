"""Tests for _pytest exception stub attributes."""

import pytest


def test_failed_exception_has_msg_attribute() -> None:
    """Test that Failed exception has .msg attribute."""
    from rustest._pytest_stub.outcomes import Failed

    exc = Failed("test message")
    assert hasattr(exc, "msg"), "Failed exception should have .msg attribute"
    assert exc.msg == "test message"


def test_failed_exception_has_pytrace_attribute() -> None:
    """Test that Failed exception has .pytrace attribute."""
    from rustest._pytest_stub.outcomes import Failed

    exc = Failed("test", pytrace=False)
    assert hasattr(exc, "pytrace"), "Failed exception should have .pytrace attribute"
    assert exc.pytrace is False

    exc2 = Failed("test")
    assert exc2.pytrace is True  # Default value


def test_skipped_exception_has_msg_attribute() -> None:
    """Test that Skipped exception has .msg attribute."""
    from rustest._pytest_stub.outcomes import Skipped

    exc = Skipped("skip reason")
    assert hasattr(exc, "msg"), "Skipped exception should have .msg attribute"
    assert exc.msg == "skip reason"


def test_skipped_exception_has_allow_module_level() -> None:
    """Test that Skipped exception has .allow_module_level attribute."""
    from rustest._pytest_stub.outcomes import Skipped

    exc = Skipped("skip", allow_module_level=True)
    assert hasattr(exc, "allow_module_level"), "Skipped exception should have .allow_module_level"
    assert exc.allow_module_level is True

    exc2 = Skipped("skip")
    assert exc2.allow_module_level is False  # Default value


def test_failed_exception_with_raises() -> None:
    """Test using Failed exception with pytest.raises."""
    from rustest._pytest_stub.outcomes import Failed

    with pytest.raises(Failed) as excinfo:
        raise Failed("custom message", pytrace=False)

    assert excinfo.value.msg == "custom message"
    assert excinfo.value.pytrace is False


def test_skipped_exception_with_raises() -> None:
    """Test using Skipped exception with pytest.raises."""
    from rustest._pytest_stub.outcomes import Skipped

    with pytest.raises(Skipped) as excinfo:
        raise Skipped("skip this", allow_module_level=True)

    assert excinfo.value.msg == "skip this"
    assert excinfo.value.allow_module_level is True


def test_failed_exception_string_representation() -> None:
    """Test that Failed exception has proper string representation."""
    from rustest._pytest_stub.outcomes import Failed

    exc = Failed("error occurred")
    assert str(exc) == "error occurred"


def test_skipped_exception_string_representation() -> None:
    """Test that Skipped exception has proper string representation."""
    from rustest._pytest_stub.outcomes import Skipped

    exc = Skipped("reason for skip")
    assert str(exc) == "reason for skip"


def test_exception_attributes_in_except_block() -> None:
    """Test accessing exception attributes in except block."""
    from rustest._pytest_stub.outcomes import Failed

    try:
        raise Failed("test failure", pytrace=True)
    except Failed as e:
        # Should be able to access attributes without AttributeError
        assert e.msg == "test failure"
        assert e.pytrace is True
        assert isinstance(e, Exception)
