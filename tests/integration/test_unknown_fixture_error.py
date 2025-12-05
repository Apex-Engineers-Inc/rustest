"""
Integration tests for unknown fixture error messages.

These tests verify that rustest provides helpful error messages when a test
requests an unknown fixture, including a list of available fixtures.

NOTE: These tests use pytest fixtures and subprocess to test rustest externally.
They require pytest to run and are skipped when run with rustest.
"""

import os
import subprocess
import sys

# Skip this module when running under rustest (not pytest)
if os.environ.get("RUSTEST_RUNNING") == "1":
    # Running under rustest - don't define any test functions
    pass
else:
    import pytest

    def _run_rustest(project_dir, *args):
        """Run rustest on a project directory and return result."""
        # Use the current Python interpreter which has rustest installed
        python_path = sys.executable
        cmd = [
            python_path,
            "-m",
            "rustest",
            str(project_dir),
            "--color",
            "never",
            *args,
        ]
        result = subprocess.run(cmd, cwd=project_dir.parent, capture_output=True, text=True)
        return result

    @pytest.fixture
    def basic_unknown_fixture_project(tmp_path):
        """Create a project with a test requesting an unknown fixture."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "test_unknown.py").write_text("""
from rustest import fixture

@fixture
def my_fixture():
    return "hello"

@fixture
def another_fixture():
    return "world"

def test_with_unknown(nonexistent_fixture):
    pass
""")
        return tests_dir

    @pytest.fixture
    def conftest_fixture_project(tmp_path):
        """Create a project with fixtures in conftest.py."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "conftest.py").write_text("""
from rustest import fixture

@fixture
def conftest_fixture():
    return "from conftest"

@fixture(name="aliased")
def _aliased_fixture():
    return "aliased fixture"

@fixture
def zebra_fixture():
    return "last alphabetically"

@fixture
def alpha_fixture():
    return "first alphabetically"
""")

        (tests_dir / "test_conftest.py").write_text("""
def test_with_unknown(unknown_fixture):
    pass
""")
        return tests_dir

    @pytest.fixture
    def nested_conftest_project(tmp_path):
        """Create a project with nested conftest.py files."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        subdir = tests_dir / "subdir"
        subdir.mkdir()

        (tests_dir / "conftest.py").write_text("""
from rustest import fixture

@fixture
def root_fixture():
    return "from root"
""")

        (subdir / "conftest.py").write_text("""
from rustest import fixture

@fixture
def child_fixture():
    return "from child"
""")

        (subdir / "test_nested.py").write_text("""
def test_with_unknown(missing_fixture):
    pass
""")
        return subdir

    @pytest.fixture
    def typo_fixture_project(tmp_path):
        """Create a project simulating a common fixture name typo."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "test_typo.py").write_text("""
from rustest import fixture

@fixture
def database_connection():
    return {"connected": True}

def test_with_typo(databse_connection):  # Typo: databse instead of database
    pass
""")
        return tests_dir

    def test_unknown_fixture_shows_available_fixtures(basic_unknown_fixture_project):
        """Test that unknown fixture error lists available fixtures."""
        result = _run_rustest(basic_unknown_fixture_project)

        assert result.returncode != 0, f"Expected failure: {result.stderr}"

        output = result.stdout + result.stderr

        # Should mention the unknown fixture name
        assert "Unknown fixture" in output, f"Expected 'Unknown fixture' in output: {output}"
        assert "nonexistent_fixture" in output, f"Expected fixture name in output: {output}"

        # Should list available fixtures
        assert "Available fixtures:" in output, (
            f"Expected 'Available fixtures:' in output: {output}"
        )

        # Should include user-defined fixtures
        assert "my_fixture" in output, f"Expected 'my_fixture' in available: {output}"
        assert "another_fixture" in output, f"Expected 'another_fixture' in available: {output}"

        # Should include built-in fixtures
        assert "tmp_path" in output, f"Expected built-in 'tmp_path' in available: {output}"
        assert "monkeypatch" in output, f"Expected built-in 'monkeypatch' in available: {output}"
        assert "capsys" in output, f"Expected built-in 'capsys' in available: {output}"

    def test_unknown_fixture_includes_conftest_fixtures(conftest_fixture_project):
        """Test that available fixtures include those from conftest.py."""
        result = _run_rustest(conftest_fixture_project)

        assert result.returncode != 0, f"Expected failure: {result.stderr}"

        output = result.stdout + result.stderr

        # Should list conftest fixtures
        assert "conftest_fixture" in output, f"Expected conftest fixture in available: {output}"

        # Should include the aliased fixture by its alias name
        assert "aliased" in output, f"Expected aliased fixture 'aliased' in available: {output}"

    def test_unknown_fixture_fixtures_are_sorted(conftest_fixture_project):
        """Test that available fixtures are sorted alphabetically."""
        result = _run_rustest(conftest_fixture_project)

        assert result.returncode != 0, f"Expected failure: {result.stderr}"

        output = result.stdout + result.stderr

        # Find the "Available fixtures:" line and check ordering
        assert "Available fixtures:" in output

        # alpha_fixture should appear before zebra_fixture
        alpha_pos = output.find("alpha_fixture")
        zebra_pos = output.find("zebra_fixture")

        assert alpha_pos != -1, f"Expected alpha_fixture in output: {output}"
        assert zebra_pos != -1, f"Expected zebra_fixture in output: {output}"
        assert alpha_pos < zebra_pos, (
            f"Expected alpha_fixture before zebra_fixture (alphabetical order): {output}"
        )

    def test_unknown_fixture_includes_nested_conftest(nested_conftest_project):
        """Test that available fixtures include those from parent conftest.py files."""
        result = _run_rustest(nested_conftest_project)

        assert result.returncode != 0, f"Expected failure: {result.stderr}"

        output = result.stdout + result.stderr

        # Should include fixture from child conftest
        assert "child_fixture" in output, f"Expected child conftest fixture: {output}"

        # Should include fixture from parent conftest
        assert "root_fixture" in output, f"Expected root conftest fixture: {output}"

    def test_unknown_fixture_helps_identify_typos(typo_fixture_project):
        """Test that the fixture list helps users identify typos."""
        result = _run_rustest(typo_fixture_project)

        assert result.returncode != 0, f"Expected failure: {result.stderr}"

        output = result.stdout + result.stderr

        # Should show the typo'd name in the error
        assert "databse_connection" in output, f"Expected typo'd name in error: {output}"

        # Should show the correct fixture name in the available list
        assert "database_connection" in output, (
            f"Expected correct fixture name in available list: {output}"
        )

    def test_unknown_fixture_includes_request_fixture(basic_unknown_fixture_project):
        """Test that the special 'request' fixture is included."""
        result = _run_rustest(basic_unknown_fixture_project)

        assert result.returncode != 0, f"Expected failure: {result.stderr}"

        output = result.stdout + result.stderr

        # The 'request' fixture should be available
        assert "request" in output, f"Expected 'request' fixture in available: {output}"

    def test_unknown_fixture_error_format(basic_unknown_fixture_project):
        """Test the overall format of the error message."""
        result = _run_rustest(basic_unknown_fixture_project)

        assert result.returncode != 0, f"Expected failure: {result.stderr}"

        output = result.stdout + result.stderr

        # Error should be a ValueError
        assert "ValueError" in output, f"Expected ValueError in output: {output}"

        # Should have the standard error format
        assert "Unknown fixture 'nonexistent_fixture'" in output, (
            f"Expected standard error format: {output}"
        )
