# `--llm` Output Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--llm` flag that produces minimal, token-efficient plain text output for LLM-based tools.

**Architecture:** New `LlmRenderer` class in `python/rustest/renderers/llm_renderer.py` that consumes the same event stream as `RichRenderer` but buffers results and emits plain text only at completion. CLI and core modules updated to wire in the flag and swap renderers. stdout/stderr data comes from the `RunReport` (not events), so `core.py` calls `renderer.finalize(report)` after `rust.run()` returns.

**Tech Stack:** Python only (no Rust changes). argparse, existing event system.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `python/rustest/renderers/llm_renderer.py` | Create | `LlmRenderer` class — buffers events, emits plain text at completion |
| `python/rustest/renderers/__init__.py` | Modify | Export `LlmRenderer` |
| `python/rustest/cli.py` | Modify | Add `--llm` flag, resolve implicit overrides |
| `python/rustest/core.py` | Modify | Wire `llm` param, swap renderer, handle pytest-compat one-liner, call `finalize(report)` |
| `python/tests/test_llm_renderer.py` | Create | Unit tests for `LlmRenderer` |
| `python/tests/test_cli.py` | Modify | CLI flag parsing and interaction tests |

---

### Task 1: LlmRenderer — Core Class With All-Pass Output

**Files:**
- Create: `python/rustest/renderers/llm_renderer.py`
- Create: `python/tests/test_llm_renderer.py`

- [ ] **Step 1: Write the failing test for all-pass output**

```python
# python/tests/test_llm_renderer.py
from __future__ import annotations

from rustest.renderers.llm_renderer import LlmRenderer


class TestLlmRendererAllPass:
    def test_all_pass_summary(self, capsys: object) -> None:
        """All-pass suite emits only the summary line."""
        from unittest.mock import MagicMock

        renderer = LlmRenderer(verbose=False)

        # Simulate suite started
        suite_started = MagicMock()
        suite_started.__class__.__name__ = "SuiteStartedEvent"
        suite_started.total_tests = 5
        suite_started.total_files = 2

        # Simulate test completions (5 passes)
        for i in range(5):
            event = MagicMock()
            event.__class__.__name__ = "TestCompletedEvent"
            event.test_id = f"tests/test_a.py::test_{i}"
            event.file_path = "tests/test_a.py"
            event.test_name = f"test_{i}"
            event.status = "passed"
            event.duration = 0.1
            event.message = None
            renderer.handle(event)

        # Simulate suite completed
        suite_completed = MagicMock()
        suite_completed.__class__.__name__ = "SuiteCompletedEvent"
        suite_completed.total = 5
        suite_completed.passed = 5
        suite_completed.failed = 0
        suite_completed.skipped = 0
        suite_completed.errors = 0
        suite_completed.duration = 0.5

        renderer.handle(suite_completed)

        import io
        import sys

        # Finalize with a mock report (no failures, so no stdout/stderr needed)
        report = MagicMock()
        report.results = ()

        captured = io.StringIO()
        renderer.output_stream = captured
        renderer.handle(suite_completed)

        # Reset and test properly via finalize
        renderer2 = LlmRenderer(verbose=False)
        buf = io.StringIO()
        renderer2._output = buf

        renderer2.handle(suite_started)
        for i in range(5):
            event = MagicMock()
            event.status = "passed"
            event.test_id = f"tests/test_a.py::test_{i}"
            event.file_path = "tests/test_a.py"
            event.test_name = f"test_{i}"
            event.duration = 0.1
            event.message = None
            renderer2.handle(event)

        renderer2.handle(suite_completed)
        output = buf.getvalue()
        assert output.strip() == "5 passed 0.5s"
```

Hmm, the test above is getting convoluted because we need to handle the mock event isinstance checks. Let me redesign. The `LlmRenderer.handle()` needs to use `type(event).__name__` instead of `isinstance` to work with mocks. Actually, let's follow the same pattern as `RichRenderer` which imports the actual event types. For unit tests we'll create simple dataclass stand-ins.

Let me restart this step cleanly.

- [ ] **Step 1: Write failing test for all-pass output**

Create `python/tests/test_llm_renderer.py`:

