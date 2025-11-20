"""Tests for caplog fixture."""

from __future__ import annotations

import logging
import pytest

from rustest.builtin_fixtures import LogCaptureFixture


@pytest.fixture
def test_caplog():
    """Provide a rustest LogCaptureFixture instance for testing."""
    capture = LogCaptureFixture()
    capture.start_capture()
    try:
        yield capture
    finally:
        capture.stop_capture()


def test_caplog_captures_logging(test_caplog):
    """Test that caplog captures logging messages."""
    logging.info("Test message")
    assert "Test message" in test_caplog.text


def test_caplog_records(test_caplog):
    """Test access to captured LogRecord objects."""
    logging.info("Info message")
    logging.warning("Warning message")

    assert len(test_caplog.records) == 2
    assert test_caplog.records[0].levelname == "INFO"
    assert test_caplog.records[1].levelname == "WARNING"


def test_caplog_messages(test_caplog):
    """Test access to captured messages as strings."""
    logging.info("First")
    logging.error("Second")

    assert test_caplog.messages == ["First", "Second"]


def test_caplog_record_tuples(test_caplog):
    """Test access to (name, level, message) tuples."""
    logger = logging.getLogger("my.logger")
    logger.info("Test")

    assert len(test_caplog.record_tuples) == 1
    name, level, message = test_caplog.record_tuples[0]
    assert name == "my.logger"
    assert level == logging.INFO
    assert message == "Test"


def test_caplog_text(test_caplog):
    """Test that caplog.text contains all messages."""
    logging.info("Line 1")
    logging.warning("Line 2")
    logging.error("Line 3")

    expected = "Line 1\nLine 2\nLine 3"
    assert test_caplog.text == expected


def test_caplog_clear(test_caplog):
    """Test clearing captured logs."""
    logging.info("Message 1")
    assert len(test_caplog.records) == 1

    test_caplog.clear()
    assert len(test_caplog.records) == 0

    logging.info("Message 2")
    assert len(test_caplog.records) == 1
    assert test_caplog.messages == ["Message 2"]


def test_caplog_set_level(test_caplog):
    """Test setting the log level."""
    test_caplog.set_level(logging.WARNING)

    logging.debug("Debug")  # Not captured
    logging.info("Info")  # Not captured
    logging.warning("Warning")  # Captured
    logging.error("Error")  # Captured

    assert len(test_caplog.records) == 2
    assert test_caplog.messages == ["Warning", "Error"]


def test_caplog_set_level_string(test_caplog):
    """Test setting log level with string."""
    test_caplog.set_level("ERROR")

    logging.warning("Warning")  # Not captured
    logging.error("Error")  # Captured

    assert len(test_caplog.records) == 1
    assert test_caplog.messages == ["Error"]


def test_caplog_at_level_context(test_caplog):
    """Test at_level context manager."""
    logging.info("Before")

    with test_caplog.at_level(logging.ERROR):
        logging.warning("Not captured in context")
        logging.error("Captured in context")

    logging.info("After")

    # Should have: Before, Captured in context, After
    assert len(test_caplog.records) == 3
    assert test_caplog.messages == ["Before", "Captured in context", "After"]


def test_caplog_at_level_string(test_caplog):
    """Test at_level with string level."""
    with test_caplog.at_level("WARNING"):
        logging.info("Not captured")
        logging.warning("Captured")

    assert len(test_caplog.records) == 1
    assert test_caplog.messages == ["Captured"]


def test_caplog_at_level_specific_logger(test_caplog):
    """Test at_level with specific logger."""
    my_logger = logging.getLogger("my.app")

    with test_caplog.at_level(logging.WARNING, logger="my.app"):
        my_logger.info("Not captured")
        my_logger.warning("Captured")
        logging.info("Also captured (root logger still at DEBUG)")

    assert len(test_caplog.records) == 2


def test_caplog_multiple_loggers(test_caplog):
    """Test capturing from multiple loggers."""
    logger1 = logging.getLogger("app.module1")
    logger2 = logging.getLogger("app.module2")

    logger1.info("From module1")
    logger2.warning("From module2")

    assert len(test_caplog.records) == 2
    assert test_caplog.record_tuples[0][0] == "app.module1"
    assert test_caplog.record_tuples[1][0] == "app.module2"


def test_caplog_different_levels(test_caplog):
    """Test capturing at different log levels."""
    logging.debug("Debug")
    logging.info("Info")
    logging.warning("Warning")
    logging.error("Error")
    logging.critical("Critical")

    assert len(test_caplog.records) == 5
    levels = [r.levelname for r in test_caplog.records]
    assert levels == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def test_caplog_with_exception(test_caplog):
    """Test capturing logs with exception info."""
    try:
        raise ValueError("Test error")
    except ValueError:
        logging.exception("An error occurred")

    assert len(test_caplog.records) == 1
    assert "An error occurred" in test_caplog.text
    # Exception info is stored in the record
    assert test_caplog.records[0].exc_info is not None


def test_caplog_formatted_messages(test_caplog):
    """Test capturing formatted log messages."""
    logging.info("User %s logged in", "alice")
    logging.warning("Failed %d times", 3)

    assert test_caplog.messages == ["User alice logged in", "Failed 3 times"]


def test_caplog_empty_initially(test_caplog):
    """Test that caplog is empty at start."""
    assert len(test_caplog.records) == 0
    assert test_caplog.messages == []
    assert test_caplog.text == ""


def test_caplog_isolation_between_tests(test_caplog):
    """Test that caplog is isolated between tests."""
    # This test should start with empty caplog
    assert len(test_caplog.records) == 0

    logging.info("Test message")
    assert len(test_caplog.records) == 1


def test_caplog_assert_patterns(test_caplog):
    """Test common assertion patterns with caplog."""
    logging.info("Starting process")
    logging.info("Processing item 1")
    logging.info("Processing item 2")
    logging.info("Finished process")

    # Assert specific message is present
    assert "Starting process" in test_caplog.text

    # Assert message count
    assert len(test_caplog.messages) == 4

    # Assert all messages contain a word
    assert all("process" in msg.lower() for msg in test_caplog.messages)

    # Assert any message contains a word
    assert any("item 1" in msg for msg in test_caplog.messages)


def test_caplog_nested_at_level(test_caplog):
    """Test nested at_level contexts."""
    logging.info("Level 0")

    with test_caplog.at_level(logging.WARNING):
        logging.info("Level 1 - not captured")
        logging.warning("Level 1 - captured")

        with test_caplog.at_level(logging.ERROR):
            logging.warning("Level 2 - not captured")
            logging.error("Level 2 - captured")

        logging.warning("Back to level 1")

    logging.info("Back to level 0")

    captured_messages = test_caplog.messages
    assert "Level 0" in captured_messages
    assert "Level 1 - captured" in captured_messages
    assert "Level 2 - captured" in captured_messages
    assert "Back to level 1" in captured_messages
    assert "Back to level 0" in captured_messages
    assert "Level 1 - not captured" not in captured_messages
    assert "Level 2 - not captured" not in captured_messages
