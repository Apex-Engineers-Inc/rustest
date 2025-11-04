from rustest import fixture


@fixture
def message() -> str:
    return "hello"


def test_receives_fixture(message: str) -> None:
    assert message == "hello"
