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
