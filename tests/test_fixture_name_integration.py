"""Integration test for fixture name parameter."""
from rustest import fixture


@fixture(name="renamed_fixture")
def original_function_name():
    """This fixture should be accessible as 'renamed_fixture', not 'original_function_name'."""
    return "success"


def test_can_use_renamed_fixture(renamed_fixture):
    """Test that we can use the fixture by its renamed name."""
    assert renamed_fixture == "success"
