"""
Stub for _pytest.assertion.rewrite

rustest rewrites ``assert`` statements in test modules with its own import hook
(``rustest._assertion_rewrite``). pytest's hook class is not part of it: code that installs
``AssertionRewritingHook`` itself is configuring pytest's plugin machinery, which rustest
does not have.
"""

import warnings


warnings.warn(
    "Importing from _pytest.assertion.rewrite is not recommended. "
    "It is pytest's private API; rustest rewrites assertions with its own hook.",
    DeprecationWarning,
    stacklevel=2,
)


class AssertionRewritingHook:
    """Stub: rustest's own hook rewrites test modules; this one cannot be installed."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "_pytest.assertion.rewrite.AssertionRewritingHook is pytest's; rustest rewrites "
            "test-module assertions with its own hook. Remove the direct use."
        )
