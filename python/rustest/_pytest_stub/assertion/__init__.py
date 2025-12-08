"""
Stub for _pytest.assertion

MIGRATION GUIDE:
Assertion rewriting is pytest-specific and not supported by rustest.
rustest uses Python's standard assertion mechanism.

Your tests should work fine with standard asserts:
    assert value == expected
    assert value in collection
"""

import warnings

# Import rewrite submodule
from . import rewrite

warnings.warn(
    "Importing from _pytest.assertion is not recommended. "
    "Assertion rewriting is pytest-specific and not supported by rustest. "
    "Use standard Python assertions.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["rewrite"]
