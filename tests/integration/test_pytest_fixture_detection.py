"""
Integration tests for pytest fixture detection warnings.

These tests verify that rustest detects @pytest.fixture definitions in conftest.py
files and emits a helpful warning suggesting --pytest-compat mode.

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
    def pytest_fixture_conftest_project(tmp_path):
        """Create a project with @pytest.fixture definitions in conftest.py."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "conftest.py").write_text("""
import pytest

@pytest.fixture
def db_session():
    return {"connected": True}

@pytest.fixture
def api_client():
    return {"url": "http://localhost"}
""")

        (tests_dir / "test_example.py").write_text("""
def test_simple():
    assert 1 + 1 == 2
""")
        return tests_dir

    @pytest.fixture
    def mixed_conftest_project(tmp_path):
        """Create a project with both @pytest.fixture and @rustest.fixture."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "conftest.py").write_text("""
import pytest
from rustest import fixture

@pytest.fixture
def pytest_only_fixture():
    return "from pytest"

@fixture
def rustest_fixture():
    return "from rustest"
""")

        (tests_dir / "test_example.py").write_text("""
def test_simple():
    assert True
""")
        return tests_dir

    @pytest.fixture
    def pure_rustest_conftest_project(tmp_path):
        """Create a project with only @rustest.fixture definitions."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "conftest.py").write_text("""
from rustest import fixture

@fixture
def my_fixture():
    return "hello"

@fixture
def another_fixture():
    return "world"
""")

        (tests_dir / "test_example.py").write_text("""
def test_simple():
    assert True
""")
        return tests_dir

    @pytest.fixture
    def pytest_compat_project(tmp_path):
        """Create a project with @pytest.fixture that works with --pytest-compat."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "conftest.py").write_text("""
import pytest

@pytest.fixture
def greeting():
    return "hello"
""")

        (tests_dir / "test_example.py").write_text("""
def test_greeting(greeting):
    assert greeting == "hello"
