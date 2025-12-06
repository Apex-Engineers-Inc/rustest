"""
Stub for _pytest.assertion.rewrite

MIGRATION GUIDE:
Assertion rewriting is a pytest-specific feature that rewrites assert
statements to provide detailed failure messages.

rustest does not support assertion rewriting, but provides good assertion
output through standard Python mechanisms.

If your code imports this, it's likely setting up pytest-specific hooks
that won't work in rustest. Consider removing these imports.
"""

import warnings


warnings.warn(
    "Assertion rewriting (_pytest.assertion.rewrite) is not supported by rustest. "
    "This is a pytest-specific feature. "
    "Standard Python assertions work normally in rustest.",
    DeprecationWarning,
    stacklevel=2,
)


class AssertionRewritingHook:
    """
    Stub for assertion rewriting hook.

    This is NOT supported in rustest. Assertion rewriting is a pytest-specific
    feature that modifies bytecode to provide better error messages.

    rustest uses standard Python assertions which work normally.
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Assertion rewriting is not supported by rustest. "
            "This is a pytest-specific feature. "
            "Please remove imports from _pytest.assertion.rewrite."
        )
