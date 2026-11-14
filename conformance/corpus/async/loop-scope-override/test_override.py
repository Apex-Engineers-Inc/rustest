import asyncio

import pytest

# The loop **objects**, not their ids.
#
# `id()` is only unique among live objects, and these loops are not all live at once: the
# class-scoped loop is closed when the class ends, and CPython promptly reused its address
# for the next function-scoped loop -- measured under pytest, where
# `test_default_is_function_scoped` failed with `1393277542608 not in {1393277542608, ...}`
# against a loop that had already been torn down. Holding the objects keeps every address
# distinct for the length of the run and makes the comparison say what it means.
LOOPS = {}


@pytest.mark.asyncio(loop_scope="module")
async def test_module_one():
    LOOPS["module_one"] = asyncio.get_running_loop()


@pytest.mark.asyncio(loop_scope="module")
async def test_module_two():
    assert asyncio.get_running_loop() is LOOPS["module_one"]


class TestClassLoop:
    @pytest.mark.asyncio(loop_scope="class")
    async def test_class_one(self):
        LOOPS["class_one"] = asyncio.get_running_loop()
        assert LOOPS["class_one"] is not LOOPS["module_one"]

    @pytest.mark.asyncio(loop_scope="class")
    async def test_class_two(self):
        assert asyncio.get_running_loop() is LOOPS["class_one"]


async def test_default_is_function_scoped():
    assert asyncio.get_running_loop() not in LOOPS.values()
