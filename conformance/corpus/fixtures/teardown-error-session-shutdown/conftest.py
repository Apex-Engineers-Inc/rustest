import pytest


@pytest.fixture(scope="session")
def session_resource():
    yield
    raise RuntimeError("session teardown")
