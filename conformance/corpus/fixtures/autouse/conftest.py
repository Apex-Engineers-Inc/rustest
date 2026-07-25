import pytest

applied = []


@pytest.fixture(autouse=True)
def always():
    applied.append(1)
