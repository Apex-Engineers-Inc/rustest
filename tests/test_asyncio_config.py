"""Test that asyncio config from pyproject.toml is respected."""

from rustest import fixture


@fixture(scope="session")
async def shared_async_resource():
    return {"initialized": True}


async def test_async_without_explicit_session_dep():
    """Test that even without session fixture deps, config controls loop scope."""
    # This test has no session fixture dependencies
    # But if asyncio_default_test_loop_scope = "session" in pyproject.toml,
    # it should use the session loop
    import asyncio

    loop = asyncio.get_event_loop()
    assert not loop.is_closed()
