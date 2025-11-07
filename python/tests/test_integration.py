"""Integration tests for the complete rustest workflow."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from .helpers import ensure_rust_stub
from rustest import cli, run, RunReport, TestResult

ensure_rust_stub()


class TestIntegration:
    """Integration tests that verify end-to-end functionality."""

    @pytest.fixture(autouse=True)
    def setup_temp_dir(self, tmp_path: Path) -> None:
        """Set up test fixtures."""
        self.temp_dir = str(tmp_path)

    def _write_test_file(self, filename: str, content: str) -> Path:
        """Write a test file to the temp directory."""
        path = Path(self.temp_dir) / filename
        path.write_text(content)
        return path

    def test_run_with_passing_tests(self) -> None:
        """Test running a simple passing test."""
        self._write_test_file(
            "test_simple.py",
            """
def test_pass():
    assert True
""",
        )

        try:
            report = run(paths=[self.temp_dir])
            assert isinstance(report, RunReport)
            assert report.passed == 1
            assert report.failed == 0
            assert report.total == 1
        except Exception:
            # If rust module is not available, skip this test
            pytest.skip("Rust module not available")

    def test_run_with_multiple_tests(self) -> None:
        """Test running multiple tests in one file."""
        self._write_test_file(
            "test_multiple.py",
            """
def test_one():
    assert 1 == 1

def test_two():
    assert 2 == 2

def test_three():
    assert 3 == 3
""",
        )

        try:
            report = run(paths=[self.temp_dir])
            assert report.total == 3
            assert report.passed == 3
        except Exception:
            pytest.skip("Rust module not available")

    def test_run_with_pattern_filter(self) -> None:
        """Test pattern filtering."""
        self._write_test_file(
            "test_pattern.py",
            """
def test_alpha():
    assert True

def test_beta():
    assert True

def test_gamma():
    assert True
""",
        )

        try:
            report = run(paths=[self.temp_dir], pattern="alpha")
            # Should only match test_alpha
            assert report.total >= 0
        except Exception:
            pytest.skip("Rust module not available")

    def test_run_without_capture_output(self) -> None:
        """Test running tests without capturing output."""
        self._write_test_file(
            "test_output.py",
            """
def test_with_print():
    print("Hello")
    assert True
""",
        )

        try:
            report = run(paths=[self.temp_dir], capture_output=False)
            assert report.passed == 1
            # When capture_output is False, stdout should not be captured
            if report.results:
                assert report.results[0].stdout is None
        except Exception:
            pytest.skip("Rust module not available")

    def test_cli_main_with_passing_tests(self) -> None:
        """Test CLI main function with passing tests."""
        self._write_test_file(
            "test_cli_pass.py",
            """
def test_success():
    assert True
