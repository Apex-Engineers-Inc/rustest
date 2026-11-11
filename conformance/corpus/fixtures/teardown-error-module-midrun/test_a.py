"""A module-scoped fixture whose teardown raises when the run leaves this module: the error
is this module's last test's (pytest's `nextitem` teardown), never the next module's test."""

import pytest


@pytest.fixture(scope="module")
def resource():
    yield
    raise RuntimeError("module teardown")


def test_one(resource):
    pass


def test_two(resource):
    pass
