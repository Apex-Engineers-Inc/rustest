"""Marks the ini declares, and pytest's own, raise nothing."""

import pytest


@pytest.mark.registered
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_registered_mark():
    pass