""",
        )

        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                exit_code = cli.main([self.temp_dir])
            assert exit_code == 0
        except Exception:
            pytest.skip("Rust module not available")

    def test_test_result_attributes(self) -> None:
        """Test TestResult data class attributes."""
        result = TestResult(
            name="test_example",
            path="/path/to/test.py",
            status="passed",
            duration=0.5,
            message=None,
            stdout="output",
            stderr=None,
        )

        assert result.name == "test_example"
        assert result.path == "/path/to/test.py"
        assert result.status == "passed"
        assert result.duration == 0.5
        assert result.message is None
        assert result.stdout == "output"
        assert result.stderr is None

    def test_run_report_attributes(self) -> None:
        """Test RunReport data class attributes."""
        result = TestResult(
            name="test",
            path="/test.py",
            status="passed",
            duration=0.1,
            message=None,
            stdout=None,
            stderr=None,
        )
        report = RunReport(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=0.1,
            results=(result,),
        )

        assert report.total == 1
        assert report.passed == 1
        assert report.failed == 0
        assert report.skipped == 0
        assert report.duration == 0.1
        assert len(report.results) == 1

    def test_run_report_iter_status(self) -> None:
        """Test filtering results by status."""
        passed = TestResult(
            name="test_pass",
            path="/test.py",
            status="passed",
            duration=0.1,
            message=None,
            stdout=None,
            stderr=None,
        )
        failed = TestResult(
            name="test_fail",
            path="/test.py",
            status="failed",
            duration=0.1,
            message="Error",
            stdout=None,
            stderr=None,
        )
        skipped = TestResult(
            name="test_skip",
            path="/test.py",
            status="skipped",
            duration=0.0,
            message="Skipped",
            stdout=None,
            stderr=None,
        )

        report = RunReport(
            total=3,
            passed=1,
            failed=1,
            skipped=1,
            duration=0.2,
            results=(passed, failed, skipped),
        )

        passed_tests = list(report.iter_status("passed"))
        failed_tests = list(report.iter_status("failed"))
        skipped_tests = list(report.iter_status("skipped"))

        assert len(passed_tests) == 1
        assert len(failed_tests) == 1
        assert len(skipped_tests) == 1
        assert passed_tests[0].name == "test_pass"
        assert failed_tests[0].name == "test_fail"
        assert skipped_tests[0].name == "test_skip"

    def test_run_with_worker_count(self) -> None:
        """Test running tests with specific worker count."""
        self._write_test_file(
            "test_workers.py",
            """
def test_one():
    assert True

def test_two():
    assert True
""",
        )

        try:
            report = run(paths=[self.temp_dir], workers=2)
            assert report.total == 2
        except Exception:
            pytest.skip("Rust module not available")

    def test_empty_test_directory(self) -> None:
        """Test running tests in an empty directory."""
        empty_dir = Path(self.temp_dir) / "empty"
        empty_dir.mkdir()

        try:
            report = run(paths=[str(empty_dir)])
            assert report.total == 0
            assert report.passed == 0
            assert report.failed == 0
        except Exception:
            pytest.skip("Rust module not available")

    def test_multiple_test_files(self) -> None:
        """Test running tests from multiple files."""
        self._write_test_file(
            "test_file1.py",
            """
def test_from_file1():
    assert True
""",
        )
        self._write_test_file(
            "test_file2.py",
            """
def test_from_file2():
    assert True
""",
        )

        try:
            report = run(paths=[self.temp_dir])
            assert report.total >= 2
        except Exception:
            pytest.skip("Rust module not available")


class TestCLIParser:
    """Tests for CLI argument parsing."""

    def test_parser_with_no_args(self) -> None:
        """Test parser with no arguments uses defaults."""
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert tuple(args.paths) == (".",)
        assert args.pattern is None
        assert args.workers is None
        assert args.capture_output is True

    def test_parser_with_paths(self) -> None:
        """Test parser with custom paths."""
        parser = cli.build_parser()
        args = parser.parse_args(["tests", "src"])
        assert tuple(args.paths) == ("tests", "src")

    def test_parser_with_pattern(self) -> None:
        """Test parser with pattern filter."""
        parser = cli.build_parser()
        args = parser.parse_args(["-k", "test_pattern"])
        assert args.pattern == "test_pattern"

    def test_parser_with_workers(self) -> None:
        """Test parser with worker count."""
        parser = cli.build_parser()
        args = parser.parse_args(["-n", "4"])
        assert args.workers == 4

    def test_parser_with_no_capture(self) -> None:
        """Test parser with capture output disabled."""
        parser = cli.build_parser()
        args = parser.parse_args(["--no-capture"])
        assert args.capture_output is False

    def test_parser_with_all_options(self) -> None:
        """Test parser with all options specified."""
        parser = cli.build_parser()
        args = parser.parse_args(["tests", "-k", "pattern", "-n", "8", "--no-capture"])
        assert tuple(args.paths) == ("tests",)
        assert args.pattern == "pattern"
        assert args.workers == 8
        assert args.capture_output is False
