import pytest

SETUPS = []


@pytest.fixture(scope="module")
def module_state():
    SETUPS.append("module")
    yield {"module": len(SETUPS)}


@pytest.fixture(scope="class")
def class_state(module_state):
    return {"class": module_state["module"]}


def test_0(module_state):
    assert module_state["module"] >= 1


def test_1(module_state):
    assert SETUPS


def test_2(module_state):
    assert module_state["module"] == len(SETUPS)


class TestGroup:
    def test_3(self, class_state):
        assert class_state["class"] >= 1

    def test_4(self, class_state, module_state):
        assert class_state["class"] == module_state["module"]

    @pytest.mark.xfail(reason="expected")
    def test_5(self, class_state):
        assert False


def test_6(module_state):
    assert module_state["module"] == len(SETUPS)


@pytest.mark.skip(reason="not here")
def test_7():
    pass


def test_8(module_state):
    assert module_state["module"] >= 1
