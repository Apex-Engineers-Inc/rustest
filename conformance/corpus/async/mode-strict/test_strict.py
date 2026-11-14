import inspect

import pytest
import pytest_asyncio


@pytest.fixture
async def unmarked_fixture():
    return 7


@pytest_asyncio.fixture
async def marked_fixture():
    return 9


def test_unmarked_async_fixture_is_not_awaited(unmarked_fixture):
    assert inspect.iscoroutine(unmarked_fixture), type(unmarked_fixture)


@pytest.mark.asyncio
async def test_marked_fixture_is_awaited(marked_fixture):
    assert marked_fixture == 9


@pytest.mark.asyncio
async def test_marked_async_runs():
    assert True


async def test_unmarked_async_fails():
    assert True


async def test_unmarked_asyncgen_fails():
    assert True
    yield
