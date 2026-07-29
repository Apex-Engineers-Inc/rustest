"""The CLI's **parser** surface.

Argument parsing only. Everything the CLI actually *does* is driven end to end against real
pytest in ``test_v2_flip_cli.py``, ``test_v2_run_cli.py`` and ``test_v2_collect_cli.py`` --
a mocked engine proves that a flag was forwarded and nothing about whether it works.

This module used to also hold `cli.run` mock tests, v1's exit-code mapping and a
``TestCIDetection`` block for the colour auto-detection only ``_run_v1`` consulted. All
three went with the v1 engine in Phase 4 Task 2.
"""

from __future__ import annotations

import pytest

from rustest import cli


class TestCli:
    def test_build_parser_defaults(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert tuple(args.paths) == (".",)
        assert args.capture_output is True


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

    def test_pytest_compat_flag_is_gone(self) -> None:
        """``--pytest-compat`` was deleted at the flip; the parser must not know it.

        Rejection happens *before* parsing (``cli.REMOVED_FLAGS``), so argparse having no
        such option is the contract here and ``cli.main`` owns the message and the exit code
        (pinned in ``tests/integration/test_pytest_fixture_detection.py``).
        """
        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            _ = parser.parse_args(["--pytest-compat"])
        assert "--pytest-compat" in cli.REMOVED_FLAGS

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
        """Test all flags can be combined.

        ``--ascii`` and ``--color`` are accepted and inert since Phase 4 Task 2 -- they were
        v1 renderer options and v2's output is neither coloured nor box-drawn. They stay on
        the parser because they live in projects' ``addopts`` forever, and refusing a purely
        cosmetic flag would refuse a run rustest can do. Parsing them is still the contract.
        """
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
        assert args.capture_output is False
        assert args.paths == ["tests/"]