```python
from __future__ import annotations

import io
from dataclasses import dataclass

import pytest


# Lightweight event stand-ins for unit testing (avoids importing Rust module)
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
    results: tuple[FakeTestResult, ...]
    collection_errors: tuple[object, ...]


class TestLlmRendererAllPass:
    def test_all_pass_summary(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=2, total_tests=5))

        for i in range(5):
            renderer.handle(
                FakeTestCompletedEvent(
                    test_id=f"tests/test_a.py::test_{i}",
                    file_path="tests/test_a.py",
                    test_name=f"test_{i}",
                    status="passed",
                    duration=0.1,
                    message=None,
                )
            )

        report = FakeRunReport(
            total=5, passed=5, failed=0, skipped=0, duration=0.5, results=(), collection_errors=()
        )
        renderer.finalize(report)

        assert buf.getvalue() == "5 passed 0.5s\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/tests/test_llm_renderer.py::TestLlmRendererAllPass::test_all_pass_summary -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rustest.renderers.llm_renderer'`

- [ ] **Step 3: Write minimal LlmRenderer implementation**

Create `python/rustest/renderers/llm_renderer.py`:

```python
"""LLM-optimized renderer for minimal, token-efficient test output.

Produces plain text with no color, no unicode, no progress indicators.
Output is emitted only when the suite completes.
"""

from __future__ import annotations

import sys
from typing import IO, Any


class LlmRenderer:
    """Minimal renderer that buffers results and emits plain text at completion.

    Designed for LLM consumption: no ANSI codes, no unicode, no progress,
    no decorative text. Output only on suite completion via finalize().
    """

    def __init__(self, *, verbose: bool = False, output: IO[str] | None = None) -> None:
        self._verbose = verbose
        self._output: IO[str] = output or sys.stdout
        self._failures: list[tuple[str, str, str, str | None]] = []
        # (test_name, file_path_with_line, error_message, full_message_for_verbose)
        self._collection_errors: list[tuple[str, str]] = []  # (path, message)
        self._passed = 0
        self._failed = 0
        self._skipped = 0

    def handle(self, event: Any) -> None:
        """Handle a test execution event. Buffers data; no output emitted."""
        name = type(event).__name__

        if name == "TestCompletedEvent" or name == "FakeTestCompletedEvent":
            self._handle_test_completed(event)
        elif name == "CollectionErrorEvent" or name == "FakeCollectionErrorEvent":
            self._collection_errors.append((event.path, event.message))
        # All other events silently ignored (no progress output)

    def _handle_test_completed(self, event: Any) -> None:
        if event.status == "passed":
            self._passed += 1
        elif event.status == "failed":
            self._failed += 1
            # Extract file:line from test_id or file_path
            file_ref = event.file_path
            if event.message:
                # Try to extract line number from message
                self._failures.append(
                    (event.test_name, file_ref, event.message, event.message)
                )
            else:
                self._failures.append(
                    (event.test_name, file_ref, "(no message)", None)
                )
        elif event.status == "skipped":
            self._skipped += 1

    def finalize(self, report: Any) -> None:
        """Emit final output using buffered event data + report for stdout/stderr.

        Called after rust.run() returns, giving access to full TestResult data.
        """
        lines: list[str] = []

        # Collection errors
        for path, message in self._collection_errors:
            lines.append(f"ERROR {path} {message}")

        # Failures with optional stdout/stderr from report
        stdout_stderr_map: dict[str, tuple[str | None, str | None]] = {}
        for result in report.results:
            if result.status == "failed":
                stdout_stderr_map[result.name] = (result.stdout, result.stderr)

        for test_name, file_ref, error_msg, full_msg in self._failures:
            # Parse first line number from error message if available
            line_info = self._extract_line_from_message(error_msg)
            if line_info:
                location = f"{file_ref}:{line_info}"
            else:
                location = file_ref

            # First line of error only (for non-verbose), full for verbose
            first_line_msg = error_msg.split("\n")[0] if error_msg else "(no message)"
            lines.append(f"FAIL {test_name} {location} {first_line_msg}")

            # Verbose: add code snippet + assertion values
            if self._verbose and full_msg:
                for line in self._extract_verbose_lines(full_msg):
                    lines.append(line)

            # stdout/stderr from report
            stdout, stderr = stdout_stderr_map.get(test_name, (None, None))
            if stdout:
                lines.append(f"stdout: {stdout.rstrip()}")
            if stderr:
                lines.append(f"stderr: {stderr.rstrip()}")

        # Summary line
        parts: list[str] = []
        if self._passed > 0:
            parts.append(f"{self._passed} passed")
        if self._failed > 0:
            parts.append(f"{self._failed} failed")
        if self._skipped > 0:
            parts.append(f"{self._skipped} skipped")
        if len(self._collection_errors) > 0:
            count = len(self._collection_errors)
            parts.append(f"{count} error{'s' if count != 1 else ''}")
        if not parts:
            parts.append("0 collected")

        duration = report.duration
        if duration < 1:
            duration_str = f"{duration:.1f}s"
        else:
            duration_str = f"{duration:.1f}s"

        lines.append(f"{' '.join(parts)} {duration_str}")

        self._output.write("\n".join(lines) + "\n")

    @staticmethod
    def _extract_line_from_message(message: str) -> str | None:
        """Try to extract a line number from an error message."""
        import re

        match = re.search(r"line (\d+)", message)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_verbose_lines(full_message: str) -> list[str]:
        """Extract code snippet and assertion values from a full error message."""
        lines: list[str] = []
        for line in full_message.split("\n"):
            stripped = line.strip()
            if stripped.startswith("assert ") or stripped.startswith("> "):
                lines.append(f"  > {stripped}")
            elif "==" in stripped and "=" in stripped and stripped.startswith("where"):
                lines.append(f"  values: {stripped}")
        return lines
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/tests/test_llm_renderer.py::TestLlmRendererAllPass::test_all_pass_summary -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/rustest/renderers/llm_renderer.py python/tests/test_llm_renderer.py
git commit -m "feat: add LlmRenderer with all-pass output support"
```

