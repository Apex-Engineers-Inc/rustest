"""
Integration tests for loop scope mismatch error messages.

These tests verify that rustest provides helpful error messages when users
explicitly set a loop_scope that's too narrow for their async fixtures.

NOTE: These tests use pytest fixtures and subprocess to test rustest externally.
They require pytest to run and are skipped when run with rustest.
"""

import os
import subprocess
import sys
from pathlib import Path

# Skip this module when running under rustest (not pytest)
if os.environ.get("RUSTEST_RUNNING") == "1":
    # Running under rustest - don't define any test functions
    pass
else:
    import pytest

    def _run_rustest(project_dir, *args):
        """Run rustest on a project directory and return result."""
        # Use the system Python that has rustest installed, not pytest's isolated env
        python_path = "/usr/local/bin/python"
        if not Path(python_path).exists():
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
    def loop_scope_mismatch_project(tmp_path):
        """Create a project with a loop scope mismatch error."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        # Create a test file with session-scoped async fixture
        # but explicit function loop_scope on the test
        (tests_dir / "test_mismatch.py").write_text("""
import asyncio
from rustest import fixture, mark


@fixture(scope="session")
async def session_client():
    \"\"\"Session-scoped async fixture.\"\"\"
    await asyncio.sleep(0)
    return {"connected": True}


@mark.asyncio(loop_scope="function")
async def test_with_wrong_scope(session_client):
    \"\"\"Test with explicit function loop_scope but session fixture.\"\"\"
    assert session_client["connected"] is True
""")

        return tests_dir

    @pytest.fixture
    def loop_scope_mismatch_class_project(tmp_path):
        """Create a project with class-level loop scope mismatch."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        (tests_dir / "test_class_mismatch.py").write_text("""
import asyncio
from rustest import fixture, mark


@fixture(scope="module")
async def module_db():
    \"\"\"Module-scoped async database fixture.\"\"\"
    await asyncio.sleep(0)
    return {"db": "connected"}


@mark.asyncio(loop_scope="class")
class TestWithWrongScope:
    \"\"\"Test class with class loop_scope but module fixture.\"\"\"

    async def test_one(self, module_db):
        assert module_db["db"] == "connected"

    async def test_two(self, module_db):
        assert module_db["db"] == "connected"
""")

        return tests_dir

    @pytest.fixture
    def no_mismatch_project(tmp_path):
        """Create a project with compatible loop scope."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        # Session fixture with session loop_scope - should work fine
        (tests_dir / "test_compatible.py").write_text("""
import asyncio
from rustest import fixture, mark


@fixture(scope="session")
async def session_client():
    \"\"\"Session-scoped async fixture.\"\"\"
    await asyncio.sleep(0)
    return {"connected": True}


@mark.asyncio(loop_scope="session")
async def test_with_correct_scope(session_client):
    \"\"\"Test with matching session loop_scope.\"\"\"
    assert session_client["connected"] is True
""")

        return tests_dir

    def test_loop_scope_mismatch_error_message(loop_scope_mismatch_project):
        """Test that loop scope mismatch produces helpful error message."""
        result = _run_rustest(loop_scope_mismatch_project)

        # Should fail
        assert result.returncode != 0, f"Expected failure but got success: {result.stderr}"

        # Check for helpful error message components
        output = result.stdout + result.stderr

        # Should mention it's a loop scope mismatch
        assert "Loop scope mismatch" in output, (
            f"Expected 'Loop scope mismatch' in output: {output}"
        )

        # Should mention the test name
        assert "test_with_wrong_scope" in output, f"Expected test name in output: {output}"

        # Should mention the explicit scope used
        assert 'loop_scope="function"' in output, f"Expected explicit scope in output: {output}"

        # Should mention the fixture that requires wider scope
        assert "session_client" in output or "session-scoped" in output, (
            f"Expected session fixture info in output: {output}"
        )

        # Should provide fix suggestions
        assert "To fix this" in output, f"Expected fix suggestions in output: {output}"
        assert "@mark.asyncio" in output, f"Expected @mark.asyncio mention in output: {output}"

    def test_loop_scope_mismatch_class_error_message(loop_scope_mismatch_class_project):
        """Test that class-level loop scope mismatch also produces helpful error."""
        result = _run_rustest(loop_scope_mismatch_class_project)

        # Should fail
        assert result.returncode != 0, f"Expected failure but got success: {result.stderr}"

        # Check for helpful error message
        output = result.stdout + result.stderr

        # Should mention loop scope mismatch
        assert "Loop scope mismatch" in output, (
            f"Expected 'Loop scope mismatch' in output: {output}"
        )

        # Should mention the module-scoped fixture
        assert "module_db" in output or "module-scoped" in output, (
            f"Expected module fixture info in output: {output}"
        )

    def test_compatible_loop_scope_passes(no_mismatch_project):
        """Test that compatible loop scope works without error."""
        result = _run_rustest(no_mismatch_project)

        # Should succeed
        assert result.returncode == 0, f"Expected success: {result.stderr}"
        assert "1 passed" in result.stderr, f"Expected test to pass: {result.stderr}"

        # Should NOT have loop scope mismatch error
        output = result.stdout + result.stderr
        assert "Loop scope mismatch" not in output, f"Should not have mismatch error: {output}"
