"""`filterwarnings = error`: a warning fails the phase it is raised in.

pytest's `catch_warnings_for_item` wraps each test's setup, call and teardown; the ini's
lines come first and a `filterwarnings` mark is applied after them, so the mark wins. Among
marks the *farthest* wins: `iter_markers` yields the closest first and each one is
inserted at the front of the filter list.
"""

import warnings

import pytest


def test_warning_fails_the_call():
    warnings.warn("deprecated", DeprecationWarning)


def test_the_ini_can_ignore():
    warnings.warn("ignored by the ini")


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_a_mark_beats_the_ini():
    warnings.warn("deprecated", DeprecationWarning)


@pytest.fixture
def warns_in_setup():
    warnings.warn("from a fixture", UserWarning)


def test_a_fixture_warning_is_a_setup_error(warns_in_setup):
    pass


def test_returning_a_value_warns():
    return 1


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.filterwarnings("error::UserWarning")
def test_the_top_decorator_wins():
    warnings.warn("which", UserWarning)


@pytest.mark.filterwarnings("ignore::UserWarning")
class TestClassMark:
    @pytest.mark.filterwarnings("error::UserWarning")
    def test_the_class_mark_beats_the_method_mark(self):
        warnings.warn("which", UserWarning)
