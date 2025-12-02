"""Root level conftest.py."""
import asyncio
import os

import rustest as testlib

AUTOUSE_ENV_VAR = "RUSTEST_PARENT_AUTOUSE_LAST_TEST"
ASYNC_AUTOUSE_ENV_VAR = "RUSTEST_PARENT_ASYNC_AUTOUSE_LAST_TEST"
# Ensure we start from a clean state each time the module is imported
os.environ.pop(AUTOUSE_ENV_VAR, None)
os.environ.pop(ASYNC_AUTOUSE_ENV_VAR, None)


@testlib.fixture
def root_fixture():
    """Fixture from root conftest.py."""
    return "root"


@testlib.fixture
def overridable_fixture():
    """Fixture that will be overridden by child conftest."""
    return "from_root"


@testlib.fixture
def root_only():
    """Fixture only in root conftest."""
    return "root_only_value"


@testlib.fixture(scope="session")
def nested_session_fixture():
    """Session-scoped fixture from nested test conftest."""
    return "session_from_root"


@testlib.fixture(autouse=True)
def root_autouse_marker(request):
    """Autouse fixture used to verify parent conftest loading in nested dirs."""
    os.environ[AUTOUSE_ENV_VAR] = request.node.name


if os.environ.get("RUSTEST_RUNNING") == "1":

    @testlib.fixture(autouse=True)
    async def root_async_autouse_marker(request):
        """Async autouse fixture to ensure async variants propagate across dirs."""
        await asyncio.sleep(0)
        os.environ[ASYNC_AUTOUSE_ENV_VAR] = request.node.name
