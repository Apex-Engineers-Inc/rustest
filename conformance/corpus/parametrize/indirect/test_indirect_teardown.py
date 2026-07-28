"""An indirect fixture is set up and torn down once per parameter, in order.

The value rides on the same `fixture_params` mapping a `@fixture(params=...)` value uses,
so `_resolve_active`'s per-parameter cache key gives teardown-per-parameter for free.
"""

import pytest

EVENTS = []


@pytest.fixture
def resource(request):
    EVENTS.append(("setup", request.param))
    yield request.param
    EVENTS.append(("teardown", request.param))


@pytest.mark.parametrize("resource", ["r1", "r2"], indirect=True)
def test_uses(resource):
    assert resource in ("r1", "r2")


def test_z_events_are_interleaved():
    # Function scope, so r1 is torn down before r2 is built -- never both alive at once.
    assert EVENTS == [
        ("setup", "r1"),
        ("teardown", "r1"),
        ("setup", "r2"),
        ("teardown", "r2"),
    ]
