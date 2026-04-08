"""Tests for LlmRenderer — minimal, token-efficient output for LLM tools."""

from __future__ import annotations

import io
import re
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

        assert buf.getvalue() == "0 collected 0.0s\n"

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
                message=(
                    "Traceback (most recent call last):\n"
                    '  File "tests/test_x.py", line 5, in test_bad\n'
                    "    assert 1 == 2\n"
                    "           ^^^^^^\n"
                    "AssertionError: assert 1 == 2"
                ),
            )
        )

        result = FakeTestResult(
            name="test_bad",
            path="tests/test_x.py",
            status="failed",
            duration=0.05,
            message=(
                "Traceback (most recent call last):\n"
                '  File "tests/test_x.py", line 5, in test_bad\n'
                "    assert 1 == 2\n"
                "           ^^^^^^\n"
                "AssertionError: assert 1 == 2"
            ),
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
        assert "FAIL test_bad tests/test_x.py:5" in output
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

        assert fake_stdout.getvalue() == "0 collected 0.0s\n"


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
        assert "0 collected 0.0s\n" == buf.getvalue()


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
                message=(
                    "Traceback (most recent call last):\n"
                    '  File "t.py", line 42, in test_line_check\n'
                    "    assert result is True\n"
                    "AssertionError"
                ),
            )
        )

        failed_result = FakeTestResult(
            name="test_line_check",
            path="t.py",
            status="failed",
            duration=0.1,
            message=(
                "Traceback (most recent call last):\n"
                '  File "t.py", line 42, in test_line_check\n'
                "    assert result is True\n"
                "AssertionError"
            ),
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

    def test_verbose_failure_with_assertion(self) -> None:
        """Full integration: FAIL, verbose lines, stdout, summary in correct order."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=True, output=buf)

        message = (
            "Traceback (most recent call last):\n"
            '  File "tests/test_auth.py", line 42, in test_login\n'
            "    assert response.status_code == 200\n"
            "           ^^^^^^^^^^^^^^^^^^^^^^^^^\n"
            "AssertionError: expected 200, got 401\n"
            "\n"
            "__RUSTEST_ASSERTION_VALUES__\n"
            "Expected: 200\n"
            "Received: 401"
        )

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_login",
                file_path="tests/test_auth.py",
                test_name="test_login",
                status="failed",
                duration=0.1,
                message=message,
            )
        )

        result = FakeTestResult(
            name="test_login",
            path="tests/test_auth.py",
            status="failed",
            duration=0.1,
            message=message,
            stdout="Attempting login for user=admin",
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
        lines = output.splitlines()

        # Locate each expected line
        fail_idx = next(i for i, ln in enumerate(lines) if ln.startswith("FAIL"))
        assert_idx = next(i for i, ln in enumerate(lines) if "assert response" in ln)
        values_idx = next(i for i, ln in enumerate(lines) if "Expected:" in ln)
        stdout_idx = next(i for i, ln in enumerate(lines) if "stdout:" in ln)
        summary_idx = next(
            i for i, ln in enumerate(lines) if "failed" in ln and not ln.startswith("FAIL")
        )

        # Exact ordering: FAIL → verbose code → verbose values → stdout → summary
        assert fail_idx < assert_idx < values_idx < stdout_idx < summary_idx

        # Content checks
        assert "AssertionError: expected 200, got 401" in lines[fail_idx]
        assert "assert response.status_code == 200" in lines[assert_idx]
        assert "Expected: 200" in lines[values_idx]
        assert "Attempting login for user=admin" in lines[stdout_idx]
        assert "1 failed" in lines[summary_idx]

    def test_verbose_does_not_affect_passing(self) -> None:
        """verbose=True with all passing tests: output is just the summary line."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=True, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=3))
        for i in range(3):
            renderer.handle(
                FakeTestCompletedEvent(
                    test_id=f"t::test_pass_{i}",
                    file_path="t.py",
                    test_name=f"test_pass_{i}",
                    status="passed",
                    duration=0.1,
                    message=None,
                )
            )

        report = FakeRunReport(
            total=3,
            passed=3,
            failed=0,
            skipped=0,
            duration=0.3,
            results=(),
            collection_errors=(),
        )
        renderer.finalize(report)

        assert buf.getvalue() == "3 passed 0.3s\n"

    def test_non_verbose_omits_code_snippets(self) -> None:
        """verbose=False: assert lines and where-clause values are NOT shown."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        message = (
            "AssertionError: expected 200, got 401\n"
            ">  assert response.status_code == 200\n"
            "where response.status_code = 401"
        )

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_login",
                file_path="tests/test_auth.py",
                test_name="test_login",
                status="failed",
                duration=0.1,
                message=message,
            )
        )

        result = FakeTestResult(
            name="test_login",
            path="tests/test_auth.py",
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
        assert "  > " not in output
        assert "  values: " not in output


class TestLlmRendererEdgeCases:
    """Edge-case coverage: collection errors, all-skip, zero tests, mixed counts."""

    def test_collection_error(self) -> None:
        """Collection error with no test events: ERROR line + '1 error' in summary."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeCollectionErrorEvent(
                path="tests/test_broken.py",
                message="SyntaxError: unexpected indent (line 15)",
            )
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
        lines = output.splitlines()
        assert lines[0] == "ERROR tests/test_broken.py SyntaxError: unexpected indent (line 15)"
        assert "1 error" in output

    def test_all_skipped(self) -> None:
        """Three skipped tests produce exactly '3 skipped 0.1s\\n'."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=3))
        for i in range(3):
            renderer.handle(
                FakeTestCompletedEvent(
                    test_id=f"t::test_skip_{i}",
                    file_path="t.py",
                    test_name=f"test_skip_{i}",
                    status="skipped",
                    duration=0.0,
                    message=None,
                )
            )

        report = FakeRunReport(
            total=3,
            passed=0,
            failed=0,
            skipped=3,
            duration=0.1,
            results=(),
            collection_errors=(),
        )
        renderer.finalize(report)

        assert buf.getvalue() == "3 skipped 0.1s\n"

    def test_zero_tests_collected(self) -> None:
        """No events at all: output is '0 collected\\n'."""
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

        assert buf.getvalue() == "0 collected 0.0s\n"

    def test_mixed_pass_fail_skip(self) -> None:
        """1 passed, 1 failed, 1 skipped: FAIL line present and summary correct."""
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
                message="AssertionError: wrong value",
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
            message="AssertionError: wrong value",
            stdout=None,
            stderr=None,
        )
        report = FakeRunReport(
            total=3,
            passed=1,
            failed=1,
            skipped=1,
            duration=0.3,
            results=(failed_result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.splitlines()
        fail_lines = [ln for ln in lines if ln.startswith("FAIL")]
        assert len(fail_lines) == 1
        assert "FAIL test_fail t.py" in output
        assert "1 passed 1 failed 1 skipped 0.3s" in output

    def test_collection_error_with_failures(self) -> None:
        """A collection error alongside a test failure: ERROR before FAIL, both in summary."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeCollectionErrorEvent(
                path="tests/test_broken.py",
                message="SyntaxError: invalid syntax",
            )
        )
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t::test_bad",
                file_path="t.py",
                test_name="test_bad",
                status="failed",
                duration=0.1,
                message="AssertionError: oops",
            )
        )

        failed_result = FakeTestResult(
            name="test_bad",
            path="t.py",
            status="failed",
            duration=0.1,
            message="AssertionError: oops",
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
        lines = output.splitlines()
        error_idx = next(i for i, ln in enumerate(lines) if ln.startswith("ERROR"))
        fail_idx = next(i for i, ln in enumerate(lines) if ln.startswith("FAIL"))
        assert error_idx < fail_idx
        assert "1 failed" in output
        assert "1 error" in output


# ---------------------------------------------------------------------------
# Real rustest message format constants (captured from actual rustest output)
# ---------------------------------------------------------------------------

REAL_TRACEBACK_SIMPLE = (
    "Traceback (most recent call last):\n"
    '  File "tests/test_auth.py", line 5, in test_fail_simple\n'
    "    assert 1 == 2\n"
    "           ^^^^^^\n"
    "AssertionError"
)

REAL_TRACEBACK_WITH_MSG = (
    "Traceback (most recent call last):\n"
    '  File "tests/test_auth.py", line 9, in test_fail_with_message\n'
    '    assert result["status"] == 200, f"expected 200, got {result[\'status\']}"\n'
    "           ^^^^^^^^^^^^^^^^^^^^^^^\n"
    "AssertionError: expected 200, got 401"
)

REAL_TRACEBACK_WITH_VALUES = (
    "Traceback (most recent call last):\n"
    '  File "tests/test_math.py", line 13, in test_broken\n'
    "    assert x == y\n"
    "           ^^^^^^\n"
    "AssertionError\n"
    "\n"
    "__RUSTEST_ASSERTION_VALUES__\n"
    "Expected: 20\n"
    "Received: 10"
)

REAL_TRACEBACK_LONG_PATH = (
    "Traceback (most recent call last):\n"
    '  File "\\\\?\\C:\\Users\\dev\\project\\tests\\test_auth.py", line 42, in test_login\n'
    "    assert response.status_code == 200\n"
    "           ^^^^^^^^^^^^^^^^^^^^^^^^^\n"
    "AssertionError"
)


class TestLlmRendererRealMessages:
    """Regression tests using actual rustest traceback message formats.

    These tests use the real message format produced by rustest's Rust core,
    which is a full Python traceback string, NOT a simple error message.
    """

    def test_fail_line_extracts_error_not_traceback_header(self) -> None:
        """FAIL line should show the actual error, not 'Traceback (most recent call last):'."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_fail_simple",
                file_path="tests/test_auth.py",
                test_name="test_fail_simple",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_SIMPLE,
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_fail_simple",
                    path="tests/test_auth.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_SIMPLE,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        # Must NOT start with "Traceback"
        assert "Traceback" not in lines[0], (
            f"FAIL line should show the error, not traceback header: {lines[0]}"
        )
        # Must contain the actual error type
        assert "AssertionError" in lines[0]

    def test_fail_line_includes_error_message_when_present(self) -> None:
        """FAIL line should include 'AssertionError: expected 200, got 401'."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_fail_with_message",
                file_path="tests/test_auth.py",
                test_name="test_fail_with_message",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_WITH_MSG,
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_fail_with_message",
                    path="tests/test_auth.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_WITH_MSG,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert "AssertionError: expected 200, got 401" in lines[0]

    def test_line_number_extracted_from_traceback(self) -> None:
        """Line number should come from the File line in the traceback, not just any 'line N'."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_fail_simple",
                file_path="tests/test_auth.py",
                test_name="test_fail_simple",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_SIMPLE,
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_fail_simple",
                    path="tests/test_auth.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_SIMPLE,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        # Should extract line 5 from the traceback's File line
        assert "tests/test_auth.py:5" in output

    def test_verbose_extracts_rustest_assertion_values(self) -> None:
        """Verbose mode should extract __RUSTEST_ASSERTION_VALUES__ block."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=True, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_math.py::test_broken",
                file_path="tests/test_math.py",
                test_name="test_broken",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_WITH_VALUES,
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_broken",
                    path="tests/test_math.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_WITH_VALUES,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        # Should contain the assertion code line
        assert "assert x == y" in output
        # Should contain the expected/received values
        assert "Expected: 20" in output
        assert "Received: 10" in output
        # Should NOT contain the __RUSTEST_ASSERTION_VALUES__ marker itself
        assert "__RUSTEST_ASSERTION_VALUES__" not in output

    def test_verbose_extracts_code_from_traceback(self) -> None:
        """Verbose mode should show the failing assert line from the traceback."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=True, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_fail_simple",
                file_path="tests/test_auth.py",
                test_name="test_fail_simple",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_SIMPLE,
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_fail_simple",
                    path="tests/test_auth.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_SIMPLE,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        # Should contain the failing code line from the traceback
        assert "assert 1 == 2" in output

    def test_non_verbose_omits_traceback_details(self) -> None:
        """Non-verbose mode should NOT include traceback details or assertion values."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_math.py::test_broken",
                file_path="tests/test_math.py",
                test_name="test_broken",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_WITH_VALUES,
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_broken",
                    path="tests/test_math.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_WITH_VALUES,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        # Should only be 2 lines: FAIL + summary
        lines = output.strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("FAIL")
        assert "Expected:" not in output
        assert "Received:" not in output

    def test_stdout_lines_clearly_prefixed(self) -> None:
        """Each line of captured stdout should be prefixed with 'stdout:'."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test.py::test_prints",
                file_path="tests/test.py",
                test_name="test_prints",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_WITH_MSG,
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_prints",
                    path="tests/test.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_WITH_MSG,
                    stdout="debug: starting auth\ndebug: user=admin\n",
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        # Find stdout lines - each should be clearly prefixed
        stdout_lines = [line for line in lines if "debug:" in line]
        for sl in stdout_lines:
            assert sl.startswith("stdout:"), f"stdout line not prefixed: {sl!r}"


class TestLlmRendererBareAssertionError:
    """Bare 'AssertionError' (no message) should include the failing code line."""

    def test_bare_assertion_includes_code(self) -> None:
        """FAIL line for bare AssertionError should append the failing code."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test.py::test_bare",
                file_path="tests/test.py",
                test_name="test_bare",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_SIMPLE,  # ends with bare "AssertionError"
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_bare",
                    path="tests/test.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_SIMPLE,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        fail_line = output.strip().split("\n")[0]
        # Should NOT just say "AssertionError" — should include the code
        assert "assert 1 == 2" in fail_line, (
            f"Bare AssertionError should include failing code: {fail_line}"
        )

    def test_assertion_with_message_unchanged(self) -> None:
        """AssertionError WITH a message should not have code appended."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test.py::test_msg",
                file_path="tests/test.py",
                test_name="test_msg",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_WITH_MSG,  # ends with "AssertionError: expected 200, got 401"
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_msg",
                    path="tests/test.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_WITH_MSG,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        fail_line = output.strip().split("\n")[0]
        assert "AssertionError: expected 200, got 401" in fail_line

    def test_bare_assertion_with_values_includes_values(self) -> None:
        """Bare AssertionError with __RUSTEST_ASSERTION_VALUES__ should show expected/received."""
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test.py::test_cmp",
                file_path="tests/test.py",
                test_name="test_cmp",
                status="failed",
                duration=0.1,
                message=REAL_TRACEBACK_WITH_VALUES,  # bare AssertionError + Expected/Received
            )
        )

        report = FakeRunReport(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            duration=0.1,
            results=(
                FakeTestResult(
                    name="test_cmp",
                    path="tests/test.py",
                    status="failed",
                    duration=0.1,
                    message=REAL_TRACEBACK_WITH_VALUES,
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        fail_line = output.strip().split("\n")[0]
        # Should show expected vs received inline
        assert "20" in fail_line and "10" in fail_line, (
            f"Bare AssertionError with values should include expected/received: {fail_line}"
        )


class TestLlmRendererOutputCleanliness:
    """LlmRenderer output contains no ANSI escape codes and no non-ASCII characters."""

    def test_no_ansi_codes_in_output(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=2))
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
            total=2,
            passed=1,
            failed=1,
            skipped=0,
            duration=0.2,
            results=(failed_result,),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        assert not re.search(r"\x1b\[[0-9;]*m", output), "ANSI escape codes found in output"
        assert output.isascii(), "Non-ASCII characters found in output"
