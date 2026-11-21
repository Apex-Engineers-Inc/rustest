"""A fixture hook reading request.node of a module-scoped fixture: pytest answers with
the Module node, which rustest does not build, so the session goes to pytest."""

import pytest


def pytest_fixture_setup(fixturedef, request):
    if fixturedef.scope == "module":
        request.node.name


@pytest.fixture(scope="module")
def shared():
    return 1
