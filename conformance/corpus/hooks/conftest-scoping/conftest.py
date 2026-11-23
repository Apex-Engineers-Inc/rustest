def pytest_runtest_setup(item):
    if item.get_closest_marker("skip_at_root"):
        import pytest

        pytest.skip("root conftest")
