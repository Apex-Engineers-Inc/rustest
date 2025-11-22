"""Test indirect parametrization support."""
from rustest import fixture, parametrize


@fixture
def data_1():
    """First fixture providing data."""
    return {"name": "fixture_1", "value": 42}


@fixture
def data_2():
    """Second fixture providing data."""
    return {"name": "fixture_2", "value": 100}


@fixture
def data_3():
    """Third fixture providing data."""
    return {"name": "fixture_3", "value": 999}


# Test with indirect as a list of strings (preferred way)
@parametrize("fixture_name, expected_value", [
    ("data_1", 42),
    ("data_2", 100),
    ("data_3", 999),
], indirect=["fixture_name"])
def test_indirect_as_list(fixture_name, expected_value):
    """Test indirect parametrization with list of parameter names."""
    assert fixture_name["value"] == expected_value
    assert "name" in fixture_name


# Test with indirect=True (all parameters are indirect)
@parametrize("data", ["data_1", "data_2", "data_3"], indirect=True)
def test_indirect_true(data):
    """Test indirect parametrization with indirect=True."""
    assert "name" in data
    assert "value" in data
    assert data["value"] in [42, 100, 999]


# Test with indirect as a single string
@parametrize("fixture_ref, direct_value", [
    ("data_1", "first"),
    ("data_2", "second"),
], indirect="fixture_ref")
def test_indirect_single_string(fixture_ref, direct_value):
    """Test indirect parametrization with single parameter name."""
    assert "value" in fixture_ref
    assert direct_value in ["first", "second"]


# Test mixed indirect and direct parameters
@parametrize("data_fixture, multiplier", [
    ("data_1", 2),
    ("data_2", 3),
], indirect=["data_fixture"])
def test_mixed_indirect_direct(data_fixture, multiplier):
    """Test mixing indirect fixture references with direct values."""
    result = data_fixture["value"] * multiplier
    if data_fixture["name"] == "fixture_1":
        assert result == 84  # 42 * 2
    elif data_fixture["name"] == "fixture_2":
        assert result == 300  # 100 * 3
