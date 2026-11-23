"""A wide-scope fixture whose setup raises runs once per scope, as pytest caches it.

`_pytest/fixtures.py`: `pytest_fixture_setup` stores the exception in `cached_result` and
`FixtureDef.execute` re-raises it for every later request in the same scope instance, a skip
included, without running the fixture again. The last test checks the counts.
"""

import pytest

CALLS = {"session": 0, "module": 0, "class": 0, "param": []}


@pytest.fixture(scope="session")
def unreachable_service():
    CALLS["session"] += 1
    pytest.skip("service unreachable")


@pytest.fixture(scope="module")
def broken_resource():
    CALLS["module"] += 1
    raise RuntimeError("setup failed")


@pytest.fixture(scope="module", params=["a", "b"])
def broken_per_param(request):
    CALLS["param"].append(request.param)
    raise RuntimeError(f"setup failed for {request.param}")


def test_session_1(unreachable_service):
    pass


def test_session_2(unreachable_service):
    pass


def test_module_1(broken_resource):
    pass


def test_module_2(broken_resource):
    pass


def test_per_param(broken_per_param):
    pass


class TestClassScope:
    @pytest.fixture(scope="class")
    def skipped_for_the_class(self):
        CALLS["class"] += 1
        pytest.skip("not for this class")

    def test_1(self, skipped_for_the_class):
        pass

    def test_2(self, skipped_for_the_class):
        pass


def test_zz_each_setup_ran_once_per_scope():
    assert CALLS == {"session": 1, "module": 1, "class": 1, "param": ["a", "b"]}
