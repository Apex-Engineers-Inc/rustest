# python/rustest/renderers/_llm_extract.py
"""Pure functions for parsing rustest tracebacks into JSONL fields.

No state, no I/O. Everything here is a deterministic transform on the
traceback/message strings produced by the Rust core.
"""

from __future__ import annotations

import os
import re

_FILE_RE = re.compile(r'File "(?P<file>.*?)", line (?P<line>\d+), in (?P<fn>.+)')
_EXT_PREFIX = "\\\\?\\"
_VALUES_MARKER = "__RUSTEST_ASSERTION_VALUES__"


def normalize_path(path: str, *, root: str | None = None) -> str:
    """Make a traceback/test path relative to ``root`` with forward slashes.

    Strips the Windows ``\\\\?\\`` extended-length prefix. Synthetic frames
    such as ``<string>`` are returned unchanged. Paths outside ``root`` fall
    back to a forward-slashed absolute path.
    """
    if path.startswith("<") and path.endswith(">"):
        return path
    if path.startswith(_EXT_PREFIX):
        path = path[len(_EXT_PREFIX) :]
    base = root if root is not None else os.getcwd()
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        # Different drive on Windows — keep absolute.
        rel = path
    return rel.replace(os.sep, "/").replace("\\", "/")


def node_id(test_id: str, *, root: str | None = None) -> str:
    """Normalize only the path segment of a ``path::name`` test id."""
    path, sep, rest = test_id.partition("::")
    if not sep:
        return test_id
    return f"{normalize_path(path, root=root)}::{rest}"


def file_of(node_id_str: str) -> str:
    """Return the file portion of a node id (text before the first ``::``)."""
    return node_id_str.partition("::")[0]


def _body_lines(message: str) -> list[str]:
    """Traceback lines up to (but not including) the assertion-values block."""
    out: list[str] = []
    for line in message.splitlines():
        if line.strip() == _VALUES_MARKER:
            break
        out.append(line)
    return out


def extract_line(message: str) -> int | None:
    """Line number of the last ``File`` frame (the innermost/failing frame)."""
    matches = _FILE_RE.findall(message)
    if not matches:
        return None
    return int(matches[-1][1])


def extract_error_and_msg(message: str) -> tuple[str, str]:
    """Return ``(error_type, message)`` from the final traceback line.

    The last non-blank body line is the exception line. ``Error: detail``
    splits into ``("Error", "detail")``; a bare ``Error`` yields ``("Error", "")``.
    """
    last = ""
    for line in _body_lines(message):
        stripped = line.strip()
        if stripped and not line.startswith(" ") and not stripped.startswith("^"):
            last = stripped
    if not last:
        return ("", "")
    error, sep, detail = last.partition(": ")
    return (error, detail if sep else "")


def extract_expected_actual(message: str) -> tuple[str, str] | None:
    """Extract ``Expected:``/``Received:`` from the assertion-values block."""
    if _VALUES_MARKER not in message:
        return None
    expected: str | None = None
    received: str | None = None
    seen = False
    for line in message.splitlines():
        stripped = line.strip()
        if stripped == _VALUES_MARKER:
            seen = True
            continue
        if not seen:
            continue
        if stripped.startswith("Expected:"):
            expected = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Received:"):
            received = stripped.split(":", 1)[1].strip()
    if expected is None or received is None:
        return None
    return (expected, received)


def extract_code(message: str) -> str | None:
    """Failing source line: the indented code under the last ``File`` frame."""
    lines = _body_lines(message)
    code: str | None = None
    for line in lines:
        stripped = line.strip()
        if _FILE_RE.search(line):
            code = None  # reset at each new frame; keep the last frame's code
        elif line.startswith("    ") and stripped and not stripped.startswith("^"):
            code = stripped
    return code


def extract_frames(message: str, *, root: str | None = None) -> list[dict[str, object]]:
    """Parse the traceback frame chain, outermost first."""
    frames: list[dict[str, object]] = []
    for file, line, fn in _FILE_RE.findall(message):
        frames.append(
            {"file": normalize_path(file, root=root), "line": int(line), "fn": fn.strip()}
        )
    return frames


def truncate_tail(text: str, max_lines: int) -> tuple[str, int]:
    """Keep the last ``max_lines`` lines. Return ``(kept_text, dropped_count)``."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return (text, 0)
    kept = lines[-max_lines:]
    return ("\n".join(kept), len(lines) - max_lines)
