import pytest


class Base:
    async def test_inherited(self):
        assert await self._helper(1, 2) == 3


@pytest.mark.asyncio(loop_scope="session")
class TestMarked(Base):
    @staticmethod
    async def _helper(a, b):
        return a + b

    @classmethod
    async def _chelper(cls, a):
        return (cls.__name__, a)

    @staticmethod
    def _sync(a):
        return a

    async def test_static(self):
        assert await self._helper(1, 2) == 3

    async def test_class(self):
        assert await self._chelper(1) == ("TestMarked", 1)

    def test_sync_static(self):
        assert self._sync(5) == 5
