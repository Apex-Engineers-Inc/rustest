from __future__ import annotations

from unittest.mock import patch

import pytest

from .helpers import stub_rust_module
from rustest import RunReport, TestResult
from rustest import cli


class TestCli:
    def test_build_parser_defaults(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert tuple(args.paths) == (".",)
        assert args.capture_output is True

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
            pytest_compat=False,
            verbose=False,
            ascii=False,
            no_color=False,
        )
        assert exit_code == 0

    def test_main_surfaces_rust_errors(self) -> None:
        def raising_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        with stub_rust_module(run=raising_run):
            with pytest.raises(RuntimeError):
                cli.main(["tests"])


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
