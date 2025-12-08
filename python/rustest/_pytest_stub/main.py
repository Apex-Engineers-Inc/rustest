"""
Stub for _pytest.main

MIGRATION GUIDE:
Instead of importing from _pytest.main, use pytest's public API.

_pytest.main is pytest's internal module for test session management.
rustest does not support pytest's plugin API.
"""

import warnings

# Show warning on import
warnings.warn(
    "Importing from _pytest.main is not recommended. "
    "These are pytest internals for session management. "
    "rustest does not support pytest's plugin API.",
    DeprecationWarning,
    stacklevel=2,
)


class Session:
    """
    Stub for pytest Session class.

    This is a pytest internal class. rustest does not support pytest's plugin API.
    """

    def __init__(self, *args, **kwargs):
        pass

    def __repr__(self):
        return "<Session (rustest compat stub)>"