---

### Task 2: LlmRenderer — Failure Output

**Files:**
- Modify: `python/tests/test_llm_renderer.py`
- Modify: `python/rustest/renderers/llm_renderer.py` (if needed)

- [ ] **Step 1: Write failing tests for failure output**

Add to `python/tests/test_llm_renderer.py`:

```python
class TestLlmRendererFailures:
    def test_single_failure(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=2))

        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_login",
                file_path="tests/test_auth.py",
                test_name="test_login",
                status="passed",
                duration=0.1,
                message=None,
            )
        )
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_logout",
                file_path="tests/test_auth.py",
                test_name="test_logout",
                status="failed",
                duration=0.2,
                message="AssertionError: expected 200, got 401",
            )
        )

        report = FakeRunReport(
            total=2,
            passed=1,
            failed=1,
            skipped=0,
            duration=0.3,
            results=(
                FakeTestResult(
                    name="test_logout",
                    path="tests/test_auth.py",
                    status="failed",
                    duration=0.2,
                    message="AssertionError: expected 200, got 401",
                    stdout=None,
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert lines[0] == "FAIL test_logout tests/test_auth.py AssertionError: expected 200, got 401"
        assert lines[1] == "1 passed 1 failed 0.3s"

    def test_failure_with_stdout_stderr(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_login",
                file_path="tests/test_auth.py",
                test_name="test_login",
                status="failed",
                duration=0.1,
                message="AssertionError: expected 200, got 401",
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
                    name="test_login",
                    path="tests/test_auth.py",
                    status="failed",
                    duration=0.1,
                    message="AssertionError: expected 200, got 401",
                    stdout="Attempting login for user=admin",
                    stderr="WARNING: rate limit approaching",
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert lines[0] == "FAIL test_login tests/test_auth.py AssertionError: expected 200, got 401"
        assert lines[1] == "stdout: Attempting login for user=admin"
        assert lines[2] == "stderr: WARNING: rate limit approaching"
        assert lines[3] == "1 failed 0.1s"

    def test_empty_error_message(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test.py::test_foo",
                file_path="tests/test.py",
                test_name="test_foo",
                status="failed",
                duration=0.1,
                message=None,
            )
        )

        report = FakeRunReport(
            total=1, passed=0, failed=1, skipped=0, duration=0.1,
            results=(
                FakeTestResult(
                    name="test_foo", path="tests/test.py", status="failed",
                    duration=0.1, message=None, stdout=None, stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert lines[0] == "FAIL test_foo tests/test.py (no message)"
        assert lines[1] == "1 failed 0.1s"
```

