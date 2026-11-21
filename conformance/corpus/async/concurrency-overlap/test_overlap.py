# `[tool.rustest] asyncio_concurrency = 8` (pyproject.toml) overlaps the `concurrent` tests
# below under rustest; pytest ignores the table and the mark and runs them one at a time.
# The outcomes must not differ: a module fixture set up once, function fixtures per test,
# a skip and a failure inside the overlapped run, and unmarked tests around it.
import asyncio

import pytest

SETUPS = []


@pytest.fixture(scope="module")
def shared():
    SETUPS.append("shared")
    return {"hits": []}


@pytest.fixture
def own(request):
    return [request.node.name]


def test_before():
    assert SETUPS == []


@pytest.mark.concurrent
@pytest.mark.parametrize("i", range(6))
async def test_sleeper(shared, own, i):
    await asyncio.sleep(0.05)
    shared["hits"].append(i)
    assert own == [f"test_sleeper[{i}]"]
    assert SETUPS == ["shared"]


@pytest.mark.concurrent
@pytest.mark.skip(reason="skipped inside the window")
async def test_skipped():
    raise AssertionError("never runs")


@pytest.mark.concurrent
async def test_fails(own):
    await asyncio.sleep(0.01)
    assert own == ["something else"]


@pytest.mark.concurrent
async def test_after_failure():
    await asyncio.sleep(0.02)


def test_after(shared):
    assert sorted(shared["hits"]) == list(range(6))
    assert SETUPS == ["shared"]
