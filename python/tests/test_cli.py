from __future__ import annotations

import os
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
            collection_errors=(),
        )

        # Clear CI environment variables to simulate local environment
        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
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

    def test_color_auto_by_default(self) -> None:
        """Test color is auto by default."""
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert args.color == "auto"

    def test_color_always(self) -> None:
        """Test --color always forces colors on."""
        parser = cli.build_parser()
        args = parser.parse_args(["--color", "always"])
        assert args.color == "always"

    def test_color_never(self) -> None:
        """Test --color never disables colors."""
        parser = cli.build_parser()
        args = parser.parse_args(["--color", "never"])
        assert args.color == "never"

    def test_color_auto_explicit(self) -> None:
        """Test --color auto explicitly."""
        parser = cli.build_parser()
        args = parser.parse_args(["--color", "auto"])
        assert args.color == "auto"

    def test_combined_flags(self) -> None:
        """Test multiple flags can be combined."""
        parser = cli.build_parser()
        args = parser.parse_args(["-v", "--ascii", "--color", "never"])
        assert args.verbose is True
        assert args.ascii is True
        assert args.color == "never"


class TestCIDetection:
    """Test CI environment detection."""

    def test_ci_detected_with_github_actions(self) -> None:
        """Test CI detection with GitHub Actions env var."""
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            assert cli.is_ci_environment() is True

    def test_ci_detected_with_ci_var(self) -> None:
        """Test CI detection with generic CI env var."""
        with patch.dict(os.environ, {"CI": "true"}):
            assert cli.is_ci_environment() is True

    def test_ci_detected_with_gitlab(self) -> None:
        """Test CI detection with GitLab CI env var."""
        with patch.dict(os.environ, {"GITLAB_CI": "true"}):
            assert cli.is_ci_environment() is True

    def test_ci_detected_with_jenkins(self) -> None:
        """Test CI detection with Jenkins env var."""
        with patch.dict(os.environ, {"JENKINS_HOME": "/var/jenkins"}):
            assert cli.is_ci_environment() is True

    def test_ci_not_detected_locally(self) -> None:
        """Test CI is not detected in local environment."""
        # Clear all CI environment variables
        ci_vars = [
            "CI",
            "CONTINUOUS_INTEGRATION",
            "GITHUB_ACTIONS",
            "GITLAB_CI",
            "CIRCLECI",
            "TRAVIS",
            "JENKINS_HOME",
            "JENKINS_URL",
            "BUILDKITE",
            "DRONE",
            "TEAMCITY_VERSION",
            "TF_BUILD",
            "BITBUCKET_BUILD_NUMBER",
            "CODEBUILD_BUILD_ID",
            "APPVEYOR",
        ]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            assert cli.is_ci_environment() is False

    def test_color_disabled_in_ci_by_default(self) -> None:
        """Test that colors are disabled in CI when not explicitly set."""
        report = RunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            results=(),
            collection_errors=(),
        )

        with patch.dict(os.environ, {"CI": "true"}):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main([])

            # Should have no_color=True in CI
            assert mock_run.call_args.kwargs["no_color"] is True

    def test_color_enabled_locally_by_default(self) -> None:
        """Test that colors are enabled locally when not explicitly set."""
        report = RunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            results=(),
            collection_errors=(),
        )

        # Clear all CI vars to simulate local environment
        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main([])

            # Should have no_color=False (colors enabled) locally
            assert mock_run.call_args.kwargs["no_color"] is False

    def test_color_always_overrides_ci_detection(self) -> None:
        """Test that --color always overrides CI detection."""
        report = RunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            results=(),
            collection_errors=(),
        )

        with patch.dict(os.environ, {"CI": "true"}):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main(["--color", "always"])

            # Should have no_color=False even in CI when --color always is passed
            assert mock_run.call_args.kwargs["no_color"] is False


