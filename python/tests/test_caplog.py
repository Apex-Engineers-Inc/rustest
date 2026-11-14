"""``caplog`` behaviour not pinned elsewhere, against the class the fixture really returns.

Levels, phases, filtering and ``text`` formatting are in ``test_builtins.py``; the fixture
end to end is in ``test_builtin_fixtures.py`` and the ``builtins/caplog-levels`` corpus case.
"""

from __future__ import annotations

import logging

from rustest import LogCaptureFixture
from rustest._builtins import LogCapture


def test_set_level_and_at_level_accept_a_level_name() -> None:
    """pytest takes ``"INFO"`` as well as ``logging.INFO`` for both."""
    capture = LogCapture()
    caplog = LogCaptureFixture(capture)
    logger = logging.getLogger("by-name")
    with capture.phase("call"):
        caplog.set_level("INFO", logger="by-name")
        logger.info("info")
        with caplog.at_level("DEBUG", logger="by-name"):
            logger.debug("debug")
        logger.debug("dropped")
        assert caplog.messages == ["info", "debug"]
        caplog._finalize()  # pyright: ignore[reportPrivateUsage]
    assert logger.level == logging.NOTSET


def test_an_exception_record_keeps_exc_info_and_text_carries_the_traceback() -> None:
    capture = LogCapture()
    caplog = LogCaptureFixture(capture)
    with capture.phase("call"):
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("exc").exception("failed")
        (record,) = caplog.records
        assert record.exc_info is not None
        assert "ValueError: boom" in caplog.text
