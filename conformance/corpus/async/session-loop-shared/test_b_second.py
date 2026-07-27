import asyncio

import _loops


async def test_third():
    _loops.SEEN.append(id(asyncio.get_running_loop()))
    # Only true if the session loop spans the RUN rather than one file.
    assert len(set(_loops.SEEN)) == 1, _loops.SEEN
    assert len(_loops.SEEN) == 3, _loops.SEEN
