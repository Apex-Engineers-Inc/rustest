"""A new-style wrapper that raises after the body, and an old-style hookwrapper that
forces an exception into the result: both make the call phase fail, as in pytest."""

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    result = yield
    if item.name == "test_wrapper_raises_after":
        raise RuntimeError("raised after the body")
    return result


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    outcome = yield
    if item.name == "test_setup_forced":
        outcome.force_exception(RuntimeError("forced in setup"))


class Outermost:
    """An old-style wrapper outside the new-style one: get_result() re-raises."""

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item):
        outcome = yield
        if item.name == "test_body_fails":
            outcome.force_result(None)  # forcing a result swallows the body's failure
        outcome.get_result()


def pytest_configure(config):
    config.pluginmanager.register(Outermost())
