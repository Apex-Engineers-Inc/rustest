from .helpers import create_helper


def test_relative_import():
    result = create_helper()
    assert result == {"status": "created"}
