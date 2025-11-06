"""Conftest with fixtures that will be overridden by test module."""
import rustest as testlib


@testlib.fixture
def shared_name():
    """This will be overridden by the test module."""
    return "from_conftest"


@testlib.fixture
def conftest_only():
    """This won't be overridden."""
    return "conftest_value"


@testlib.fixture
def another_shared():
    """Another fixture that will be overridden."""
    return "conftest_version"
