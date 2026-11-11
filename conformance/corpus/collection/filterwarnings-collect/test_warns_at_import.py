"""A warning at import, under `filterwarnings = error`, is a collection error."""

import warnings

warnings.warn("at import", UserWarning)


def test_never_runs():
    pass
