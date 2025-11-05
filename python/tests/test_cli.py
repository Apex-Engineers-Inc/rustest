from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from .helpers import stub_rust_module
from rustest import RunReport, TestResult
from rustest import _cli


class TestCli:
    def test_build_parser_defaults(self) -> None:
        parser = _cli.build_parser()
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
            _cli._print_report(report)

        output = buffer.getvalue()
        assert "FAILED" in output
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

        with patch("rustest._cli.run", return_value=report) as mock_run:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = _cli.main(["tests"])

        mock_run.assert_called_once_with(
            paths=("tests",),
            pattern=None,
            workers=None,
            capture_output=True,
        )
        assert exit_code == 0

    def test_main_surfaces_rust_errors(self) -> None:
        def raising_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        with stub_rust_module(run=raising_run):
            with pytest.raises(RuntimeError):
                _cli.main(["tests"])
