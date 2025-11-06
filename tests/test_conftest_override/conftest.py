"""Conftest with fixtures that will be overridden by test module."""
import rustest


@rustest.fixture
def shared_name():
    """This will be overridden by the test module."""
    return "from_conftest"


@rustest.fixture
def conftest_only():
    """This won't be overridden."""
    return "conftest_value"


@rustest.fixture
def another_shared():
    """Another fixture that will be overridden."""
    return "conftest_version"
