"""A **module-scoped parametrized** fixture requested by two tests.

pytest's ``reorder_items`` groups items by the higher-scoped parameter before the run, so
the fixture is set up once per param value (2 setups) and the collected order is grouped
by flavour. A runner that keeps source order sets it up once per test (4 setups) and
emits the tests grouped by *function*.
"""

import pytest

setups = []


@pytest.fixture(scope="module", params=["one", "two"])
def flavour(request):
    setups.append(request.param)
    return request.param


def test_alpha(flavour):
    assert flavour in ("one", "two")


def test_beta(flavour):
    assert flavour in ("one", "two")
