"""Edge cases and special scenarios for class-based tests.

This test file covers edge cases and special scenarios:
1. Classes with setUp/tearDown-like patterns
2. Classes with __init__ methods
3. Classes with class methods and static methods
4. Mixed unittest.TestCase and plain test classes
5. Classes with inheritance
6. Classes with attributes
"""

from rustest import fixture, parametrize, skip, mark


# ============================================================================
# FIXTURES
# ============================================================================


@fixture(scope="class")
def class_fixture():
    """Simple class-scoped fixture."""
    return {"value": 42}


@fixture
def func_fixture():
    """Simple function-scoped fixture."""
    return "test"


# ============================================================================
# BASIC CLASS WITH __init__
# ============================================================================


class TestClassWithInit:
    """Test class with __init__ method."""

    def __init__(self):
        """Initialize test class."""
        self.instance_var = "initialized"

    def test_instance_var(self):
        """Test that instance variable is accessible."""
        assert hasattr(self, "instance_var")
        assert self.instance_var == "initialized"

    def test_with_fixture(self, func_fixture):
        """Test with fixture."""
        assert self.instance_var == "initialized"
        assert func_fixture == "test"


# ============================================================================
# CLASS WITH SETUP-LIKE METHODS (not setUp/tearDown)
# ============================================================================


class TestClassWithSetupPattern:
    """Test class with setup pattern (not unittest.TestCase)."""

    def setup_method(self):
        """Setup method (pytest pattern)."""
        self.setup_called = True

    def test_can_call_setup(self):
        """Test that we can manually call setup."""
        self.setup_method()
        assert self.setup_called is True

    def test_without_manual_setup(self):
        """Test behavior varies by runner."""
        # Note: pytest auto-calls setup_method, rustest does not
        # This test works with both behaviors
        if hasattr(self, "setup_called"):
            # pytest behavior - setup_method was auto-called
            assert self.setup_called is True
        else:
            # rustest behavior - setup_method not auto-called
            assert True


# ============================================================================
# CLASS WITH INSTANCE ATTRIBUTES
# ============================================================================


class TestClassWithAttributes:
    """Test class with class and instance attributes."""

    class_attribute = "class_level"

    def test_class_attribute(self):
        """Test accessing class attribute."""
        assert TestClassWithAttributes.class_attribute == "class_level"
        assert self.class_attribute == "class_level"

    def test_instance_attribute(self):
        """Test creating instance attribute."""
        self.instance_attr = "instance_level"
        assert self.instance_attr == "instance_level"

    def test_instance_isolation(self):
        """Test that instance attributes don't leak."""
        # Each test gets a fresh instance
        assert not hasattr(self, "instance_attr")


# ============================================================================
# CLASS WITH CLASS METHODS AND STATIC METHODS
# ============================================================================


class TestClassWithClassMethods:
    """Test class with class methods and static methods."""

    @classmethod
    def class_method(cls):
        """Class method."""
        return "class_method_result"

    @staticmethod
    def static_method():
        """Static method."""
        return "static_method_result"

    def test_class_method(self):
        """Test calling class method."""
        result = self.class_method()
        assert result == "class_method_result"

    def test_static_method(self):
        """Test calling static method."""
        result = self.static_method()
        assert result == "static_method_result"

    def test_with_fixture(self, func_fixture):
        """Test that fixtures work with class methods present."""
        assert func_fixture == "test"
        assert self.class_method() == "class_method_result"


# ============================================================================
# INHERITANCE (single level)
# ============================================================================


class TestBaseClass:
    """Base test class."""

    def test_in_base(self):
        """Test defined in base class."""
        assert True

    def test_base_with_fixture(self, func_fixture):
        """Test in base with fixture."""
        assert func_fixture == "test"


class TestDerivedClass(TestBaseClass):
    """Derived test class."""

    def test_in_derived(self):
        """Test defined in derived class."""
        assert True

    def test_derived_with_fixture(self, func_fixture):
        """Test in derived with fixture."""
        assert func_fixture == "test"


# ============================================================================
# CLASS WITH ONLY SKIPPED TESTS
# ============================================================================


class TestAllSkipped:
    """Test class where all tests are skipped."""

    @skip("Not implemented")
    def test_skipped_one(self):
        """First skipped test."""
        assert False  # Should not run

    @skip("Not ready")
    def test_skipped_two(self):
        """Second skipped test."""
        assert False  # Should not run


# ============================================================================
# CLASS WITH MARKED TESTS
# ============================================================================


class TestWithMarks:
    """Test class with various marks."""

    @mark.slow
    def test_slow(self):
        """Slow test."""
        assert True

    @mark.integration
    def test_integration(self):
        """Integration test."""
        assert True

    @mark.slow
    @mark.integration
    def test_multiple_marks(self):
        """Test with multiple marks."""
        assert True

    def test_no_marks(self):
        """Test without marks."""
        assert True


