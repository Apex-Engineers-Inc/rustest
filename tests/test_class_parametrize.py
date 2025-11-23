"""Test class-level @parametrize decorator.

This tests that @parametrize applied to a class correctly expands
to create parametrized versions of all test methods in the class.
"""

from rustest import parametrize


@parametrize("x", [1, 2, 3])
class TestClassLevelParametrize:
    """Test class with class-level parametrize."""

    def test_method_one(self, x):
        """First test method."""
        assert x in [1, 2, 3]
        assert x > 0

    def test_method_two(self, x):
        """Second test method."""
        assert x < 10


@parametrize("config", ["dev", "prod"])
class TestMultipleMethods:
    """Test class with multiple methods."""

    def test_setup(self, config):
        """Test setup with config."""
        assert config in ["dev", "prod"]

    def test_processing(self, config):
        """Test processing with config."""
        assert isinstance(config, str)

    def test_teardown(self, config):
        """Test teardown with config."""
        assert len(config) > 0


@parametrize("x", [1, 2])
class TestClassAndMethodParametrize:
    """Test class with both class and method level parametrize."""

    @parametrize("y", [10, 20])
    def test_combined(self, x, y):
        """Test with both class and method parameters."""
        # Should create 4 test cases: (1,10), (1,20), (2,10), (2,20)
        assert x in [1, 2]
        assert y in [10, 20]

    def test_class_only(self, x):
        """Test with only class parameter."""
        # Should create 2 test cases: (1,), (2,)
        assert x in [1, 2]