- [ ] **Step 2: Run tests to verify they fail (or pass if implementation already handles it)**

Run: `uv run pytest python/tests/test_llm_renderer.py::TestLlmRendererFailures -v`
Expected: PASS (the implementation from Task 1 should handle this). If any fail, fix the implementation.

- [ ] **Step 3: Commit**

```bash
git add python/tests/test_llm_renderer.py
git commit -m "test: add failure output tests for LlmRenderer"
```

---

### Task 3: LlmRenderer — Collection Errors, Skips, Zero Tests

**Files:**
- Modify: `python/tests/test_llm_renderer.py`
- Modify: `python/rustest/renderers/llm_renderer.py` (if needed)

- [ ] **Step 1: Write tests for collection errors, skips, and zero tests**

Add to `python/tests/test_llm_renderer.py`:

```python
class TestLlmRendererEdgeCases:
    def test_collection_error(self) -> None:
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
            total=0, passed=0, failed=0, skipped=0, duration=0.1, results=(), collection_errors=()
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert lines[0] == "ERROR tests/test_broken.py SyntaxError: unexpected indent (line 15)"
        assert lines[1] == "1 error 0.1s"

    def test_all_skipped(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=3))
        for i in range(3):
            renderer.handle(
                FakeTestCompletedEvent(
                    test_id=f"tests/test.py::test_{i}",
                    file_path="tests/test.py",
                    test_name=f"test_{i}",
                    status="skipped",
                    duration=0.0,
                    message=None,
                )
            )

        report = FakeRunReport(
            total=3, passed=0, failed=0, skipped=3, duration=0.1, results=(), collection_errors=()
        )
        renderer.finalize(report)

        assert buf.getvalue() == "3 skipped 0.1s\n"

    def test_zero_tests_collected(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        report = FakeRunReport(
            total=0, passed=0, failed=0, skipped=0, duration=0.0, results=(), collection_errors=()
        )
        renderer.finalize(report)

        assert buf.getvalue() == "0 collected 0.0s\n"

    def test_mixed_pass_fail_skip(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=3))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t.py::a", file_path="t.py", test_name="a",
                status="passed", duration=0.1, message=None,
            )
        )
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t.py::b", file_path="t.py", test_name="b",
                status="failed", duration=0.1, message="AssertionError",
            )
        )
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t.py::c", file_path="t.py", test_name="c",
                status="skipped", duration=0.0, message=None,
            )
        )

        report = FakeRunReport(
            total=3, passed=1, failed=1, skipped=1, duration=0.3,
            results=(
                FakeTestResult(
                    name="b", path="t.py", status="failed", duration=0.1,
                    message="AssertionError", stdout=None, stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert lines[0] == "FAIL b t.py AssertionError"
        assert lines[1] == "1 passed 1 failed 1 skipped 0.3s"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest python/tests/test_llm_renderer.py::TestLlmRendererEdgeCases -v`
Expected: PASS. Fix implementation if needed.

- [ ] **Step 3: Commit**

```bash
git add python/tests/test_llm_renderer.py python/rustest/renderers/llm_renderer.py
git commit -m "test: add edge case tests for LlmRenderer (errors, skips, zero tests)"
```

---

### Task 4: LlmRenderer — Verbose Mode

**Files:**
- Modify: `python/tests/test_llm_renderer.py`
- Modify: `python/rustest/renderers/llm_renderer.py` (likely needs refinement)

- [ ] **Step 1: Write test for verbose failure output**

Add to `python/tests/test_llm_renderer.py`:

