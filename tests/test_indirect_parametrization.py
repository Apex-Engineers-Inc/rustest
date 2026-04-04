"""Test indirect parametrization support.

Indirect parametrize passes param values to fixtures via request.param,
rather than using them as direct test arguments.
"""
from rustest import fixture, parametrize
from rustest.compat.pytest import FixtureRequest


DATA_REGISTRY = {
    "data_1": {"name": "fixture_1", "value": 42},
    "data_2": {"name": "fixture_2", "value": 100},
    "data_3": {"name": "fixture_3", "value": 999},
}


@fixture
def fixture_name(request: FixtureRequest):
    """Fixture that looks up data by request.param key."""
    return DATA_REGISTRY[request.param]


@fixture
def data(request: FixtureRequest):
    """Fixture that looks up data by request.param key."""
    return DATA_REGISTRY[request.param]


@fixture
def fixture_ref(request: FixtureRequest):
    """Fixture that looks up data by request.param key."""
    return DATA_REGISTRY[request.param]


@fixture
def data_fixture(request: FixtureRequest):
    """Fixture that looks up data by request.param key."""
    return DATA_REGISTRY[request.param]


# Test with indirect as a list of strings (preferred way)
@parametrize(
    "fixture_name, expected_value",
    [
        ("data_1", 42),
        ("data_2", 100),
        ("data_3", 999),
    ],
    indirect=["fixture_name"],
)
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
@parametrize(
    "fixture_ref, direct_value",
    [
        ("data_1", "first"),
        ("data_2", "second"),
    ],
    indirect=["fixture_ref"],
)
def test_indirect_single_string(fixture_ref, direct_value):
    """Test indirect parametrization with single parameter name."""
    assert "value" in fixture_ref
    assert direct_value in ["first", "second"]


# Test mixed indirect and direct parameters
@parametrize(
    "data_fixture, multiplier",
    [
        ("data_1", 2),
        ("data_2", 3),
    ],
    indirect=["data_fixture"],
)
def test_mixed_indirect_direct(data_fixture, multiplier):
    """Test mixing indirect fixture references with direct values."""
    result = data_fixture["value"] * multiplier
    if data_fixture["name"] == "fixture_1":
        assert result == 84  # 42 * 2
    elif data_fixture["name"] == "fixture_2":
        assert result == 300  # 100 * 3


@fixture
def executor_style(request: FixtureRequest):
    """Fixture that uses request.param to decide behavior."""
    if request.param == "fast":
        return {"mode": "fast", "value": 10}
    elif request.param == "slow":
        return {"mode": "slow", "value": 100}
    return {"mode": "unknown", "value": 0}


@parametrize("executor_style", ["fast", "slow"], indirect=True)
def test_indirect_with_request_param(executor_style):
    """Indirect parametrize should pass values via request.param, not as fixture names."""
    assert executor_style["mode"] in ("fast", "slow")
    if executor_style["mode"] == "fast":
        assert executor_style["value"] == 10
    else:
        assert executor_style["value"] == 100


@fixture
def model_resolver(request: FixtureRequest):
    """Fixture that resolves another fixture by name via request.param."""
    name = request.param
    # Simulate dynamic fixture resolution
    return {"resolved_from": name, "value": f"resolved_{name}"}


@parametrize(
    "direct_val,model_resolver",
    [("x", "alpha"), ("y", "beta")],
    indirect=["model_resolver"],
)
class TestClassLevelIndirect:
    """Class-level parametrize with partial indirect."""

    def test_direct_value(self, direct_val, model_resolver):
        assert direct_val in ("x", "y")
        assert isinstance(model_resolver, dict)
        assert model_resolver["resolved_from"] in ("alpha", "beta")

    def test_resolved_correctly(self, direct_val, model_resolver):
        if direct_val == "x":
            assert model_resolver["resolved_from"] == "alpha"
        else:
            assert model_resolver["resolved_from"] == "beta"
