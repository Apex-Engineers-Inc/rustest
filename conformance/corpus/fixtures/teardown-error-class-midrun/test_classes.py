"""A class-scoped fixture whose teardown raises when the run leaves the class: the class's
last test gets the error, the next test runs and passes."""

import pytest


@pytest.fixture(scope="class")
def per_class():
    yield
    raise RuntimeError("class teardown")


class TestFirst:
    def test_one(self, per_class):
        pass

    def test_two(self, per_class):
        pass


class TestSecond:
    def test_three(self):
        pass