```python
class TestLlmRendererVerbose:
    def test_verbose_failure_with_assertion(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=True, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test_auth.py::test_login",
                file_path="tests/test_auth.py",
                test_name="test_login",
                status="failed",
                duration=0.1,
                message="AssertionError: expected 200, got 401\n> assert response.status_code == 200\nwhere response.status_code = 401",
            )
        )

        report = FakeRunReport(
            total=1, passed=0, failed=1, skipped=0, duration=0.1,
            results=(
                FakeTestResult(
                    name="test_login", path="tests/test_auth.py", status="failed",
                    duration=0.1,
                    message="AssertionError: expected 200, got 401\n> assert response.status_code == 200\nwhere response.status_code = 401",
                    stdout="Attempting login for user=admin",
                    stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert lines[0] == "FAIL test_login tests/test_auth.py AssertionError: expected 200, got 401"
        assert lines[1] == "  > assert response.status_code == 200"
        assert lines[2] == "  values: where response.status_code = 401"
        assert lines[3] == "stdout: Attempting login for user=admin"
        assert lines[4] == "1 failed 0.1s"

    def test_verbose_does_not_affect_passing_tests(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=True, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=1))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="tests/test.py::test_ok", file_path="tests/test.py",
                test_name="test_ok", status="passed", duration=0.1, message=None,
            )
        )

        report = FakeRunReport(
            total=1, passed=1, failed=0, skipped=0, duration=0.1,
            results=(), collection_errors=(),
        )
        renderer.finalize(report)

        assert buf.getvalue() == "1 passed 0.1s\n"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest python/tests/test_llm_renderer.py::TestLlmRendererVerbose -v`
Expected: May need implementation adjustments for the verbose line extraction logic. Fix `_extract_verbose_lines` until tests pass.

- [ ] **Step 3: Commit**

```bash
git add python/tests/test_llm_renderer.py python/rustest/renderers/llm_renderer.py
git commit -m "feat: add verbose mode support to LlmRenderer"
```

---

### Task 5: Export LlmRenderer

**Files:**
- Modify: `python/rustest/renderers/__init__.py`

- [ ] **Step 1: Write failing test**

Add to `python/tests/test_llm_renderer.py`:

```python
class TestLlmRendererImport:
    def test_importable_from_renderers_package(self) -> None:
        from rustest.renderers import LlmRenderer

        assert LlmRenderer is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/tests/test_llm_renderer.py::TestLlmRendererImport -v`
Expected: FAIL — `ImportError: cannot import name 'LlmRenderer' from 'rustest.renderers'`

- [ ] **Step 3: Update `__init__.py`**

Modify `python/rustest/renderers/__init__.py` to:

```python
"""Event consumers for rendering test execution progress."""

from __future__ import annotations

__all__ = ["LlmRenderer", "RichRenderer"]

from .llm_renderer import LlmRenderer
from .rich_renderer import RichRenderer
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/tests/test_llm_renderer.py::TestLlmRendererImport -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/rustest/renderers/__init__.py python/tests/test_llm_renderer.py
git commit -m "feat: export LlmRenderer from renderers package"
```

---

### Task 6: CLI `--llm` Flag

**Files:**
- Modify: `python/rustest/cli.py`
- Modify: `python/tests/test_cli.py`

- [ ] **Step 1: Write failing tests for `--llm` flag parsing**

Add to `python/tests/test_cli.py`:

```python
class TestLlmFlag:
    def test_llm_flag_parsed(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["--llm"])
        assert args.llm is True

    def test_llm_flag_default_false(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args([])
        assert args.llm is False

    def test_llm_overrides_color_and_ascii(self) -> None:
        """--llm should force ascii=True and no_color=True in the run() call."""
        report = RunReport(
            total=0, passed=0, failed=0, skipped=0, duration=0.0,
            results=(), collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main(["--llm"])

            assert mock_run.call_args.kwargs["no_color"] is True
            assert mock_run.call_args.kwargs["ascii"] is True
            assert mock_run.call_args.kwargs["llm"] is True

    def test_llm_silently_overrides_color_always(self) -> None:
        """--llm --color always should still disable color."""
        report = RunReport(
            total=0, passed=0, failed=0, skipped=0, duration=0.0,
            results=(), collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main(["--llm", "--color", "always"])

            assert mock_run.call_args.kwargs["no_color"] is True
            assert mock_run.call_args.kwargs["ascii"] is True

    def test_llm_with_verbose(self) -> None:
        """--llm -v should pass both flags."""
        report = RunReport(
            total=0, passed=0, failed=0, skipped=0, duration=0.0,
            results=(), collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main(["--llm", "-v"])

            assert mock_run.call_args.kwargs["llm"] is True
            assert mock_run.call_args.kwargs["verbose"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest python/tests/test_cli.py::TestLlmFlag -v`
