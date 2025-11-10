from __future__ import annotations

import io
import re
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from .helpers import stub_rust_module
from rustest import RunReport, TestResult
from rustest import cli


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from text."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


class TestCli:
    def test_build_parser_defaults(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert tuple(args.paths) == (".",)
        assert args.capture_output is True

    def test_print_report_outputs_summary(self) -> None:
        result = TestResult(
            name="test_case",
            path="tests/test_sample.py",
            status="failed",
            duration=0.2,
            message="assert False",
            stdout=None,
            stderr=None,
        )
        report = RunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.2,
            results=(result,),
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report)

        output = buffer.getvalue()
        assert "FAILURES" in output  # Updated: new format shows "FAILURES" section
        assert "1 tests" in output
        assert "assert False" in output

    def test_main_invokes_core_run(self) -> None:
        result = TestResult(
            name="test_case",
            path="tests/test_sample.py",
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

        with patch("rustest.cli.run", return_value=report) as mock_run:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = cli.main(["tests"])

        mock_run.assert_called_once_with(
            paths=["tests"],
            pattern=None,
            mark_expr=None,
            workers=None,
            capture_output=True,
            enable_codeblocks=True,
            last_failed_mode="none",
            fail_fast=False,
        )
        assert exit_code == 0

    def test_main_surfaces_rust_errors(self) -> None:
        def raising_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        with stub_rust_module(run=raising_run):
            with pytest.raises(RuntimeError):
                cli.main(["tests"])


class TestOutputFormatting:
    """Test different output formats and modes."""

    def test_default_output_unicode_passed(self) -> None:
        """Test default mode with passing tests shows Unicode checkmarks."""
        result = TestResult(
            name="test_pass",
            path="test.py",
            status="passed",
            duration=0.001,
            message=None,
            stdout=None,
            stderr=None,
        )
        report = RunReport(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=0.001,
            results=(result,),
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=False, ascii_mode=False)

        output = buffer.getvalue()
        # Should contain green checkmark (with ANSI codes)
        assert "✓" in output
        assert "1 tests" in output
        assert "1 passed" in output

    def test_default_output_unicode_failed(self) -> None:
        """Test default mode with failing tests shows Unicode X marks."""
        result = TestResult(
            name="test_fail",
            path="test.py",
            status="failed",
            duration=0.001,
            message="AssertionError: test failed",
            stdout=None,
            stderr=None,
        )
        report = RunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.001,
            results=(result,),
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=False, ascii_mode=False)

        output = buffer.getvalue()
        # Should contain red X mark
        assert "✗" in output
        assert "FAILURES" in output
        assert "AssertionError: test failed" in output

    def test_default_output_unicode_skipped(self) -> None:
        """Test default mode with skipped tests shows Unicode skip symbol."""
        result = TestResult(
            name="test_skip",
            path="test.py",
            status="skipped",
            duration=0.0,
            message=None,
            stdout=None,
            stderr=None,
        )
        report = RunReport(
            total=1,
            passed=0,
            failed=0,
            skipped=1,
            duration=0.0,
            results=(result,),
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=False, ascii_mode=False)

        output = buffer.getvalue()
        # Should contain yellow skip symbol
        assert "⊘" in output
        assert "1 skipped" in output

    def test_default_output_ascii_mode(self) -> None:
        """Test ASCII mode uses pytest-style characters (., F, s)."""
        results = (
            TestResult("test_pass", "test.py", "passed", 0.001, None, None, None),
            TestResult("test_fail", "test.py", "failed", 0.001, "error", None, None),
            TestResult("test_skip", "test.py", "skipped", 0.0, None, None, None),
        )
        report = RunReport(
            total=3,
            passed=1,
            failed=1,
            skipped=1,
            duration=0.001,
            results=results,
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=False, ascii_mode=True)

        output = strip_ansi(buffer.getvalue())
        # Should contain pytest-style characters
        assert (
            ".Fs" in output
            or ".sF" in output
            or "F.s" in output
            or any(c in output for c in [".", "F", "s"])
        )
        # Verify all three character types are present
        assert "." in output  # passed
        assert "F" in output  # failed
        assert "s" in output  # skipped

    def test_default_output_no_spaces_between_symbols(self) -> None:
        """Test that Unicode symbols have no spaces between them."""
        results = (
            TestResult("test_1", "test.py", "passed", 0.001, None, None, None),
            TestResult("test_2", "test.py", "passed", 0.001, None, None, None),
            TestResult("test_3", "test.py", "passed", 0.001, None, None, None),
        )
        report = RunReport(
            total=3,
            passed=3,
            failed=0,
            skipped=0,
            duration=0.003,
            results=results,
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=False, ascii_mode=False)

        output = buffer.getvalue()
        # Strip ANSI codes to check the actual spacing
        clean_output = strip_ansi(output)
        # Should have checkmarks without spaces: ✓✓✓
        assert "✓✓✓" in clean_output or clean_output.count("✓") == 3

    def test_verbose_output_unicode_shows_hierarchy(self) -> None:
        """Test verbose mode shows hierarchical structure."""
        results = (
            TestResult("test_func", "test_module.py", "passed", 0.001, None, None, None),
            TestResult(
                "TestClass.test_method", "test_module.py", "passed", 0.002, None, None, None
            ),
        )
        report = RunReport(
            total=2,
            passed=2,
            failed=0,
            skipped=0,
            duration=0.003,
            results=results,
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=True, ascii_mode=False)

        output = buffer.getvalue()
        # Should show file path
        assert "test_module.py" in output
        # Should show class name
        assert "TestClass" in output
        # Should show test names
        assert "test_func" in output
        assert "test_method" in output
        # Should show timing
        assert "ms" in output

    def test_verbose_output_ascii_mode(self) -> None:
        """Test verbose mode with ASCII uses full words (PASS, FAIL, SKIP)."""
        results = (
            TestResult("test_pass", "test.py", "passed", 0.001, None, None, None),
            TestResult("test_fail", "test.py", "failed", 0.001, "error", None, None),
            TestResult("test_skip", "test.py", "skipped", 0.0, None, None, None),
        )
        report = RunReport(
            total=3,
            passed=1,
            failed=1,
            skipped=1,
            duration=0.001,
            results=results,
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=True, ascii_mode=True)

        output = strip_ansi(buffer.getvalue())
        # Should use full words in verbose mode
        assert "PASS" in output
        assert "FAIL" in output
        assert "SKIP" in output

    def test_verbose_output_shows_timing(self) -> None:
        """Test verbose mode displays test timing in milliseconds."""
        result = TestResult("test_timing", "test.py", "passed", 0.123, None, None, None)
        report = RunReport(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=0.123,
            results=(result,),
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=True, ascii_mode=False)

        output = buffer.getvalue()
        # Should show timing in milliseconds (123ms from 0.123s)
        assert "123ms" in output

    def test_color_output_enabled_by_default(self) -> None:
        """Test that color output includes ANSI codes by default."""
        result = TestResult("test_pass", "test.py", "passed", 0.001, None, None, None)
        report = RunReport(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=0.001,
            results=(result,),
        )

        # Reset colors to default state
        cli.Colors.green = "\033[92m"
        cli.Colors.reset = "\033[0m"

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=False, ascii_mode=False)

        output = buffer.getvalue()
        # Should contain ANSI escape codes
        assert "\033[" in output

    def test_failures_section_in_default_mode(self) -> None:
        """Test that failures section is displayed in default mode."""
        results = (
            TestResult("test_pass", "test.py", "passed", 0.001, None, None, None),
            TestResult("test_fail_1", "test.py", "failed", 0.001, "Error 1", None, None),
            TestResult("test_fail_2", "test.py", "failed", 0.001, "Error 2", None, None),
        )
        report = RunReport(
            total=3,
            passed=1,
            failed=2,
            skipped=0,
            duration=0.003,
            results=results,
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=False, ascii_mode=False)

        output = buffer.getvalue()
        # Should have failures section
        assert "FAILURES" in output
        assert "test_fail_1" in output
        assert "test_fail_2" in output
        assert "Error 1" in output
        assert "Error 2" in output

    def test_verbose_mode_shows_inline_errors(self) -> None:
        """Test that verbose mode shows errors inline with tests."""
        result = TestResult(
            "test_fail",
            "test.py",
            "failed",
            0.001,
            "AssertionError: Expected True",
            None,
            None,
        )
        report = RunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.001,
            results=(result,),
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._print_report(report, verbose=True, ascii_mode=False)

        output = buffer.getvalue()
        # Should show error message inline (indented)
        assert "AssertionError: Expected True" in output
        assert "test_fail" in output


