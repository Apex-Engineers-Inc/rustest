import pytest_asyncio


@pytest_asyncio.fixture(scope="session", loop_scope="function")
async def wide():
    yield "live"


async def test_one(wide):
    assert wide == "live"


async def test_two(wide):
    assert wide == "live"