class TestCliEdgeCases:
    """Test CLI edge cases and error handling."""

    def test_no_capture_flag(self) -> None:
        """Test --no-capture flag disables output capture."""
        parser = cli.build_parser()
        args = parser.parse_args(["--no-capture"])
        assert args.capture_output is False

    def test_pattern_filter_short(self) -> None:
        """Test -k flag for pattern filtering."""
        parser = cli.build_parser()
        args = parser.parse_args(["-k", "test_something"])
        assert args.pattern == "test_something"

    def test_pattern_filter_long(self) -> None:
        """Test --pattern flag for pattern filtering."""
        parser = cli.build_parser()
        args = parser.parse_args(["--pattern", "test_other"])
        assert args.pattern == "test_other"

    def test_mark_filter_short(self) -> None:
        """Test -m flag for mark filtering."""
        parser = cli.build_parser()
        args = parser.parse_args(["-m", "slow"])
        assert args.mark_expr == "slow"

    def test_mark_filter_long(self) -> None:
        """Test --marks flag for mark filtering."""
        parser = cli.build_parser()
        args = parser.parse_args(["--marks", "integration"])
        assert args.mark_expr == "integration"

    def test_mark_expression_complex(self) -> None:
        """Test complex mark expressions."""
        parser = cli.build_parser()
        args = parser.parse_args(["-m", "slow and not integration"])
        assert args.mark_expr == "slow and not integration"

    def test_workers_flag_short(self) -> None:
        """Test -n flag for worker count."""
        parser = cli.build_parser()
        args = parser.parse_args(["-n", "4"])
        assert args.workers == 4

    def test_workers_flag_long(self) -> None:
        """Test --workers flag for worker count."""
        parser = cli.build_parser()
        args = parser.parse_args(["--workers", "8"])
        assert args.workers == 8

    def test_workers_none_by_default(self) -> None:
        """Test workers is None by default."""
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert args.workers is None

    def test_last_failed_flag(self) -> None:
        """Test --lf/--last-failed flag."""
        parser = cli.build_parser()
        args = parser.parse_args(["--lf"])
        assert args.last_failed is True

    def test_failed_first_flag(self) -> None:
        """Test --ff/--failed-first flag."""
        parser = cli.build_parser()
        args = parser.parse_args(["--ff"])
        assert args.failed_first is True

    def test_fail_fast_short(self) -> None:
        """Test -x flag for fail fast."""
        parser = cli.build_parser()
        args = parser.parse_args(["-x"])
        assert args.fail_fast is True

    def test_fail_fast_long(self) -> None:
        """Test --exitfirst flag for fail fast."""
        parser = cli.build_parser()
        args = parser.parse_args(["--exitfirst"])
        assert args.fail_fast is True

    def test_pytest_compat_flag(self) -> None:
        """Test --pytest-compat flag."""
        parser = cli.build_parser()
        args = parser.parse_args(["--pytest-compat"])
        assert args.pytest_compat is True

    def test_no_codeblocks_flag(self) -> None:
        """Test --no-codeblocks flag."""
        parser = cli.build_parser()
        args = parser.parse_args(["--no-codeblocks"])
        assert args.enable_codeblocks is False

    def test_multiple_paths(self) -> None:
        """Test multiple path arguments."""
        parser = cli.build_parser()
        args = parser.parse_args(["tests/", "examples/"])
        assert args.paths == ["tests/", "examples/"]

    def test_specific_file_path(self) -> None:
        """Test specific file path."""
        parser = cli.build_parser()
        args = parser.parse_args(["tests/test_specific.py"])
        assert args.paths == ["tests/test_specific.py"]

    def test_pattern_with_special_chars(self) -> None:
        """Test pattern with special regex characters."""
        parser = cli.build_parser()
        args = parser.parse_args(["-k", "test_[abc]_func"])
        assert args.pattern == "test_[abc]_func"

    def test_mark_with_parentheses(self) -> None:
        """Test mark expression with parentheses."""
        parser = cli.build_parser()
        args = parser.parse_args(["-m", "(slow or integration) and not smoke"])
        assert args.mark_expr == "(slow or integration) and not smoke"

    def test_all_flags_combined(self) -> None:
        """Test all flags can be combined."""
        parser = cli.build_parser()
        args = parser.parse_args(
            [
                "-v",
                "--ascii",
                "--color",
                "always",
                "-k",
                "test_pattern",
                "-m",
                "slow",
                "-n",
                "4",
                "--lf",
                "-x",
                "--pytest-compat",
                "--no-capture",
                "tests/",
            ]
        )
        assert args.verbose is True
        assert args.ascii is True
        assert args.color == "always"
        assert args.pattern == "test_pattern"
        assert args.mark_expr == "slow"
        assert args.workers == 4
        assert args.last_failed is True
        assert args.fail_fast is True
        assert args.pytest_compat is True
        assert args.capture_output is False
        assert args.paths == ["tests/"]


class TestCliReturnCodes:
    """Test CLI return codes for different scenarios."""

    def test_returns_zero_on_success(self) -> None:
        """Test exit code is 0 when all tests pass."""
        report = RunReport(
            total=5,
            passed=5,
            failed=0,
            skipped=0,
            duration=0.5,
            results=(),
            collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report):
                exit_code = cli.main(["tests"])

        assert exit_code == 0

    def test_returns_one_on_failure(self) -> None:
        """Test exit code is 1 when tests fail."""
        result = TestResult(
            name="test_failure",
            path="tests/test_fail.py",
            status="failed",
            duration=0.1,
            message="AssertionError",
            stdout=None,
            stderr=None,
        )
        report = RunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(result,),
            collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report):
                exit_code = cli.main(["tests"])

        assert exit_code == 1

    def test_returns_two_on_collection_errors(self) -> None:
        """Test exit code is 2 when there are collection errors."""
        from rustest import CollectionError

        collection_error = CollectionError(
            path="tests/test_broken.py",
            message="SyntaxError: invalid syntax",
        )
        report = RunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.1,
            results=(),
            collection_errors=(collection_error,),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report):
                exit_code = cli.main(["tests"])

        assert exit_code == 2

    def test_returns_zero_with_only_skipped(self) -> None:
        """Test exit code is 0 when all tests are skipped."""
        result = TestResult(
            name="test_skipped",
            path="tests/test_skip.py",
            status="skipped",
            duration=0.0,
            message="Skipped because reason",
            stdout=None,
            stderr=None,
        )
        report = RunReport(
            total=1,
            passed=0,
            failed=0,
            skipped=1,
            duration=0.1,
            results=(result,),
            collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report):
                exit_code = cli.main(["tests"])

        assert exit_code == 0


class TestCliOutput:
    """Test CLI output formatting."""

    def test_verbose_mode_passed_to_run(self) -> None:
        """Test verbose flag is passed to run function."""
        report = RunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            results=(),
            collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main(["-v"])

            assert mock_run.call_args.kwargs["verbose"] is True

    def test_ascii_mode_passed_to_run(self) -> None:
        """Test ascii flag is passed to run function."""
        report = RunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            results=(),
            collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main(["--ascii"])

            assert mock_run.call_args.kwargs["ascii"] is True