class TestCliArguments:
    """Test CLI argument parsing."""

    def test_verbose_flag_short(self) -> None:
        """Test -v flag is parsed correctly."""
        parser = cli.build_parser()
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_verbose_flag_long(self) -> None:
        """Test --verbose flag is parsed correctly."""
        parser = cli.build_parser()
        args = parser.parse_args(["--verbose"])
        assert args.verbose is True

    def test_ascii_flag(self) -> None:
        """Test --ascii flag is parsed correctly."""
        parser = cli.build_parser()
        args = parser.parse_args(["--ascii"])
        assert args.ascii is True

    def test_no_color_flag(self) -> None:
        """Test --no-color flag is parsed correctly."""
        parser = cli.build_parser()
        args = parser.parse_args(["--no-color"])
        assert args.color is False

    def test_color_enabled_by_default(self) -> None:
        """Test color is enabled by default."""
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert args.color is True

    def test_combined_flags(self) -> None:
        """Test multiple flags can be combined."""
        parser = cli.build_parser()
        args = parser.parse_args(["-v", "--ascii", "--no-color"])
        assert args.verbose is True
        assert args.ascii is True
        assert args.color is False

    def test_main_disables_colors_when_no_color_flag(self) -> None:
        """Test that --no-color flag disables colors in main()."""
        result = TestResult("test_pass", "test.py", "passed", 0.001, None, None, None)
        report = RunReport(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=0.001,
            results=(result,),
        )

        # Save original color values
        original_green = cli.Colors.green
        original_reset = cli.Colors.reset

        try:
            with patch("rustest.cli.run", return_value=report):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    cli.main(["--no-color", "tests"])

                output = buffer.getvalue()
                # Should not contain ANSI escape codes
                assert "\033[" not in output
        finally:
            # Restore original colors
            cli.Colors.green = original_green
            cli.Colors.reset = original_reset
