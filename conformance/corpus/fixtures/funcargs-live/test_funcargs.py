"""``request.node.funcargs`` holds the closure's values as they are set up -- autouse and
transitive fixtures included -- and is the same live dict a later fixture's teardown reads."""

import pytest

SEEN: dict[str, list[str]] = {}


@pytest.fixture(autouse=True)
def first():
    return "first"


@pytest.fixture
def a():
    return 1


@pytest.fixture
def b(a, request):
    SEEN["b-setup"] = sorted(request.node.funcargs)
    yield 2
    SEEN["b-teardown"] = sorted(request.node.funcargs)


def test_uses_b(b):
    assert b == 2


def test_zz_saw_the_closure():
    assert "a" in SEEN["b-setup"] and "first" in SEEN["b-setup"]
    assert "b" in SEEN["b-teardown"]