# ============================================================================
# CLASS WITH SINGLE TEST
# ============================================================================


class TestSingleTest:
    """Class with only one test."""

    def test_only_one(self, class_fixture):
        """Only test in this class."""
        assert class_fixture["value"] == 42


# ============================================================================
# CLASS WITH MANY TESTS
# ============================================================================


class TestManyTests:
    """Class with many tests to verify fixture reuse."""

    def test_01(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_02(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_03(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_04(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_05(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_06(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_07(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_08(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_09(self, class_fixture):
        assert class_fixture["value"] == 42

    def test_10(self, class_fixture):
        # Mutate to verify it's the same instance
        if "mutation_count" not in class_fixture:
            class_fixture["mutation_count"] = 0
        class_fixture["mutation_count"] += 1
        # All tests share the fixture, so this should be 1
        assert class_fixture["mutation_count"] == 1


# ============================================================================
# EMPTY CLASS (no test methods)
# ============================================================================


class TestNotActuallyTestClass:
    """Class that starts with Test but has no test methods."""

    def helper_method(self):
        """Not a test method."""
        return "helper"

    def another_helper(self):
        """Another non-test method."""
        return "helper2"


# ============================================================================
# CLASS WITH UNDERSCORE METHODS
# ============================================================================


class TestUnderscoreMethods:
    """Class with private/protected methods."""

    def _private_helper(self):
        """Private helper method."""
        return "private"

    def __double_underscore(self):
        """Name-mangled method."""
        return "mangled"

    def test_with_helpers(self):
        """Test that can use helper methods."""
        assert self._private_helper() == "private"

    def test_regular(self):
        """Regular test."""
        assert True


# ============================================================================
# CLASS WITH SPECIAL METHOD NAMES
# ============================================================================


class TestSpecialNames:
    """Class with special method names."""

    def test_with_numbers_123(self):
        """Test with numbers in name."""
        assert True

    def test_with_underscores_in_name(self):
        """Test with underscores."""
        assert True

    def test_CAPS(self):
        """Test with caps."""
        assert True

    def test_with_fixture_and_special_name(self, func_fixture):
        """Test combining special name and fixture."""
        assert func_fixture == "test"


# ============================================================================
# CLASS WITH PARAMETRIZE AND SKIP COMBINED
# ============================================================================


class TestParametrizeAndSkip:
    """Class combining parametrization and skip."""

    @parametrize("x", [1, 2, 3])
    def test_parametrized_only(self, x):
        """Simple parametrized test."""
        assert x > 0

    @parametrize("x", [1, 2, 3])
    @skip("Not ready")
    def test_parametrized_and_skipped(self, x):
        """Parametrized test that's skipped."""
        assert False  # Should not run


# ============================================================================
# CLASS WITH ASSERTIONS
# ============================================================================


class TestVariousAssertions:
    """Class testing various assertion patterns."""

    def test_equality(self):
        """Test equality."""
        assert 1 == 1
        assert "hello" == "hello"

    def test_inequality(self):
        """Test inequality."""
        assert 1 != 2
        assert "hello" != "world"

    def test_comparison(self):
        """Test comparisons."""
        assert 1 < 2
        assert 2 > 1
        assert 1 <= 1
        assert 1 >= 1

    def test_membership(self):
        """Test membership."""
        assert 1 in [1, 2, 3]
        assert "hello" in "hello world"

    def test_truthiness(self):
        """Test truthiness."""
        assert True
        assert not False
        assert [].__len__() == 0
        assert [1]

    def test_with_message(self):
        """Test with assertion message."""
        x = 1
        assert x == 1, f"Expected 1, got {x}"


# ============================================================================
# CLASS WITH TRY/EXCEPT
# ============================================================================


class TestExceptionHandling:
    """Class testing exception handling."""

    def test_no_exception(self):
        """Test that doesn't raise."""
        result = 1 + 1
        assert result == 2

    def test_with_try_except(self):
        """Test with try/except."""
        try:
            result = 1 / 1
            assert result == 1.0
        except ZeroDivisionError:
            assert False, "Should not get here"

    def test_expecting_exception(self):
        """Test expecting an exception."""
        try:
            _ = 1 / 0
            assert False, "Should have raised"
        except ZeroDivisionError:
            assert True  # Expected


# ============================================================================
# CLASS WITH NESTED CLASSES (edge case)
# ============================================================================


class TestOuter:
    """Outer test class."""

    def test_outer(self):
        """Test in outer class."""
        assert True

    class TestInner:
        """Inner test class (nested)."""

        def test_inner(self):
            """Test in inner class."""
            assert True


# ============================================================================
# CLASS WITHOUT DOCSTRING
# ============================================================================


class TestNoDocstring:
    def test_no_docstring_method(self):
        assert True

    def test_another_no_docstring(self):
        assert 1 == 1
