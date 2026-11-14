import pytest

events = []


@pytest.fixture
def resource():
    events.append("setup")
    yield "value"
    events.append("teardown")


def test_uses_resource(resource):
    assert resource == "value"
    assert events == ["setup"]


def test_teardown_ran_after_previous_test():
    assert events == ["setup", "teardown"]
