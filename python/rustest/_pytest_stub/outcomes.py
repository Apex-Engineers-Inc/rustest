"""
Stub for _pytest.outcomes

MIGRATION GUIDE:
Instead of:
    from _pytest.outcomes import Failed, Skipped

Use pytest's public API:
    import pytest

    pytest.fail("message")
    pytest.skip("reason")

Or catch standard exceptions:
    try:
        assert False
    except AssertionError:
        pass
"""

import warnings


# Show warning on import
warnings.warn(
    "Importing from _pytest.outcomes is not recommended. "
    "Use pytest.fail() and pytest.skip() instead. "
    "These exceptions are provided for compatibility only.",
    DeprecationWarning,
    stacklevel=2,
)


class Failed(Exception):
    """
    Test failure exception (compatible with pytest).

    Instead of:
        from _pytest.outcomes import Failed
        raise Failed("message")

    Use:
        import pytest
        pytest.fail("message")

    Or just:
        assert False, "message"
    """

    def __init__(self, msg: str = "", pytrace: bool = True):
        """Initialize Failed exception.

        Args:
            msg: Failure message
            pytrace: Whether to show Python traceback (pytest compatibility)
        """
        super().__init__(msg)
        self.msg = msg
        self.pytrace = pytrace


class Skipped(Exception):
    """
    Test skip exception (compatible with pytest).

    Instead of:
        from _pytest.outcomes import Skipped
        raise Skipped("reason")

    Use:
        import pytest
        pytest.skip("reason")
    """

    def __init__(self, msg: str = "", allow_module_level: bool = False):
        """Initialize Skipped exception.

        Args:
            msg: Skip reason
            allow_module_level: Whether to allow skipping at module level (pytest compatibility)
        """
        super().__init__(msg)
        self.msg = msg
        self.allow_module_level = allow_module_level


def fail(msg: str = "", pytrace: bool = True):
    """
    Fail the current test with a message.

    Prefer using: pytest.fail(msg)
    """
    raise Failed(msg)


def skip(msg: str = ""):
    """
    Skip the current test with a reason.

    Prefer using: pytest.skip(msg)
    """
    raise Skipped(msg)


# Pytest internal - outcome tuple for internal pytest use
# This is not part of pytest's public API
TEST_OUTCOME = (Failed, Skipped)
