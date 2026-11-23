"""
Stub for _pytest.assertion

rustest rewrites ``assert`` statements in test modules itself, the way pytest does
(``rustest._assertion_rewrite``), so a failing ``assert a == b`` reports both operands.
Nothing in this stub is needed for that; it exists so the import does not fail.
"""

import warnings

# Import rewrite submodule
from . import rewrite

warnings.warn(
    "Importing from _pytest.assertion is not recommended. "
    "It is pytest's private API; rustest rewrites assertions without it.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["rewrite"]