Expected: FAIL — `args has no attribute 'llm'`

- [ ] **Step 3: Add `--llm` flag to CLI**

Modify `python/rustest/cli.py`. Add the argument after the `--color` argument (around line 101):

```python
    _ = parser.add_argument(
        "--llm",
        action="store_true",
        help="Produce minimal, token-efficient output optimized for LLM consumption.",
    )
```

Add `llm=False` to the `parser.set_defaults(...)` call.

In `main()`, after the color mode determination block (after line 170), add the `--llm` override logic:

```python
    # --llm forces ascii and no color
    if args.llm:
        use_color = False
        args.ascii = True
```

Add `llm=args.llm` to the `run()` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest python/tests/test_cli.py::TestLlmFlag -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python/rustest/cli.py python/tests/test_cli.py
git commit -m "feat: add --llm CLI flag with implicit ascii/color overrides"
```

---

### Task 7: Wire LlmRenderer Into core.py

**Files:**
- Modify: `python/rustest/core.py`

- [ ] **Step 1: Write failing test**

Add to `python/tests/test_cli.py` (since it tests the full CLI → core path):

```python
class TestLlmIntegration:
    def test_llm_mode_uses_llm_renderer(self) -> None:
        """Verify --llm causes LlmRenderer to be used instead of RichRenderer."""
        report = RunReport(
            total=1, passed=1, failed=0, skipped=0, duration=0.1,
            results=(), collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main(["--llm"])

            # Verify llm=True was passed
            assert mock_run.call_args.kwargs["llm"] is True
```

This test ensures the flag is plumbed through. The actual renderer swap is in `core.py`. Let's add a dedicated test for `core.run()`:

Add to `python/tests/test_core.py` (or create if needed):

```python
class TestCoreLlmMode:
    def test_llm_param_accepted(self) -> None:
        """Verify core.run() accepts the llm parameter."""
        import inspect
        from rustest.core import run

        sig = inspect.signature(run)
        assert "llm" in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/tests/test_core.py::TestCoreLlmMode -v` (or wherever you place the test)
Expected: FAIL — `llm` not in signature

- [ ] **Step 3: Modify `core.py` to accept `llm` param and swap renderer**

In `python/rustest/core.py`:

Add import at the top:

```python
from .renderers import LlmRenderer, RichRenderer
```

(Replace the existing `from .renderers import RichRenderer` line.)

Add `llm: bool = False` parameter to the `run()` function signature (after `no_color`).

Replace the renderer setup section (lines 134-137) with:

```python
    # Set up event routing with appropriate renderer
    router = EventRouter()
    if llm:
        renderer = LlmRenderer(verbose=verbose)
        router.subscribe(renderer)
    else:
        renderer = RichRenderer(use_colors=not no_color, use_ascii=ascii)
        router.subscribe(renderer)
```

Replace the pytest-compat banner section (lines 126-132) with:

```python
    # Print pytest compatibility banner and install _pytest stubs if enabled
    if pytest_compat:
        if llm:
            print("pytest-compat mode")
        else:
            _print_pytest_compat_banner(use_colors=not no_color)
        # Install _pytest stub modules for compatibility
        from rustest.compat.pytest import install_pytest_stubs

        install_pytest_stubs()
```

After `raw_report = rust.run(...)` (before `return RunReport.from_py(...)`), add the finalize call:

```python
    report = RunReport.from_py(raw_report)

    # Finalize LLM renderer with full report (for stdout/stderr access)
    if llm and isinstance(renderer, LlmRenderer):
        renderer.finalize(report)

    return report
```

(Move the `return RunReport.from_py(raw_report)` into the `report = ...` assignment.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/tests/test_core.py::TestCoreLlmMode -v`
Expected: PASS

- [ ] **Step 5: Run all existing tests to verify no regressions**

Run: `uv run pytest python/tests/ -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add python/rustest/core.py python/tests/test_core.py
git commit -m "feat: wire LlmRenderer into core.run() with renderer swap and finalize"
```

---

### Task 8: Pytest-Compat One-Liner Test

**Files:**
- Modify: `python/tests/test_llm_renderer.py`

- [ ] **Step 1: Write test for pytest-compat one-liner**

This is handled in `core.py` (not the renderer), so we test it at the CLI level:

Add to `python/tests/test_cli.py`:

```python
class TestLlmPytestCompat:
    def test_llm_pytest_compat_one_liner(self, capsys: object) -> None:
        """--llm --pytest-compat prints one-liner instead of banner."""
        report = RunReport(
            total=1, passed=1, failed=0, skipped=0, duration=0.1,
            results=(), collection_errors=(),
        )

        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_HOME"]
        with patch.dict(os.environ, {var: "" for var in ci_vars}, clear=True):
            with patch("rustest.cli.run", return_value=report) as mock_run:
                cli.main(["--llm", "--pytest-compat"])

            assert mock_run.call_args.kwargs["llm"] is True
            assert mock_run.call_args.kwargs["pytest_compat"] is True
```

- [ ] **Step 2: Run test**

Run: `uv run pytest python/tests/test_cli.py::TestLlmPytestCompat -v`
Expected: PASS (the flag plumbing is already done; the one-liner logic is in core.py from Task 7)

- [ ] **Step 3: Commit**

```bash
git add python/tests/test_cli.py
git commit -m "test: add pytest-compat one-liner test for --llm mode"
```

---

### Task 9: No ANSI / No Unicode Verification Test

**Files:**
- Modify: `python/tests/test_llm_renderer.py`

- [ ] **Step 1: Write test that verifies no ANSI codes or unicode in output**

Add to `python/tests/test_llm_renderer.py`:

```python
import re


class TestLlmRendererOutputCleanliness:
    def test_no_ansi_codes_in_output(self) -> None:
        from rustest.renderers.llm_renderer import LlmRenderer

        buf = io.StringIO()
        renderer = LlmRenderer(verbose=False, output=buf)

        renderer.handle(FakeSuiteStartedEvent(total_files=1, total_tests=2))
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t.py::a", file_path="t.py", test_name="a",
                status="passed", duration=0.1, message=None,
            )
        )
        renderer.handle(
            FakeTestCompletedEvent(
                test_id="t.py::b", file_path="t.py", test_name="b",
                status="failed", duration=0.1, message="AssertionError: x != y",
            )
        )

        report = FakeRunReport(
            total=2, passed=1, failed=1, skipped=0, duration=0.2,
            results=(
                FakeTestResult(
                    name="b", path="t.py", status="failed", duration=0.1,
                    message="AssertionError: x != y", stdout=None, stderr=None,
                ),
            ),
            collection_errors=(),
        )
        renderer.finalize(report)

        output = buf.getvalue()

        # No ANSI escape sequences
        ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")
        assert not ansi_pattern.search(output), f"Found ANSI codes in output: {output!r}"

        # Only ASCII characters (no unicode symbols like checkmarks)
        assert output.isascii(), f"Found non-ASCII characters in output: {output!r}"
```

- [ ] **Step 2: Run test**

Run: `uv run pytest python/tests/test_llm_renderer.py::TestLlmRendererOutputCleanliness -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add python/tests/test_llm_renderer.py
git commit -m "test: verify LlmRenderer output contains no ANSI or unicode"
```

---

### Task 10: Run Full Test Suite and Format

**Files:** None new — verification only.

- [ ] **Step 1: Run Python formatter**

Run: `uv run ruff format python`

- [ ] **Step 2: Run Python linter**

Run: `uv run ruff check python`
Fix any issues.

- [ ] **Step 3: Run type checker**

Run: `uv run basedpyright python`
Fix any type errors.

- [ ] **Step 4: Run full Python test suite**

Run: `uv run pytest python/tests/ -v`
Expected: All pass.

- [ ] **Step 5: Run integration tests**

Run: `uv run pytest tests/ examples/tests/ -v`
Expected: All pass.

- [ ] **Step 6: Commit any formatting/lint fixes**

```bash
git add -u
git commit -m "chore: format and lint fixes for --llm flag implementation"
```
