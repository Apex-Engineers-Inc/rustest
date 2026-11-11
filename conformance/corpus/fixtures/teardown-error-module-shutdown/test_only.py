"""The only module's fixture raises in teardown at the end of the run."""

import pytest


@pytest.fixture(scope="module")
def resource():
    yield
    raise RuntimeError("module teardown at the end")


def test_one(resource):
    pass


def test_two():
    pass
