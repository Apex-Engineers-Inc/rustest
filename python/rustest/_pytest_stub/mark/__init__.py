"""
Stub for _pytest.mark

MIGRATION GUIDE:
Instead of:
    from _pytest.mark.structures import MarkDecorator

Use pytest's public API:
    import pytest

    @pytest.mark.skip
    def test_example():
        pass

Or with rustest:
    from rustest import mark

    @mark.skip
    def test_example():
        pass
"""

import warnings

# Import structures submodule
from . import structures

warnings.warn(
    "Importing from _pytest.mark is not recommended. Use pytest.mark or rustest.mark instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["structures"]
