"""Minimal, token-efficient renderer for LLM tool consumption.

Produces plain-text output with no ANSI codes, no Unicode decorators,
and no progress spinners — just the signal an LLM needs to act on.
"""

from __future__ import annotations

import re
import sys
from typing import IO, Any


class LlmRenderer:
    """Plain-text event consumer optimised for LLM readability.

    Output format (all lines plain ASCII, no colour codes):

        ERROR {path} {message}          — one per collection error
        FAIL {test_name} {file_path} {first_line_of_error}
          {stdout/stderr if present}
          {verbose: > assert ... / where ... lines}
        {N passed} {N failed} {N skipped} {N error} {duration}s
        0 collected                     — when nothing ran at all
    """

    def __init__(self, *, verbose: bool = False, output: IO[str] | None = None) -> None:
        super().__init__()
        self._verbose = verbose
        self._output: IO[str] = output if output is not None else sys.stdout

        self._passed = 0
        self._failed = 0
        self._skipped = 0

        # (test_name, file_path, message)
        self._failures: list[tuple[str, str, str]] = []
        # (path, message)
        self._collection_errors: list[tuple[str, str]] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def handle(self, event: Any) -> None:  # noqa: ANN401
        """Dispatch an event by class name (accepts Fake* prefixes for tests)."""
        name = type(event).__name__
        if name.endswith("TestCompletedEvent"):
            self._handle_test_completed(event)
        elif name.endswith("CollectionErrorEvent"):
            self._collection_errors.append((event.path, event.message))
        # All other event types are silently ignored.

    def finalize(self, report: Any) -> None:  # noqa: ANN401
        """Emit the final plain-text output block."""
        out = self._output

        # Collection errors
        for path, message in self._collection_errors:
            out.write(f"ERROR {path} {message}\n")

        # Failure detail lines
        # Build a lookup from (name, path) → TestResult for stdout/stderr
        result_lookup: dict[tuple[str, str], Any] = {}
        for result in report.results:
            result_lookup[(result.name, result.path)] = result

        for test_name, file_path, message in self._failures:
            error_line = self._extract_error_line(message) if message else "(no message)"
            line_number = self._extract_line_from_traceback(message) if message else None
            annotated_path = f"{file_path}:{line_number}" if line_number else file_path
            out.write(f"FAIL {test_name} {annotated_path} {error_line}\n")

            # Verbose: assertion detail lines go directly under the FAIL line
            if self._verbose and message:
                for vline in self._extract_verbose_lines(message):
                    out.write(f"{vline}\n")

            # stdout / stderr from the result object — prefix each line
            result = result_lookup.get((test_name, file_path))
            if result is not None:
                if result.stdout:
                    for sline in result.stdout.strip().splitlines():
                        out.write(f"stdout: {sline}\n")
                if result.stderr:
                    for sline in result.stderr.strip().splitlines():
                        out.write(f"stderr: {sline}\n")

        # Summary line
        parts: list[str] = []
        if self._passed > 0:
            parts.append(f"{self._passed} passed")
        if self._failed > 0:
            parts.append(f"{self._failed} failed")
        if self._skipped > 0:
            parts.append(f"{self._skipped} skipped")
        if self._collection_errors:
            parts.append(f"{len(self._collection_errors)} error")

        duration = f"{report.duration:.1f}s"
        if not parts:
            out.write(f"0 collected {duration}\n")
        else:
            out.write(" ".join(parts) + f" {duration}\n")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _handle_test_completed(self, event: Any) -> None:  # noqa: ANN401
        status = event.status
        if status == "passed":
            self._passed += 1
        elif status == "failed":
            self._failed += 1
            message: str = event.message or ""
            self._failures.append((event.test_name, event.file_path, message))
        elif status == "skipped":
            self._skipped += 1

    @staticmethod
    def _extract_error_line(message: str) -> str:
        """Extract the actual error from a traceback message.

        Rustest messages are full Python tracebacks.  The useful error is
        the *last* non-blank line (e.g. ``AssertionError: expected 200``),
        not the first (``Traceback (most recent call last):``).  If a
        ``__RUSTEST_ASSERTION_VALUES__`` block is present, stop before it.
        """
        error_line = "(no message)"
        for raw in message.splitlines():
            stripped = raw.strip()
            if stripped == "__RUSTEST_ASSERTION_VALUES__":
                break
            if stripped and not stripped.startswith("^"):
                error_line = stripped
        return error_line

    @staticmethod
    def _extract_line_from_traceback(message: str) -> str | None:
        """Extract the line number from a Python traceback ``File`` line."""
        match = re.search(r'File ".*", line (\d+)', message)
        return match.group(1) if match else None

    @staticmethod
    def _extract_verbose_lines(full_message: str) -> list[str]:
        """Extract code context and assertion values for verbose output.

        Handles two formats produced by rustest:

        1. **Traceback code lines** — indented ``assert ...`` lines from the
           traceback body are emitted as ``  > {code}``.
        2. **Assertion values** — the ``__RUSTEST_ASSERTION_VALUES__`` block
           with ``Expected:`` / ``Received:`` pairs is emitted as-is
           (indented).  The marker line itself is suppressed.

        Also handles pytest-style ``> assert`` and ``where`` prefixes for
        compatibility.
        """
        lines: list[str] = []
        in_assertion_values = False

        for raw in full_message.splitlines():
            stripped = raw.strip()

            # __RUSTEST_ASSERTION_VALUES__ block
            if stripped == "__RUSTEST_ASSERTION_VALUES__":
                in_assertion_values = True
                continue
            if in_assertion_values:
                if stripped:
                    lines.append(f"  {stripped}")
                continue

            # Traceback code lines (indented assert/code after the File line)
            if raw.startswith("    ") and not stripped.startswith("File "):
                # Skip caret lines (^^^^^^)
                if stripped.startswith("^"):
                    continue
                lines.append(f"  > {stripped}")
                continue

            # pytest-style prefixes (for compatibility)
            if stripped.startswith("> "):
                content = stripped[2:].strip()
                lines.append(f"  > {content}")
            elif stripped.startswith("where "):
                content = stripped[len("where ") :]
                lines.append(f"  values: {content}")

        return lines
