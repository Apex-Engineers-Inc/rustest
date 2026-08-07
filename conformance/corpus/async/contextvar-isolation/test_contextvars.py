import contextvars

import pytest

LEAKED = contextvars.ContextVar("LEAKED", default="unset")
FROM_FIXTURE = contextvars.ContextVar("FROM_FIXTURE", default="unset")


@pytest.fixture(scope="session")
async def setter():
    FROM_FIXTURE.set("from-fixture")
    return 1


async def test_a_sets_a_contextvar():
    LEAKED.set("from-test-a")
    assert LEAKED.get() == "from-test-a"


async def test_b_does_not_see_it():
    # Runs on the SAME session loop as test_a, and must still not see its write.
    assert LEAKED.get() == "unset", LEAKED.get()


async def test_c_sees_a_fixtures_write(setter):
    # ...while the other direction still works: a session fixture's ContextVar is
    # visible to the tests it serves.
    assert setter == 1
    assert FROM_FIXTURE.get() == "from-fixture", FROM_FIXTURE.get()
