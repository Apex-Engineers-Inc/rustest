"""Test class variables and class-scoped fixture methods within test classes."""

from rustest import fixture


# ============================================================================
# TEST 1: CLASS VARIABLES
# ============================================================================


class TestClassVariables:
    """Test that methods can access class variables."""

    class_variable = "shared_data"
    class_counter = 0

    def test_access_class_variable(self):
        """Test accessing class variable."""
        assert self.class_variable == "shared_data"
        assert TestClassVariables.class_variable == "shared_data"

    def test_modify_class_variable(self):
        """Test modifying class variable."""
        # Modify via class name
        initial = TestClassVariables.class_counter
        TestClassVariables.class_counter += 1
        assert TestClassVariables.class_counter == initial + 1

    def test_class_variable_persists(self):
        """Test that class variable modifications persist."""
        # Class variables are shared across instances
        # Test can run in any order, so just verify we can modify it
        TestClassVariables.class_counter += 1
        assert TestClassVariables.class_counter >= 1

    def test_instance_variable(self):
        """Test creating instance variable."""
        self.instance_var = "instance_data"
        assert self.instance_var == "instance_data"

    def test_instance_variable_isolation(self):
        """Test that instance variables don't persist."""
        # Each test gets a fresh instance
        assert not hasattr(self, "instance_var")


# ============================================================================
# TEST 2: CLASS-SCOPED FIXTURE METHOD WITHIN CLASS
# ============================================================================


class TestClassFixtureMethod:
    """Test class with fixture method defined in the class."""

    @fixture(scope="class")
    def class_setup(self):
        """Class-scoped fixture method."""
        data = {"setup": True, "value": 42}
        yield data
        # Teardown
        data["teardown"] = True

    def test_uses_class_fixture(self, class_setup):
        """Test using class-scoped fixture method."""
        assert class_setup["setup"] is True
        assert class_setup["value"] == 42

    def test_reuses_class_fixture(self, class_setup):
        """Test that class fixture is reused."""
        # Should be the same instance
        assert class_setup["setup"] is True
        assert class_setup["value"] == 42


# ============================================================================
# TEST 3: FIXTURE METHOD WITH DEPENDENCIES
# ============================================================================


@fixture(scope="session")
def session_data():
    """Session-scoped fixture."""
    return {"session": True}


class TestClassFixtureMethodWithDeps:
    """Test class fixture method with dependencies."""

    @fixture(scope="class")
    def class_resource(self, session_data):
        """Class-scoped fixture method with dependency."""
        resource = {"session": session_data, "class_level": True}
        yield resource
        # Teardown

    def test_uses_fixture_with_deps(self, class_resource):
        """Test using fixture method with dependencies."""
        assert class_resource["class_level"] is True
        assert class_resource["session"]["session"] is True


# ============================================================================
# TEST 4: MULTIPLE FIXTURE METHODS IN CLASS
# ============================================================================


class TestMultipleFixtureMethods:
    """Test class with multiple fixture methods."""

    @fixture(scope="class")
    def fixture_a(self):
        """First class fixture method."""
        yield {"name": "fixture_a"}

    @fixture(scope="class")
    def fixture_b(self):
        """Second class fixture method."""
        yield {"name": "fixture_b"}

    @fixture
    def function_fixture(self, fixture_a):
        """Function fixture depending on class fixture method."""
        return {"func": True, "a": fixture_a}

    def test_uses_fixture_a(self, fixture_a):
        """Test using fixture_a."""
        assert fixture_a["name"] == "fixture_a"

    def test_uses_fixture_b(self, fixture_b):
        """Test using fixture_b."""
        assert fixture_b["name"] == "fixture_b"

    def test_uses_both_fixtures(self, fixture_a, fixture_b):
        """Test using both class fixtures."""
        assert fixture_a["name"] == "fixture_a"
        assert fixture_b["name"] == "fixture_b"

    def test_uses_function_fixture(self, function_fixture):
        """Test using function fixture that depends on class fixture."""
        assert function_fixture["func"] is True
        assert function_fixture["a"]["name"] == "fixture_a"


# ============================================================================
# TEST 5: CLASS VARIABLES WITH CLASS FIXTURES
# ============================================================================


class TestClassVarsWithFixtures:
    """Test combining class variables and class fixture methods."""

    shared_data = []

    @fixture(scope="class")
    def class_fixture(self):
        """Class fixture that can access class variables."""
        # Can access class variables
        self.shared_data.append("from_fixture")
        yield {"shared": self.shared_data}

    def test_fixture_modifies_class_var(self, class_fixture):
        """Test that fixture modified class variable."""
        assert "from_fixture" in TestClassVarsWithFixtures.shared_data
        assert "from_fixture" in class_fixture["shared"]

    def test_class_var_persists(self, class_fixture):
        """Test class variable still has the modification."""
        assert "from_fixture" in TestClassVarsWithFixtures.shared_data
