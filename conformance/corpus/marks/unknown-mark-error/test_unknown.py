"""An unregistered mark is `PytestUnknownMarkWarning` at import, which `filterwarnings = error`
makes a collection error for this module."""

import pytest


@pytest.mark.unregistered_thing
def test_unknown_mark():
    pass
