"""`pytestmark = pytest.mark.asyncio` -- one such module used to stop a whole run."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_async_body_runs():
    assert True


async def test_async_body_can_fail():
    assert 1 == 2
