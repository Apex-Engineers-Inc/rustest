# `[tool.rustest] asyncio_concurrency = 8` (pyproject.toml): `concurrent` tests overlap only
# within one class and one value of a parametrized module fixture, so class- and module-scoped
# fixtures set up and tear down exactly where pytest's do. pytest ignores the table and the
# mark; the last test checks the order of every setup and teardown.
import asyncio

import pytest

EVENTS = []


@pytest.fixture(scope="class")
def per_class(request):
    EVENTS.append(f"setup {request.cls.__name__}")
    yield
    EVENTS.append(f"teardown {request.cls.__name__}")


@pytest.fixture(scope="module", params=["m1", "m2"])
def wide(request):
    EVENTS.append(f"setup {request.param}")
    yield request.param
    EVENTS.append(f"teardown {request.param}")


@pytest.fixture(scope="class")
def module_level_class_scope():
    EVENTS.append("setup function-like")
    yield
    EVENTS.append("teardown function-like")


@pytest.mark.concurrent
class TestA:
    async def test_a0(self, per_class):
        await asyncio.sleep(0.03)

    async def test_a1(self, per_class):
        await asyncio.sleep(0.03)


@pytest.mark.concurrent
class TestB:
    async def test_b0(self, per_class):
        await asyncio.sleep(0.03)

    async def test_b1(self, per_class):
        await asyncio.sleep(0.03)


@pytest.mark.concurrent
async def test_function_like_0(module_level_class_scope):
    await asyncio.sleep(0.03)


@pytest.mark.concurrent
async def test_function_like_1(module_level_class_scope):
    await asyncio.sleep(0.03)


# One function, so the ids group by the module param under both runners.
@pytest.mark.concurrent
@pytest.mark.parametrize("i", range(2))
async def test_w(wide, i):
    await asyncio.sleep(0.03)


def test_zz_events():
    # A class-scoped fixture of a module-level test lives and dies with that test, so two such
    # tests that overlap interleave its setups and teardowns: only the counts are pytest's.
    assert EVENTS.count("setup function-like") == EVENTS.count("teardown function-like") == 2
    assert [event for event in EVENTS if "function-like" not in event] == [
        "setup TestA",
        "teardown TestA",
        "setup TestB",
        "teardown TestB",
        "setup m1",
        "teardown m1",
        "setup m2",
    ]
