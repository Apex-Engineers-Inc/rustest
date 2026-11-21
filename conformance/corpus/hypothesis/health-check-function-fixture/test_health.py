"""hypothesis's pytest plugin fails a `@given` test that asks for a function-scoped fixture
(`HealthCheck.function_scoped_fixture`) unless the check is suppressed; wider scopes pass."""

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st


@pytest.fixture(scope="module")
def shared():
    return 1


@given(st.integers())
def test_function_scoped_fixture_fails(tmp_path, x):
    pass


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.integers())
def test_suppressed_passes(tmp_path, x):
    pass


@given(st.integers())
def test_module_scoped_fixture_passes(shared, x):
    pass


class TestMethods:
    @given(st.integers())
    def test_method_with_function_fixture_fails(self, monkeypatch, x):
        pass
