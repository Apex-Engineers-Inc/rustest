"""A module's `filterwarnings` mark beats a function's: it is applied last."""

import warnings

import pytest

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_the_module_mark_wins():
    warnings.warn("which", UserWarning)


def test_no_warning():
    pass
