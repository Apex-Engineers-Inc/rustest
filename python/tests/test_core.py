from __future__ import annotations

from types import SimpleNamespace

from .helpers import stub_rust_module
from rustest import RunReport
from rustest.core import run as core_run


class TestCoreRun:
    def test_run_delegates_to_rust_layer(self) -> None:
        dummy_result = SimpleNamespace(
            name="test_sample",
            path="tests/test_sample.py",
            status="passed",
            duration=0.05,
            message=None,
            stdout=None,
            stderr=None,
        )
        dummy_report = SimpleNamespace(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=0.05,
            results=[dummy_result],
        )

        captured_args: dict[str, object] = {}

        def fake_run(
            paths,
            pattern,
            mark_expr,
            workers,
            capture_output,
            enable_codeblocks,
            last_failed_mode,
            fail_fast,
            pytest_compat,
            verbose,
            ascii,
            no_color,
        ):  # type: ignore[no-untyped-def]
            captured_args["paths"] = paths
            captured_args["pattern"] = pattern
            captured_args["mark_expr"] = mark_expr
            captured_args["workers"] = workers
            captured_args["capture_output"] = capture_output
            captured_args["enable_codeblocks"] = enable_codeblocks
            captured_args["last_failed_mode"] = last_failed_mode
            captured_args["fail_fast"] = fail_fast
            captured_args["pytest_compat"] = pytest_compat
            captured_args["verbose"] = verbose
            captured_args["ascii"] = ascii
            captured_args["no_color"] = no_color
            return dummy_report

        with stub_rust_module(run=fake_run):
            report = core_run(
                paths=["tests"],
                pattern="sample",
                workers=4,
                capture_output=False,
            )

        assert isinstance(report, RunReport)
        assert captured_args["paths"] == ["tests"]
        assert captured_args["pattern"] == "sample"
        assert captured_args["mark_expr"] is None
        assert captured_args["workers"] == 4
        assert captured_args["capture_output"] is False
        assert captured_args["enable_codeblocks"] is True
        assert captured_args["last_failed_mode"] == "none"
        assert captured_args["fail_fast"] is False
        assert captured_args["pytest_compat"] is False
        assert captured_args["verbose"] is False
        assert captured_args["ascii"] is False
        assert captured_args["no_color"] is False
        assert report.total == 1
        assert report.passed == 1
