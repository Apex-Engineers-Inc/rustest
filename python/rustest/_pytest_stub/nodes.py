"""
Stub for _pytest.nodes

MIGRATION GUIDE:
These are typically used for type hints in pytest plugins.
rustest does not support pytest's plugin system.

If you're writing tests (not plugins), you likely don't need these imports.
"""

import warnings


warnings.warn(
    "Importing from _pytest.nodes is not recommended. "
    "These are pytest internals for plugin development. "
    "rustest does not support pytest's plugin API.",
    DeprecationWarning,
    stacklevel=2,
)


class Node:
    """
    Base class for collection nodes (stub for type hints).

    This is part of pytest's internal plugin API and is not
    supported by rustest.
    """

    pass


class Item(Node):
    """
    Base class for test items (stub for type hints).

    This is part of pytest's internal plugin API and is not
    supported by rustest.
    """

    pass


class Collector(Node):
    """Base class for collectors (stub for type hints)"""

    pass


class FSCollector(Collector):
    """Base class for filesystem collectors (stub for type hints)"""

    pass