""")
        return tests_dir

    def test_detects_pytest_fixtures_in_conftest(pytest_fixture_conftest_project):
        """Test that rustest warns about @pytest.fixture definitions in conftest."""
        result = _run_rustest(pytest_fixture_conftest_project)

        output = result.stdout + result.stderr

        # Should mention --pytest-compat in the warning
        assert "--pytest-compat" in output, (
            f"Expected '--pytest-compat' in output:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_detects_pytest_fixtures_mentions_names(pytest_fixture_conftest_project):
        """Test that the warning mentions the detected pytest fixture names."""
        result = _run_rustest(pytest_fixture_conftest_project)

        output = result.stdout + result.stderr

        # Should mention the fixture names
        assert "db_session" in output, (
            f"Expected 'db_session' in output:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "api_client" in output, (
            f"Expected 'api_client' in output:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_mixed_conftest_warns_about_pytest_fixtures(mixed_conftest_project):
        """Test that a conftest with both @pytest.fixture and @rustest.fixture still warns."""
        result = _run_rustest(mixed_conftest_project)

        output = result.stdout + result.stderr

        # Should warn about the pytest fixture
        assert "pytest_only_fixture" in output, (
            f"Expected 'pytest_only_fixture' in output:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "--pytest-compat" in output, (
            f"Expected '--pytest-compat' in output:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_no_warning_in_compat_mode(pytest_compat_project):
        """Test that --pytest-compat mode does not emit pytest fixture warnings."""
        result = _run_rustest(pytest_compat_project, "--pytest-compat")

        stderr = result.stderr

        # Should NOT contain the warning about pytest fixtures
        assert "Found @pytest.fixture" not in stderr, (
            f"Did not expect pytest fixture warning in compat mode:\nstderr: {stderr}"
        )

        # The test should pass
        assert result.returncode == 0, (
            f"Expected tests to pass in compat mode:\nstdout: {result.stdout}\nstderr: {stderr}"
        )

    def test_no_warning_when_no_pytest_fixtures(pure_rustest_conftest_project):
        """Test that no warning is emitted when conftest has only @rustest.fixture."""
        result = _run_rustest(pure_rustest_conftest_project)

        stderr = result.stderr

        # Should NOT contain the warning
        assert "Found @pytest.fixture" not in stderr, (
            f"Did not expect pytest fixture warning:\nstderr: {stderr}"
        )

        # Tests should pass
        assert result.returncode == 0, (
            f"Expected tests to pass:\nstdout: {result.stdout}\nstderr: {stderr}"
        )

    @pytest.fixture
    def inline_pytest_fixture_project(tmp_path):
        """Project with @pytest.fixture defined in the test file itself."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "test_inline.py").write_text(
            """
import pytest

@pytest.fixture
def local_fixture():
    return "local"

def test_uses_local(local_fixture):
    assert local_fixture == "local"
"""
        )
        return tests_dir

    def test_detects_pytest_fixtures_in_test_files(inline_pytest_fixture_project):
        """When test files use @pytest.fixture, suggest --pytest-compat."""
        result = _run_rustest(inline_pytest_fixture_project)

        output = result.stdout + result.stderr

        assert "--pytest-compat" in output, (
            f"Expected --pytest-compat suggestion for inline fixtures:\n{output}"
        )
        assert "local_fixture" in output, (
            f"Expected 'local_fixture' name in warning output:\n{output}"
        )

    def test_no_warning_for_inline_fixtures_in_compat_mode(inline_pytest_fixture_project):
        """In compat mode, inline @pytest.fixture works and no warning shown."""
        result = _run_rustest(inline_pytest_fixture_project, "--pytest-compat")

        output = result.stdout + result.stderr

        assert result.returncode == 0, (
            f"Expected success with inline fixtures in compat mode:\n{output}"
        )
        assert "Found @pytest.fixture" not in output, (
            f"Did not expect pytest fixture warning in compat mode:\n{output}"
        )

    @pytest.fixture
    def pytest_conftest_project(tmp_path):
        """Project with @pytest.fixture in conftest.py and a test that requests one."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "conftest.py").write_text(
            """
import pytest

@pytest.fixture
def db_session():
    return {"connected": True}
"""
        )

        # This test requests db_session which is a @pytest.fixture — rustest won't load it
        (tests_dir / "test_needs_pytest.py").write_text(
            """
def test_needs_db(db_session):
    assert db_session["connected"]
"""
        )
        return tests_dir

    def test_unknown_fixture_error_suggests_compat_when_pytest_fixtures_present(
        pytest_conftest_project,
    ):
        """Unknown fixture error includes --pytest-compat hint when pytest fixtures detected."""
        result = _run_rustest(pytest_conftest_project)

        output = result.stdout + result.stderr

        # Should have the standard unknown fixture error
        assert "Unknown fixture" in output, f"Expected Unknown fixture error:\n{output}"

        # The error itself should mention --pytest-compat
        assert "--pytest-compat" in output, (
            f"Expected --pytest-compat hint in error output:\n{output}"
        )

    def test_unknown_fixture_error_no_hint_when_no_pytest_fixtures(tmp_path):
        """Unknown fixture error does NOT add hint when project has no pytest fixtures."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "test_pure.py").write_text(
            """
from rustest import fixture

@fixture
def my_fixture():
    return 42

def test_uses_unknown(nonexistent_fixture):
    pass
"""
        )

        result = _run_rustest(tests_dir)
        output = result.stdout + result.stderr

        assert "Unknown fixture" in output, f"Expected Unknown fixture error:\n{output}"

        # Should NOT add the pytest hint since no pytest fixtures exist
        assert "Run with --pytest-compat" not in output, (
            f"Should not suggest compat mode for pure rustest project:\n{output}"
        )

    @pytest.fixture
    def pytest_import_only_project(tmp_path):
        """Project that imports pytest but only uses pytest.raises (no @pytest.fixture)."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "test_raises.py").write_text(
            """
