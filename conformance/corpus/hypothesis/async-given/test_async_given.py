import asyncio

import pytest
from hypothesis import given, settings, strategies as st


@pytest.mark.asyncio
@settings(max_examples=5)
@given(st.integers())
async def test_async_given(x):
    await asyncio.sleep(0)
    assert isinstance(x, int)


@pytest.mark.asyncio
@settings(max_examples=5, database=None)
@given(st.integers())
async def test_async_given_fails(x):
    assert x < 100
