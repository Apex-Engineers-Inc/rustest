from .helpers.helper_module import create_helper


def test_relative_import_deep():
    result = create_helper()
    assert result == {"status": "created"}