import pytest

def test_raises_error():
    with pytest.raises(ValueError):
        raise ValueError("expected")
"""
        )
        return tests_dir

    def test_suggests_compat_when_import_pytest_found(pytest_import_only_project):
        """When files import pytest (even without @pytest.fixture), suggest --pytest-compat."""
        result = _run_rustest(pytest_import_only_project)

        output = result.stdout + result.stderr

        assert "--pytest-compat" in output, (
            f"Expected --pytest-compat suggestion when import pytest found:\n{output}"
        )

    def test_no_note_in_compat_mode_for_import_pytest(pytest_import_only_project):
        """In --pytest-compat mode, no import-pytest note needed."""
        result = _run_rustest(pytest_import_only_project, "--pytest-compat")

        output = result.stdout + result.stderr

        # In compat mode, pytest.raises works natively
        assert result.returncode == 0, (
            f"Expected success with pytest.raises in compat mode:\n{output}"
        )

    @pytest.fixture
    def fixture_warning_priority_project(tmp_path):
        """Project with both `import pytest` AND `@pytest.fixture` — tests warning priority."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "conftest.py").write_text(
            """
import pytest

@pytest.fixture
def db_session():
    return {"connected": True}
"""
        )

        (tests_dir / "test_example.py").write_text(
            """
def test_simple():
    assert True
"""
        )
        return tests_dir

    def test_fixture_warning_suppresses_import_note(fixture_warning_priority_project):
        """When @pytest.fixture is detected, the generic 'import pytest' note should NOT appear."""
        result = _run_rustest(fixture_warning_priority_project)

        stderr = result.stderr

        # The specific fixture warning should appear
        assert "Found @pytest.fixture" in stderr, (
            f"Expected fixture warning in stderr:\n{stderr}"
        )

        # The generic import note should NOT appear — fixture warning takes priority
        assert "Note: Detected `import pytest`" not in stderr, (
            f"Generic import note should be suppressed when fixture warning is shown:\n{stderr}"
        )

    @pytest.fixture
    def nested_conftest_project(tmp_path):
        """Project with pytest fixtures in parent conftest but rustest fixtures in child."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        subdir = tests_dir / "subdir"
        subdir.mkdir()

        # Parent conftest has @pytest.fixture
        (tests_dir / "conftest.py").write_text(
            """
import pytest

@pytest.fixture
def parent_fixture():
    return "from parent"
"""
        )

        # Child conftest has only @rustest.fixture
        (subdir / "conftest.py").write_text(
            """
from rustest import fixture

@fixture
def child_fixture():
    return "from child"
"""
        )

        # Test in subdir requests the parent pytest fixture — should get hint
        (subdir / "test_nested.py").write_text(
            """
def test_needs_parent(parent_fixture):
    assert parent_fixture == "from parent"
"""
        )
        return tests_dir

    def test_nested_conftest_unknown_fixture_suggests_compat(nested_conftest_project):
        """Unknown fixture error in nested dir includes hint when parent conftest has pytest fixtures."""
        result = _run_rustest(nested_conftest_project)

        output = result.stdout + result.stderr

        # Should have the unknown fixture error for the parent fixture
        assert "Unknown fixture" in output, f"Expected Unknown fixture error:\n{output}"

        # The hint should be present because the parent conftest has @pytest.fixture
        assert "--pytest-compat" in output, (
            f"Expected --pytest-compat hint from parent conftest detection:\n{output}"
        )

    def test_help_mentions_pytest_compat_migration(tmp_path):
        """The --help output describes --pytest-compat's migration purpose."""
        python_path = sys.executable
        cmd = [python_path, "-m", "rustest", "--help"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        output = result.stdout

        assert "--pytest-compat" in output, f"Expected --pytest-compat in help:\n{output}"
        assert "migration" in output.lower(), (
            f"Expected 'migration' mention in --pytest-compat help:\n{output}"
        )
