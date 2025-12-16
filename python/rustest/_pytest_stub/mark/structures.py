"""
Stub for _pytest.mark.structures

MIGRATION GUIDE:
These are typically used for type hints.
Use pytest's or rustest's public mark API instead.
"""

import warnings


warnings.warn(
    "Importing from _pytest.mark.structures is not recommended. "
    "Use pytest.mark or rustest.mark instead.",
    DeprecationWarning,
    stacklevel=2,
)


class MarkDecorator:
    """Stub for mark decorator type (for type hints only)"""

    pass


class Mark:
    """Stub for mark type (for type hints only)"""

    pass


class ParameterSet:
    """Stub for parametrize parameter set (for type hints only)"""

    pass
