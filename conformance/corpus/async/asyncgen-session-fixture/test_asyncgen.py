import asyncio

import pytest

EVENTS = []


@pytest.fixture(scope="session")
async def resource():
    EVENTS.append(("setup", id(asyncio.get_running_loop())))
    yield "live"
    EVENTS.append(("teardown", id(asyncio.get_running_loop())))


@pytest.fixture(scope="session")
async def dependent(resource):
    EVENTS.append(("dependent-setup", id(asyncio.get_running_loop())))
    yield resource.upper()
    EVENTS.append(("dependent-teardown", id(asyncio.get_running_loop())))


async def test_setup_ran_on_the_session_loop(dependent):
    assert dependent == "LIVE"
    assert [name for name, _loop in EVENTS] == ["setup", "dependent-setup"]
    assert {loop for _name, loop in EVENTS} == {id(asyncio.get_running_loop())}


async def test_the_fixture_is_not_torn_down_between_tests(dependent):
    assert dependent == "LIVE"
    assert len(EVENTS) == 2, EVENTS
