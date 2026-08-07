import asyncio

import _loops


async def test_first():
    _loops.SEEN.append(id(asyncio.get_running_loop()))
    assert len(_loops.SEEN) == 1


async def test_second():
    _loops.SEEN.append(id(asyncio.get_running_loop()))
    # Two tests in one file, one session loop.
    assert len(set(_loops.SEEN)) == 1, _loops.SEEN
