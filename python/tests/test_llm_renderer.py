"""Tests for LlmRenderer — minimal, token-efficient output for LLM tools."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Fake event stand-ins (lightweight dataclasses matching real event shapes)
# ---------------------------------------------------------------------------


@dataclass
class FakeSuiteStartedEvent:
    total_files: int
    total_tests: int
    timestamp: float = 0.0


@dataclass
class FakeTestCompletedEvent:
    test_id: str
    file_path: str
    test_name: str
    status: str
    duration: float
    message: str | None
    timestamp: float = 0.0


@dataclass
class FakeSuiteCompletedEvent:
    total: int
    passed: int
    failed: int
    skipped: int
    errors: int
    duration: float
    timestamp: float = 0.0


@dataclass
class FakeCollectionErrorEvent:
    path: str
    message: str
    timestamp: float = 0.0


@dataclass
class FakeCollectionStartedEvent:
    timestamp: float = 0.0


@dataclass
class FakeCollectionProgressEvent:
    file_path: str
    tests_collected: int
    files_collected: int
    timestamp: float = 0.0


@dataclass
class FakeCollectionCompletedEvent:
    total_files: int
    total_tests: int
    duration: float
    timestamp: float = 0.0


@dataclass
class FakeFileStartedEvent:
    file_path: str
    total_tests: int
    timestamp: float = 0.0


@dataclass
class FakeFileCompletedEvent:
    file_path: str
    duration: float
    passed: int
    failed: int
    skipped: int
    timestamp: float = 0.0


@dataclass
class FakeTestResult:
    name: str
    path: str
    status: str
    duration: float
    message: str | None
    stdout: str | None
    stderr: str | None


@dataclass
class FakeRunReport:
    total: int
    passed: int
    failed: int
    skipped: int
    duration: float
    results: tuple[Any, ...]
    collection_errors: tuple[Any, ...]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLlmRendererAllPass:
    """Core test: all-pass scenario emits a minimal summary line."""

    def test_all_pass_output(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=2, total_tests=5))

        for i in range(5):
            renderer.handle(
                FakeTestCompletedEvent(
                    test_id=f"tests/test_sample.py::test_{i}",
                    file_path="tests/test_sample.py",
                    test_name=f"test_{i}",
                    status="passed",
                    duration=0.1,
                    message=None,
                )
            )

        report = FakeRunReport(
            total=5,
            passed=5,
            failed=0,
            skipped=0,
            duration=0.5,
            results=(),
            collection_errors=(),
        )
        renderer.finalize(report)

        assert buf.getvalue() == "5 passed 0.5s\n"


class TestLlmRendererSummaryLine:
    """Summary line format: zero counts omitted, correct token formatting."""

    def test_mixed_results_summary(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=3))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_pass",
                file_path="t.py",
                test_name="test_pass",
                status="passed",
                duration=0.1,
                message=None,
            )
        )
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_fail",
                file_path="t.py",
                test_name="test_fail",
                status="failed",
                duration=0.1,
                message="assert 1 == 2",
            )
        )
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_skip",
                file_path="t.py",
                test_name="test_skip",
                status="skipped",
                duration=0.0,
                message=None,
            )
        )

        failed_result = FakeTestResult(
            name="test_fail",
            path="t.py",
            status="failed",
            duration=0.1,
            message="assert 1 == 2",
            stdout=None,
            stderr=None,
        )
        report = FakeRunReport(
            total=3,
            passed=1,
            failed=1,
            skipped=1,
            duration=1.0,
            results=(failed_result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        # Summary line must contain each non-zero count and duration
        assert "1 passed" in output
        assert "1 failed" in output
        assert "1 skipped" in output
        assert "1.0s" in output

    def test_zero_counts_omitted_from_summary(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=2))
        for i in range(2):
            renderer.handle(
                FakeTestCompletedEvent(
                    test_id=f"t::test_{i}",
                    file_path="t.py",
                    test_name=f"test_{i}",
                    status="passed",
                    duration=0.1,
                    message=None,
                )
            )

        report = FakeRunReport(
            total=2,
            passed=2,
            failed=0,
            skipped=0,
            duration=0.2,
            results=(),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        assert "failed" not in output
        assert "skipped" not in output
        assert "2 passed" in output

    def test_zero_collected_when_no_tests(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        report = FakeRunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            results=(),
            collection_errors=(),
        )
        renderer.finalize(report)

        assert buf.getvalue() == "0 collected\n"

    def test_duration_formatting(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_a",
                file_path="t.py",
                test_name="test_a",
                status="passed",
                duration=0.1,
                message=None,
            )
        )

        report = FakeRunReport(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=1.23,
            results=(),
            collection_errors=(),
        )
        renderer.finalize(report)

        # Spec: duration formatted as {x.y}s — one decimal place
        assert "1.2s" in buf.getvalue()


class TestLlmRendererFailureLines:
    """Failure lines: FAIL {test_name} {file_path} {first_line_of_error}."""

    def test_fail_line_emitted_for_failure(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_x.py::test_bad",
                file_path="tests/test_x.py",
                test_name="test_bad",
                status="failed",
                duration=0.05,
                message="AssertionError: assert 1 == 2\nwhere 1 = foo()",
            )
        )

        result = FakeTestResult(
            name="test_bad",
            path="tests/test_x.py",
            status="failed",
            duration=0.05,
            message="AssertionError: assert 1 == 2\nwhere 1 = foo()",
            stdout=None,
            stderr=None,
        )
        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.05,
            results=(result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        assert "FAIL test_bad tests/test_x.py" in output
        # First line of error only
        assert "AssertionError: assert 1 == 2" in output


class TestLlmRendererCollectionErrors:
    """Collection errors: ERROR {path} {message}."""

    def test_collection_error_emitted(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeCollectionErrorEvent(path="tests/bad.py", message="SyntaxError: invalid syntax")
        )

        report = FakeRunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            results=(),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        assert "ERROR tests/bad.py SyntaxError: invalid syntax" in output


class TestLlmRendererDefaultOutput:
    """LlmRenderer defaults output to sys.stdout."""

    def test_defaults_to_stdout(self) -> None:
        import sys
        from unittest.mock import patch

        from rustest.renderers.llm_renderer import LlmRenderer

        fake_stdout = io.StringIO()
        with patch.object(sys, "stdout", fake_stdout):
            renderer = LlmRenderer(verbose=False)
            report = FakeRunReport(
                total=0,
                passed=0,
                failed=0,
                skipped=0,
                duration=0.0,
                results=(),
                collection_errors=(),
            )
            renderer.finalize(report)

        assert fake_stdout.getvalue() == "0 collected\n"


class TestLlmRendererIgnoresUnknownEvents:
    """handle() silently ignores event types it doesn't care about."""

    def test_unknown_event_silently_ignored(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        # These events should be silently ignored
        renderer.handle(FakeCollectionStartedEvent())
        renderer.handle(
            FakeCollectionProgressEvent(file_path="t.py", tests_collected=1, files_collected=1)
        )
        renderer.handle(FakeCollectionCompletedEvent(total_files=1, total_tests=1, duration=0.1))
        renderer.handle(FakeFileStartedEvent(file_path="t.py", total_tests=1))
        renderer.handle(
            FakeFileCompletedEvent(file_path="t.py", duration=0.1, passed=1, failed=0, skipped=0)
        )
        renderer.handle(
            FakeSuiteCompletedEvent(total=1, passed=1, failed=0, skipped=0, errors=0, duration=0.1)
        )

        report = FakeRunReport(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            duration=0.0,
            results=(),
            collection_errors=(),
        )
        renderer.finalize(report)

        # Should still produce valid output without crashing
        assert "0 collected\n" == buf.getvalue()


class TestLlmRendererFailures:
    """Failure output: correct FAIL lines, stdout/stderr, empty messages, ordering."""

    def test_single_failure(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=2))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_ok",
                file_path="t.py",
                test_name="test_ok",
                status="passed",
                duration=0.1,
                message=None,
            )
        )
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_bad",
                file_path="t.py",
                test_name="test_bad",
                status="failed",
                duration=0.2,
                message="assert False",
            )
        )

        failed_result = FakeTestResult(
            name="test_bad",
            path="t.py",
            status="failed",
            duration=0.2,
            message="assert False",
            stdout=None,
            stderr=None,
        )
        report = FakeRunReport(
            total=2,
            passed=1,
            failed=1,
            skipped=0,
            duration=0.3,
            results=(failed_result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.splitlines()
        fail_idx = next(i for i, ln in enumerate(lines) if ln.startswith("FAIL"))
        summary_idx = next(i for i, ln in enumerate(lines) if "passed" in ln and "failed" in ln)
        assert fail_idx < summary_idx
        assert "FAIL test_bad t.py" in output
        assert "1 passed 1 failed 0.3s" in output

    def test_failure_with_stdout_stderr(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_login",
                file_path="t.py",
                test_name="test_login",
                status="failed",
                duration=0.1,
                message="AssertionError: login failed",
            )
        )

        failed_result = FakeTestResult(
            name="test_login",
            path="t.py",
            status="failed",
            duration=0.1,
            message="AssertionError: login failed",
            stdout="Attempting login for user=admin",
            stderr="WARNING: rate limit approaching",
        )
        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(failed_result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.splitlines()
        fail_idx = next(i for i, ln in enumerate(lines) if ln.startswith("FAIL"))
        stdout_idx = next(i for i, ln in enumerate(lines) if "stdout:" in ln)
        stderr_idx = next(i for i, ln in enumerate(lines) if "stderr:" in ln)
        summary_idx = next(
            i
            for i, ln in enumerate(lines)
            if "failed" in ln and "s" in ln and not ln.startswith("FAIL")
        )
        assert fail_idx < stdout_idx < stderr_idx < summary_idx
        assert "Attempting login for user=admin" in output
        assert "WARNING: rate limit approaching" in output

    def test_empty_error_message(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_silent_fail",
                file_path="t.py",
                test_name="test_silent_fail",
                status="failed",
                duration=0.05,
                message=None,
            )
        )

        failed_result = FakeTestResult(
            name="test_silent_fail",
            path="t.py",
            status="failed",
            duration=0.05,
            message=None,
            stdout=None,
            stderr=None,
        )
        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.05,
            results=(failed_result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        assert "FAIL test_silent_fail" in output
        assert "(no message)" in output

    def test_multiple_failures(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=3))
        for i in range(3):
            renderer.handle(
                FakeTestCompletedEvent(
                    test_id=f"t::test_fail_{i}",
                    file_path="t.py",
                    test_name=f"test_fail_{i}",
                    status="failed",
                    duration=0.1,
                    message=f"error {i}",
                )
            )

        results = tuple(
            FakeTestResult(
                name=f"test_fail_{i}",
                path="t.py",
                status="failed",
                duration=0.1,
                message=f"error {i}",
                stdout=None,
                stderr=None,
            )
            for i in range(3)
        )
        report = FakeRunReport(
            total=3,
            passed=0,
            failed=3,
            skipped=0,
            duration=0.3,
            results=results,
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.splitlines()
        fail_lines = [ln for ln in lines if ln.startswith("FAIL")]
        summary_lines = [ln for ln in lines if "failed" in ln and not ln.startswith("FAIL")]
        assert len(fail_lines) == 3
        assert len(summary_lines) == 1
        # All FAIL lines must appear before the summary
        last_fail_idx = max(i for i, ln in enumerate(lines) if ln.startswith("FAIL"))
        summary_idx = next(
            i for i, ln in enumerate(lines) if "failed" in ln and not ln.startswith("FAIL")
        )
        assert last_fail_idx < summary_idx

    def test_failure_with_line_number(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_line_check",
                file_path="t.py",
                test_name="test_line_check",
                status="failed",
                duration=0.1,
                message="AssertionError at line 42: expected True",
            )
        )

        failed_result = FakeTestResult(
            name="test_line_check",
            path="t.py",
            status="failed",
            duration=0.1,
            message="AssertionError at line 42: expected True",
            stdout=None,
            stderr=None,
        )
        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(failed_result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        assert "FAIL test_line_check" in output
        assert "t.py:42" in output


class TestLlmRendererVerboseMode:
    """Verbose mode includes assert lines and where-clauses under failures."""

    def test_verbose_includes_assert_lines(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=True, output=buf)

        message = (
            "AssertionError: assert 1 == 2\n"
            ">  assert result == expected\n"
            "where result = foo()\n"
            "where expected = 2\n"
            "  values: result = 1, expected = 2\n"
        )

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_v",
                file_path="t.py",
                test_name="test_v",
                status="failed",
                duration=0.1,
                message=message,
            )
        )

        result = FakeTestResult(
            name="test_v",
            path="t.py",
            status="failed",
            duration=0.1,
            message=message,
            stdout=None,
            stderr=None,
        )
        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        # In verbose mode, both assert lines and values lines must appear
        assert "  > " in output
        assert "  values: " in output
