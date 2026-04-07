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
            first_line = message.splitlines()[0] if message else ""
            out.write(f"FAIL {test_name} {file_path} {first_line}\n")

            # stdout / stderr from the result object
            result = result_lookup.get((test_name, file_path))
            if result is not None:
                if result.stdout:
                    out.write(f"  stdout: {result.stdout.strip()}\n")
                if result.stderr:
                    out.write(f"  stderr: {result.stderr.strip()}\n")

            # Verbose: assertion detail lines
            if self._verbose and message:
                for line in self._extract_verbose_lines(message):
                    out.write(f"  {line}\n")

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

        if not parts:
            out.write("0 collected\n")
        else:
            duration = f"{report.duration:.1f}s"
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
    def _extract_line_from_message(message: str) -> str | None:
        """Return the line number string from a message containing 'line N'."""
        match = re.search(r"line (\d+)", message)
        return match.group(1) if match else None

    @staticmethod
    def _extract_verbose_lines(full_message: str) -> list[str]:
        """Extract '> assert ...' and 'where ...' lines for verbose output."""
        lines: list[str] = []
        for line in full_message.splitlines():
            stripped = line.strip()
            if stripped.startswith(">") or stripped.startswith("where "):
                lines.append(stripped)
        return lines
