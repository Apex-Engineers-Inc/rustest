# `--llm` JSONL Output Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plain-text `--llm` renderer with a deterministic JSONL stream optimized for LLM agent consumption.

**Architecture:** A Python-layer renderer (`LlmRenderer`) buffers test results and emits JSONL at `finalize`: a `meta` header line, one `error`/`fail` line per collection error/failure, optional `skip` lines under `-v`, and a `summary` sentinel line last. Pure extraction/normalization helpers and the JSON Schema live in sibling modules. No Rust changes.

**Tech Stack:** Python 3.10–3.14, `json` (stdlib), `importlib.metadata`, argparse. Tests via pytest.

## Global Constraints

- Output is **JSONL only** — no text/human mode under `--llm`. The old plain-text format is removed.
- Every emitted line MUST be valid JSON, ASCII-only (`json.dumps(..., ensure_ascii=True)`), compact (`separators=(",", ":")`), one object + `\n` per line.
- No ANSI codes, no progress output. `--llm` continues to force `--ascii` and `--color never`.
- Output is **buffered** (emitted once at `finalize`) and **sorted** for determinism.
- The `summary` line is always the final line of a completed run (the completion sentinel).
- Schema version constant is `1` (`meta.v == 1`); keep `meta.v` and the `--llm-schema` document in lockstep.
- Follow existing code style: `from __future__ import annotations`, full type hints (basedpyright must pass), ruff format/check clean.
- Reference spec: `docs/superpowers/specs/2026-07-23-llm-jsonl-output-design.md`.

## Data facts (verified against the codebase)

- `TestCompletedEvent`: `test_id`, `file_path`, `test_name`, `status`, `duration`, `message`. `status` ∈ `{"passed","failed","skipped"}`.
- `test_id` is already node-ID shaped: `{abs_path}::test_name`, where `test_name` folds in class and params — e.g. `.../t.py::test_param[2]`, `.../t.py::TestGroup::test_method`. The path segment is absolute with plain backslashes on Windows (no `\\?\`).
- `CollectionErrorEvent`: `path`, `message`.
- `RunReport` (passed to `finalize`): `total`, `passed`, `failed`, `skipped`, `duration`, `results` (tuple of `TestResult`: `name`, `path`, `status`, `message`, `stdout`, `stderr`), `collection_errors` (tuple of `CollectionError`: `path`, `message`).
- Correlate a failure's captured output via `(event.test_name, event.file_path) == (result.name, result.path)`.
- Failure `message` is a full Python traceback. The **failing line** is the line number of the **last** `File "...", line N` entry (class methods produce a `<string>` frame first, then the real file). Traceback file paths carry a `\\?\` extended-length prefix + backslashes.
- Optional assertion values appear as a trailing block:
  ```
  __RUSTEST_ASSERTION_VALUES__
  Expected: 200
  Received: 401
  ```
- Package version: `importlib.metadata.version("rustest")`.

## File Structure

- **Create** `python/rustest/renderers/_llm_extract.py` — pure functions: path normalization, node-ID split, and traceback parsing (line, error/msg, expected/actual, code, frames, capture truncation). No I/O, no state — trivially unit-testable.
- **Create** `python/rustest/renderers/_llm_schema.py` — `SCHEMA_VERSION: int` and `SCHEMA: dict` (JSON Schema for every line type), plus `schema_json() -> str`.
- **Rewrite** `python/rustest/renderers/llm_renderer.py` — `LlmRenderer` buffers events, then emits JSONL in `finalize` using the helpers.
- **Modify** `python/rustest/cli.py` — `-v` becomes `count`; add `--llm-schema`, `--llm-full`; thread verbosity level + full flag; short-circuit `--llm-schema`.
- **Modify** `python/rustest/core.py` — `run()` gains `llm_verbosity: int` and `llm_full: bool`; pass to `LlmRenderer`.
- **Rewrite** `python/tests/test_llm_renderer.py` — assert on parsed JSON, not string matching. Drop all old text-format tests.
- **Modify** `python/tests/test_cli.py`, `python/tests/test_core.py` — cover new flags/params.
- **Modify** `tests/test_llm_integration.py` (create if absent) — end-to-end `--llm` over a fixture.

---

### Task 1: Extraction helpers (`_llm_extract.py`)

Pure functions with no state. Everything the renderer needs to parse a traceback lives here.

**Files:**
- Create: `python/rustest/renderers/_llm_extract.py`
- Test: `python/tests/test_llm_extract.py`

**Interfaces:**
- Produces:
  - `normalize_path(path: str, *, root: str | None = None) -> str`
  - `node_id(test_id: str, *, root: str | None = None) -> str`
  - `file_of(node_id: str) -> str`
  - `extract_line(message: str) -> int | None`
  - `extract_error_and_msg(message: str) -> tuple[str, str]`
  - `extract_expected_actual(message: str) -> tuple[str, str] | None`
  - `extract_code(message: str) -> str | None`
  - `extract_frames(message: str, *, root: str | None = None) -> list[dict[str, object]]`
  - `truncate_tail(text: str, max_lines: int) -> tuple[str, int]`

- [ ] **Step 1: Write the failing tests**

```python
# python/tests/test_llm_extract.py
"""Unit tests for the pure LLM extraction helpers."""

from __future__ import annotations

import os

from rustest.renderers import _llm_extract as ex

ROOT = os.path.normpath("/proj")


def _p(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def test_normalize_path_makes_relative_and_forward_slashed() -> None:
    assert ex.normalize_path(_p("tests", "test_a.py"), root=ROOT) == "tests/test_a.py"


def test_normalize_path_strips_extended_length_prefix() -> None:
    raw = "\\\\?\\" + _p("tests", "test_a.py")
    assert ex.normalize_path(raw, root=ROOT) == "tests/test_a.py"


def test_normalize_path_leaves_synthetic_frames() -> None:
    assert ex.normalize_path("<string>", root=ROOT) == "<string>"


def test_node_id_normalizes_only_the_path_segment() -> None:
    tid = _p("tests", "t.py") + "::TestGroup::test_method"
    assert ex.node_id(tid, root=ROOT) == "tests/t.py::TestGroup::test_method"


def test_node_id_handles_params_with_colons() -> None:
    tid = _p("t.py") + "::test_x[a:b]"
    assert ex.node_id(tid, root=ROOT) == "t.py::test_x[a:b]"


def test_file_of_splits_on_first_double_colon() -> None:
    assert ex.file_of("tests/t.py::TestGroup::test_method") == "tests/t.py"


def test_extract_line_takes_last_file_frame() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 11, in _wrap\n'
        '  File "/proj/t.py", line 9, in test_method\n'
        "    assert False\n"
        "AssertionError\n"
    )
    assert ex.extract_line(msg) == 9


def test_extract_line_returns_none_when_absent() -> None:
    assert ex.extract_line("boom") is None


def test_extract_error_and_msg_splits_on_first_colon() -> None:
    assert ex.extract_error_and_msg("AssertionError: expected 200, got 401") == (
        "AssertionError",
        "expected 200, got 401",
    )


def test_extract_error_and_msg_bare_exception() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        '  File "/proj/t.py", line 5, in test\n'
        "    assert x == 1\n"
        "AssertionError\n"
    )
    assert ex.extract_error_and_msg(msg) == ("AssertionError", "")


def test_extract_error_and_msg_ignores_assertion_values_block() -> None:
    msg = "AssertionError\n\n__RUSTEST_ASSERTION_VALUES__\nExpected: 20\nReceived: 10"
    assert ex.extract_error_and_msg(msg) == ("AssertionError", "")


def test_extract_expected_actual_present() -> None:
    msg = "AssertionError\n\n__RUSTEST_ASSERTION_VALUES__\nExpected: 200\nReceived: 401"
    assert ex.extract_expected_actual(msg) == ("200", "401")


def test_extract_expected_actual_absent() -> None:
    assert ex.extract_expected_actual("AssertionError") is None


def test_extract_code_returns_last_frame_code_line() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 11, in _wrap\n'
        '  File "/proj/t.py", line 9, in test_method\n'
        "    assert response.status == 200\n"
        "           ^^^^^^^^^^^^^^^^^^^^^^\n"
        "AssertionError\n"
    )
    assert ex.extract_code(msg) == "assert response.status == 200"


def test_extract_code_none_when_no_source() -> None:
    assert ex.extract_code("AssertionError") is None


def test_extract_frames_parses_chain_outermost_first() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        '  File "\\\\?\\/proj/t.py", line 42, in test_login\n'
        "    get_status()\n"
        '  File "\\\\?\\/proj/app/client.py", line 88, in get_status\n'
        "    raise TimeoutError\n"
        "TimeoutError\n"
    )
    assert ex.extract_frames(msg, root=ROOT) == [
        {"file": "t.py", "line": 42, "fn": "test_login"},
        {"file": "app/client.py", "line": 88, "fn": "get_status"},
    ]


def test_truncate_tail_keeps_last_n_and_counts_dropped() -> None:
    text = "\n".join(str(i) for i in range(10))
    kept, dropped = ex.truncate_tail(text, 3)
    assert kept == "7\n8\n9"
    assert dropped == 7


def test_truncate_tail_no_truncation_returns_zero() -> None:
    assert ex.truncate_tail("a\nb", 5) == ("a\nb", 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest python/tests/test_llm_extract.py -q`
Expected: FAIL — `ModuleNotFoundError: rustest.renderers._llm_extract`.

- [ ] **Step 3: Implement the helpers**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest python/tests/test_llm_extract.py -q`
Expected: PASS (all).

- [ ] **Step 5: Format, lint, type-check**

Run: `uv run ruff format python && uv run ruff check python && uv run basedpyright python`
Expected: formatted; no lint errors; 0 type errors.

- [ ] **Step 6: Commit**

```bash
git add python/rustest/renderers/_llm_extract.py python/tests/test_llm_extract.py
git commit -m "feat: add pure traceback-extraction helpers for LLM JSONL renderer"
```

---

### Task 2: Schema module (`_llm_schema.py`)

**Files:**
- Create: `python/rustest/renderers/_llm_schema.py`
- Test: `python/tests/test_llm_schema.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION: int`, `SCHEMA: dict[str, object]`, `schema_json() -> str`.

- [ ] **Step 1: Write the failing test**

```python
# python/tests/test_llm_schema.py
"""Tests for the --llm JSON Schema document."""

from __future__ import annotations

import json

from rustest.renderers import _llm_schema as sch


def test_schema_version_is_one() -> None:
    assert sch.SCHEMA_VERSION == 1


def test_schema_json_is_valid_json() -> None:
    parsed = json.loads(sch.schema_json())
    assert isinstance(parsed, dict)


def test_schema_documents_every_line_type() -> None:
    text = sch.schema_json()
    for t in ("meta", "fail", "error", "skip", "summary"):
        assert t in text


def test_schema_reports_matching_version() -> None:
    parsed = json.loads(sch.schema_json())
    assert parsed["version"] == sch.SCHEMA_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/tests/test_llm_schema.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the schema**

```python
# python/rustest/renderers/_llm_schema.py
"""JSON Schema for the --llm JSONL output, printed by --llm-schema."""

from __future__ import annotations

import json

SCHEMA_VERSION = 1

SCHEMA: dict[str, object] = {
    "version": SCHEMA_VERSION,
    "description": "rustest --llm JSONL output. One JSON object per line. "
    "Line order: meta, error(s), fail(s), skip(s) (only with -v), summary (last). "
    "The summary line is the completion sentinel; if absent, the run was interrupted.",
    "lines": {
        "meta": {
            "description": "First line. Version header.",
            "fields": {
                "t": "meta",
                "v": "int schema version",
                "tool": "always 'rustest'",
                "version": "rustest package version",
            },
        },
        "fail": {
            "description": "A test failure. Sorted by (file, line).",
            "fields": {
                "t": "fail",
                "id": "canonical node id 'path::Class::test[param]'; file = id up to first '::'",
                "line": "int failing line number",
                "error": "exception type name",
                "msg": "exception message ('' when none)",
                "expected": "optional; comparison expected value",
                "actual": "optional; comparison actual value",
                "stdout": "optional; captured stdout (tail-truncated unless --llm-full)",
                "stderr": "optional; captured stderr (tail-truncated unless --llm-full)",
                "stdout_omitted": "optional int; stdout lines dropped by truncation",
                "stderr_omitted": "optional int; stderr lines dropped by truncation",
                "code": "optional (-v); failing source line",
                "frames": "optional (-vv); [{file,line,fn}] outermost-first",
            },
        },
        "error": {
            "description": "A collection/setup error. Sorted by path.",
            "fields": {
                "t": "error",
                "path": "file that failed to collect",
                "error": "exception type name",
                "msg": "message",
            },
        },
        "skip": {
            "description": "A skipped test. Emitted only with -v. Sorted by id.",
            "fields": {"t": "skip", "id": "node id", "reason": "skip reason ('' if none)"},
        },
        "summary": {
            "description": "Last line. Completion sentinel.",
            "fields": {
                "t": "summary",
                "passed": "int",
                "failed": "int",
                "skipped": "int",
                "errors": "int",
                "duration": "float seconds",
                "rerun": "optional; node ids of failures + paths of collection errors",
            },
        },
    },
}


def schema_json() -> str:
    """Return the schema as compact JSON (single line)."""
    return json.dumps(SCHEMA, separators=(",", ":"), ensure_ascii=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/tests/test_llm_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/rustest/renderers/_llm_schema.py python/tests/test_llm_schema.py
git commit -m "feat: add versioned JSON Schema for --llm output"
```

---

### Task 3: `LlmRenderer` rewrite — default JSONL (meta/error/fail/summary)

Rewrite the renderer to buffer events and emit JSONL. This task covers the **default** (non-verbose) output. Verbose fields and truncation come in Tasks 4–5.

**Files:**
- Rewrite: `python/rustest/renderers/llm_renderer.py`
- Rewrite: `python/tests/test_llm_renderer.py` (fakes + default-output tests)

**Interfaces:**
- Consumes: `_llm_extract` (Task 1), `_llm_schema.SCHEMA_VERSION` (Task 2).
- Produces: `LlmRenderer(*, verbosity: int = 0, full: bool = False, root: str | None = None, output: IO[str] | None = None)` with `handle(event)` and `finalize(report)`.

- [ ] **Step 1: Write the fakes and failing default-output tests**

```python
# python/tests/test_llm_renderer.py
"""Tests for LlmRenderer JSONL output. Assertions parse JSON, never match strings."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeTestCompletedEvent:
    test_id: str
    file_path: str
    test_name: str
    status: str
    message: str | None = None
    duration: float = 0.0
    timestamp: float = 0.0


@dataclass
class FakeCollectionErrorEvent:
    path: str
    message: str
    timestamp: float = 0.0


@dataclass
class FakeTestResult:
    name: str
    path: str
    status: str
    message: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    duration: float = 0.0


@dataclass
class FakeRunReport:
    passed: int
    failed: int
    skipped: int
    duration: float
    results: tuple[Any, ...] = ()
    collection_errors: tuple[Any, ...] = ()
    total: int = 0


ROOT = "/proj"


def render(events: list[Any], report: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Drive the renderer and return the parsed JSONL objects."""
    from rustest.renderers.llm_renderer import LlmRenderer

    buf = io.StringIO()
    r = LlmRenderer(output=buf, root=ROOT, **kwargs)
    for ev in events:
        r.handle(ev)
    r.finalize(report)
    lines = [ln for ln in buf.getvalue().splitlines() if ln]
    return [json.loads(ln) for ln in lines]


def test_every_line_is_valid_ascii_json() -> None:
    objs = render([], FakeRunReport(passed=0, failed=0, skipped=0, duration=0.0))
    assert all(isinstance(o, dict) for o in objs)


def test_meta_line_is_first_with_version() -> None:
    objs = render([], FakeRunReport(passed=1, failed=0, skipped=0, duration=0.1))
    assert objs[0]["t"] == "meta"
    assert objs[0]["v"] == 1
    assert objs[0]["tool"] == "rustest"
    assert isinstance(objs[0]["version"], str)


def test_summary_line_is_last_and_counts_present() -> None:
    objs = render([], FakeRunReport(passed=30, failed=0, skipped=2, duration=1.2))
    summ = objs[-1]
    assert summ["t"] == "summary"
    assert summ["passed"] == 30
    assert summ["failed"] == 0
    assert summ["skipped"] == 2
    assert summ["errors"] == 0
    assert summ["duration"] == 1.2


def test_all_pass_emits_only_meta_and_summary() -> None:
    objs = render(
        [FakeTestCompletedEvent(f"{ROOT}/t.py::test_{i}", f"{ROOT}/t.py", f"test_{i}", "passed") for i in range(3)],
        FakeRunReport(passed=3, failed=0, skipped=0, duration=0.05),
    )
    assert [o["t"] for o in objs] == ["meta", "summary"]
    assert "rerun" not in objs[-1]


def test_zero_collected_emits_meta_and_zero_summary() -> None:
    objs = render([], FakeRunReport(passed=0, failed=0, skipped=0, duration=0.0))
    assert [o["t"] for o in objs] == ["meta", "summary"]
    assert objs[-1]["passed"] == 0


def _fail_event(name: str, line_msg: str) -> FakeTestCompletedEvent:
    msg = (
        "Traceback (most recent call last):\n"
        f'  File "{ROOT}/t.py", line {line_msg}\n'
        "AssertionError: boom\n"
    )
    return FakeTestCompletedEvent(f"{ROOT}/t.py::{name}", f"{ROOT}/t.py", name, "failed", msg)


def test_failure_line_shape() -> None:
    ev = _fail_event("test_login", "42, in test_login")
    objs = render([ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1))
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["id"] == "t.py::test_login"
    assert fail["line"] == 42
    assert fail["error"] == "AssertionError"
    assert fail["msg"] == "boom"


def test_failures_sorted_by_file_then_line() -> None:
    a = FakeTestCompletedEvent(
        f"{ROOT}/b.py::t1", f"{ROOT}/b.py", "t1", "failed",
        'Traceback (most recent call last):\n  File "%s/b.py", line 9, in t1\nAssertionError\n' % ROOT,
    )
    b = FakeTestCompletedEvent(
        f"{ROOT}/a.py::t2", f"{ROOT}/a.py", "t2", "failed",
        'Traceback (most recent call last):\n  File "%s/a.py", line 3, in t2\nAssertionError\n' % ROOT,
    )
    objs = render([a, b], FakeRunReport(passed=0, failed=2, skipped=0, duration=0.1))
    ids = [o["id"] for o in objs if o["t"] == "fail"]
    assert ids == ["a.py::t2", "b.py::t1"]


def test_collection_error_line_and_count() -> None:
    ev = FakeCollectionErrorEvent(f"{ROOT}/broken.py", "SyntaxError: unexpected indent (line 15)")
    objs = render([ev], FakeRunReport(passed=0, failed=0, skipped=0, duration=0.0,
                                      collection_errors=(FakeTestResult("", f"{ROOT}/broken.py", "error"),)))
    err = next(o for o in objs if o["t"] == "error")
    assert err["path"] == "broken.py"
    assert err["error"] == "SyntaxError"
    assert err["msg"] == "unexpected indent (line 15)"
    assert objs[-1]["errors"] == 1


def test_expected_actual_populated_when_present() -> None:
    msg = (
        "Traceback (most recent call last):\n"
        f'  File "{ROOT}/t.py", line 5, in test_x\n'
        "AssertionError\n\n__RUSTEST_ASSERTION_VALUES__\nExpected: 200\nReceived: 401"
    )
    ev = FakeTestCompletedEvent(f"{ROOT}/t.py::test_x", f"{ROOT}/t.py", "test_x", "failed", msg)
    objs = render([ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1))
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["expected"] == "200"
    assert fail["actual"] == "401"


def test_stdout_attached_to_correct_failure() -> None:
    ev = _fail_event("test_login", "42, in test_login")
    result = FakeTestResult("test_login", f"{ROOT}/t.py", "failed", stdout="hello\n")
    objs = render([ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1, results=(result,)))
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["stdout"] == "hello"


def test_rerun_lists_failures_and_error_paths() -> None:
    fail_ev = _fail_event("test_login", "42, in test_login")
    err_ev = FakeCollectionErrorEvent(f"{ROOT}/broken.py", "SyntaxError: bad")
    objs = render(
        [fail_ev, err_ev],
        FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1,
                      collection_errors=(FakeTestResult("", f"{ROOT}/broken.py", "error"),)),
    )
    rerun = objs[-1]["rerun"]
    assert "t.py::test_login" in rerun
    assert "broken.py" in rerun
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest python/tests/test_llm_renderer.py -q`
Expected: FAIL (renderer still has old text API / new constructor kwargs missing).

- [ ] **Step 3: Rewrite `LlmRenderer`**

```python
# python/rustest/renderers/llm_renderer.py
"""JSONL renderer for LLM tool consumption.

Buffers test results during execution and emits one JSON object per line at
``finalize``: a ``meta`` header, ``error``/``fail`` lines, optional ``skip``
lines (``-v``), and a ``summary`` sentinel last. See
docs/superpowers/specs/2026-07-23-llm-jsonl-output-design.md.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from typing import IO, Any

from . import _llm_extract as ex
from ._llm_schema import SCHEMA_VERSION

_CAPTURE_MAX_LINES = 50


class LlmRenderer:
    """Event consumer that emits deterministic JSONL at finalize."""

    def __init__(
        self,
        *,
        verbosity: int = 0,
        full: bool = False,
        root: str | None = None,
        output: IO[str] | None = None,
    ) -> None:
        super().__init__()
        self._verbosity = verbosity
        self._full = full
        self._root = root
        self._output: IO[str] = output if output is not None else __import__("sys").stdout

        # (test_id, file_path, test_name, message)
        self._failures: list[tuple[str, str, str, str]] = []
        # (test_id, reason)
        self._skips: list[tuple[str, str]] = []
        # (path, message)
        self._collection_errors: list[tuple[str, str]] = []

    # -- event intake -------------------------------------------------------

    def handle(self, event: Any) -> None:  # noqa: ANN401
        name = type(event).__name__
        if name.endswith("TestCompletedEvent"):
            if event.status == "failed":
                self._failures.append(
                    (event.test_id, event.file_path, event.test_name, event.message or "")
                )
            elif event.status == "skipped":
                self._skips.append((event.test_id, event.message or ""))
        elif name.endswith("CollectionErrorEvent"):
            self._collection_errors.append((event.path, event.message))

    # -- emission -----------------------------------------------------------

    def finalize(self, report: Any) -> None:  # noqa: ANN401
        self._emit({"t": "meta", "v": SCHEMA_VERSION, "tool": "rustest", "version": _pkg_version()})

        for obj in self._error_objects():
            self._emit(obj)
        for obj in self._fail_objects(report):
            self._emit(obj)
        if self._verbosity >= 1:
            for obj in self._skip_objects():
                self._emit(obj)

        self._emit(self._summary_object(report))

    def _emit(self, obj: dict[str, object]) -> None:
        self._output.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=True) + "\n")

    # -- object builders ----------------------------------------------------

    def _error_objects(self) -> list[dict[str, object]]:
        objs: list[dict[str, object]] = []
        for path, message in self._collection_errors:
            error, msg = ex.extract_error_and_msg(message)
            objs.append(
                {
                    "t": "error",
                    "path": ex.normalize_path(path, root=self._root),
                    "error": error or "Error",
                    "msg": msg,
                }
            )
        objs.sort(key=lambda o: o["path"])  # type: ignore[arg-type,return-value]
        return objs

    def _fail_objects(self, report: Any) -> list[dict[str, object]]:  # noqa: ANN401
        capture = {(r.name, r.path): r for r in report.results}
        objs: list[dict[str, object]] = []
        for test_id, file_path, test_name, message in self._failures:
            nid = ex.node_id(test_id, root=self._root)
            error, msg = ex.extract_error_and_msg(message)
            obj: dict[str, object] = {
                "t": "fail",
                "id": nid,
                "line": ex.extract_line(message) or 0,
                "error": error or "Error",
                "msg": msg,
            }
            pair = ex.extract_expected_actual(message)
            if pair is not None:
                obj["expected"], obj["actual"] = pair
            self._attach_capture(obj, capture.get((test_name, file_path)))
            self._attach_verbose(obj, message)
            objs.append(obj)
        objs.sort(key=lambda o: (ex.file_of(o["id"]), o["line"]))  # type: ignore[index,arg-type]
        return objs

    def _skip_objects(self) -> list[dict[str, object]]:
        objs = [
            {"t": "skip", "id": ex.node_id(tid, root=self._root), "reason": reason}
            for tid, reason in self._skips
        ]
        objs.sort(key=lambda o: o["id"])  # type: ignore[arg-type,return-value]
        return objs

    def _summary_object(self, report: Any) -> dict[str, object]:  # noqa: ANN401
        summary: dict[str, object] = {
            "t": "summary",
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "errors": len(report.collection_errors),
            "duration": round(report.duration, 3),
        }
        rerun = [ex.node_id(tid, root=self._root) for tid, _, _, _ in self._failures]
        rerun += [ex.normalize_path(p, root=self._root) for p, _ in self._collection_errors]
        if rerun:
            summary["rerun"] = rerun
        return summary

    # -- helpers filled in by later tasks -----------------------------------

    def _attach_capture(self, obj: dict[str, object], result: Any) -> None:  # noqa: ANN401
        # Task 5 fills this in. Default: no capture.
        return

    def _attach_verbose(self, obj: dict[str, object], message: str) -> None:
        # Task 4 fills this in. Default: no verbose fields.
        return


def _pkg_version() -> str:
    try:
        return version("rustest")
    except PackageNotFoundError:  # pragma: no cover - dev fallback
        return "0.0.0"
```

> Note: `_attach_capture` and `_attach_verbose` are intentionally stubs here so Task 3 has a self-contained passing deliverable. Tasks 4 and 5 replace their bodies. The `test_stdout_attached_to_correct_failure` test in Step 1 depends on capture — move that single test into Task 5, OR implement `_attach_capture` now. **Decision: implement `_attach_capture` now** (below) and defer only truncation to Task 5.

Replace the `_attach_capture` stub with the working version (truncation deferred):

```python
    def _attach_capture(self, obj: dict[str, object], result: Any) -> None:  # noqa: ANN401
        if result is None:
            return
        max_lines = 10**9 if self._full else _CAPTURE_MAX_LINES
        for stream in ("stdout", "stderr"):
            raw = getattr(result, stream, None)
            if not raw:
                continue
            kept, dropped = ex.truncate_tail(raw.strip(), max_lines)
            obj[stream] = kept
            if dropped:
                obj[f"{stream}_omitted"] = dropped
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest python/tests/test_llm_renderer.py -q`
Expected: PASS (all Task 3 tests, including capture attachment).

- [ ] **Step 5: Format, lint, type-check**

Run: `uv run ruff format python && uv run ruff check python && uv run basedpyright python`
Expected: clean; 0 type errors.

- [ ] **Step 6: Commit**

```bash
git add python/rustest/renderers/llm_renderer.py python/tests/test_llm_renderer.py
git commit -m "feat: rewrite LlmRenderer to emit deterministic JSONL"
```

---

### Task 4: Verbose fields — `code` (`-v`) and `frames` (`-vv`), plus skip lines

Skip-line emission already landed in Task 3 (gated on `verbosity >= 1`). This task fills in `_attach_verbose`.

**Files:**
- Modify: `python/rustest/renderers/llm_renderer.py` (`_attach_verbose`)
- Modify: `python/tests/test_llm_renderer.py` (verbose tests)

**Interfaces:**
- Consumes: `_llm_extract.extract_code`, `_llm_extract.extract_frames`.

- [ ] **Step 1: Write failing verbose tests**

```python
# append to python/tests/test_llm_renderer.py

def _multiframe_fail() -> FakeTestCompletedEvent:
    msg = (
        "Traceback (most recent call last):\n"
        f'  File "{ROOT}/t.py", line 42, in test_login\n'
        "    get_status()\n"
        f'  File "{ROOT}/app/client.py", line 88, in get_status\n'
        "    assert ok\n"
        "AssertionError\n"
    )
    return FakeTestCompletedEvent(f"{ROOT}/t.py::test_login", f"{ROOT}/t.py", "test_login", "failed", msg)


def test_default_has_no_code_or_frames() -> None:
    objs = render([_multiframe_fail()], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1))
    fail = next(o for o in objs if o["t"] == "fail")
    assert "code" not in fail and "frames" not in fail


def test_v_adds_code_line() -> None:
    objs = render([_multiframe_fail()], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1), verbosity=1)
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["code"] == "assert ok"
    assert "frames" not in fail


def test_vv_adds_frames_outermost_first() -> None:
    objs = render([_multiframe_fail()], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1), verbosity=2)
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["frames"] == [
        {"file": "t.py", "line": 42, "fn": "test_login"},
        {"file": "app/client.py", "line": 88, "fn": "get_status"},
    ]


def test_v_emits_skip_lines() -> None:
    skip = FakeTestCompletedEvent(f"{ROOT}/t.py::test_wip", f"{ROOT}/t.py", "test_wip", "skipped", "not ready")
    objs = render([skip], FakeRunReport(passed=0, failed=0, skipped=1, duration=0.0), verbosity=1)
    skips = [o for o in objs if o["t"] == "skip"]
    assert skips == [{"t": "skip", "id": "t.py::test_wip", "reason": "not ready"}]


def test_default_omits_skip_lines() -> None:
    skip = FakeTestCompletedEvent(f"{ROOT}/t.py::test_wip", f"{ROOT}/t.py", "test_wip", "skipped", "not ready")
    objs = render([skip], FakeRunReport(passed=0, failed=0, skipped=1, duration=0.0))
    assert not [o for o in objs if o["t"] == "skip"]
    assert objs[-1]["skipped"] == 1
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest python/tests/test_llm_renderer.py -q -k "code or frames or skip"`
Expected: `test_v_adds_code_line` and `test_vv_adds_frames_outermost_first` FAIL; skip tests already pass from Task 3.

- [ ] **Step 3: Implement `_attach_verbose`**

```python
    def _attach_verbose(self, obj: dict[str, object], message: str) -> None:
        if self._verbosity >= 1:
            code = ex.extract_code(message)
            if code is not None:
                obj["code"] = code
        if self._verbosity >= 2:
            frames = ex.extract_frames(message, root=self._root)
            if frames:
                obj["frames"] = frames
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest python/tests/test_llm_renderer.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/rustest/renderers/llm_renderer.py python/tests/test_llm_renderer.py
git commit -m "feat: add -v code line and -vv frame chain to LLM JSONL"
```

---

### Task 5: Capture truncation edge cases

`_attach_capture` already truncates (Task 3). This task adds explicit tests for the tail-cap + `*_omitted` + `--llm-full` behavior and locks the 50-line default.

**Files:**
- Modify: `python/tests/test_llm_renderer.py`

- [ ] **Step 1: Write failing truncation tests**

```python
# append to python/tests/test_llm_renderer.py

def _fail_with_stdout(nlines: int) -> tuple[FakeTestCompletedEvent, FakeTestResult]:
    ev = _fail_event("test_x", "5, in test_x")
    text = "\n".join(f"line{i}" for i in range(nlines))
    return ev, FakeTestResult("test_x", f"{ROOT}/t.py", "failed", stdout=text)


def test_stdout_truncated_to_last_50_lines() -> None:
    ev, result = _fail_with_stdout(60)
    objs = render([ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1, results=(result,)))
    fail = next(o for o in objs if o["t"] == "fail")
    assert fail["stdout"].splitlines()[0] == "line10"
    assert fail["stdout"].splitlines()[-1] == "line59"
    assert fail["stdout_omitted"] == 10


def test_stdout_not_truncated_when_short() -> None:
    ev, result = _fail_with_stdout(5)
    objs = render([ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1, results=(result,)))
    fail = next(o for o in objs if o["t"] == "fail")
    assert "stdout_omitted" not in fail


def test_full_disables_truncation() -> None:
    ev, result = _fail_with_stdout(200)
    objs = render([ev], FakeRunReport(passed=0, failed=1, skipped=0, duration=0.1, results=(result,)), full=True)
    fail = next(o for o in objs if o["t"] == "fail")
    assert len(fail["stdout"].splitlines()) == 200
    assert "stdout_omitted" not in fail
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest python/tests/test_llm_renderer.py -q -k truncat or full`
Expected: PASS (behavior already implemented in Task 3; these lock it).

- [ ] **Step 3: Commit**

```bash
git add python/tests/test_llm_renderer.py
git commit -m "test: lock capture truncation and --llm-full behavior"
```

---

### Task 6: CLI flags — `-v` count, `--llm-schema`, `--llm-full`

**Files:**
- Modify: `python/rustest/cli.py`
- Modify: `python/tests/test_cli.py`

**Interfaces:**
- Consumes: `_llm_schema.schema_json` (for the `--llm-schema` short-circuit), `core.run` (new params from Task 7).
- Produces: parser with `verbose` (int count), `llm_schema` (bool), `llm_full` (bool); `main()` prints schema and returns 0 when `--llm-schema` is set.

- [ ] **Step 1: Write failing CLI tests**

```python
# append/modify in python/tests/test_cli.py

def test_verbose_is_count() -> None:
    from rustest.cli import build_parser

    args = build_parser().parse_args(["-vv"])
    assert args.verbose == 2


def test_llm_schema_flag_parses() -> None:
    from rustest.cli import build_parser

    args = build_parser().parse_args(["--llm-schema"])
    assert args.llm_schema is True


def test_llm_full_flag_parses() -> None:
    from rustest.cli import build_parser

    args = build_parser().parse_args(["--llm", "--llm-full"])
    assert args.llm_full is True


def test_llm_schema_prints_and_exits_zero(capsys: object) -> None:
    import json as _json

    from rustest.cli import main

    rc = main(["--llm-schema"])
    assert rc == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    doc = _json.loads(out.strip())
    assert doc["version"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest python/tests/test_cli.py -q -k "verbose_is_count or llm_schema or llm_full"`
Expected: FAIL — `verbose` is bool; flags absent.

- [ ] **Step 3: Modify `cli.py`**

Change the `-v` argument and add the two flags:

```python
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity. Repeat (-vv) for more detail (LLM: adds frames).",
    )
```

Add after the existing `--llm` argument:

```python
    _ = parser.add_argument(
        "--llm-schema",
        action="store_true",
        help="Print the JSON Schema for --llm output and exit.",
    )
    _ = parser.add_argument(
        "--llm-full",
        action="store_true",
        help="With --llm, do not truncate captured stdout/stderr.",
    )
```

Update `parser.set_defaults(...)` to include `llm_schema=False, llm_full=False` (leave `verbose` to its argument default of 0).

In `main()`, short-circuit `--llm-schema` before running, and pass the new params through. At the top of `main()` after `args = parser.parse_args(argv)`:

```python
    if args.llm_schema:
        from rustest.renderers._llm_schema import schema_json

        print(schema_json())
        return 0
```

Update the `run(...)` call to pass verbosity + full (see Task 7 for the signature):

```python
    report = run(
        paths=list(args.paths),
        pattern=args.pattern,
        mark_expr=args.mark_expr,
        workers=args.workers,
        capture_output=args.capture_output,
        enable_codeblocks=args.enable_codeblocks,
        last_failed_mode=last_failed_mode,
        fail_fast=args.fail_fast,
        pytest_compat=args.pytest_compat,
        verbose=bool(args.verbose),
        ascii=args.ascii,
        no_color=not use_color,
        llm=args.llm,
        llm_verbosity=args.verbose,
        llm_full=args.llm_full,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest python/tests/test_cli.py -q`
Expected: PASS (fix any old tests that assumed `verbose` was a bool — update them to `== 0/1`).

- [ ] **Step 5: Format, lint, type-check**

Run: `uv run ruff format python && uv run ruff check python && uv run basedpyright python`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add python/rustest/cli.py python/tests/test_cli.py
git commit -m "feat: add --llm-schema and --llm-full flags; make -v a count"
```

---

### Task 7: Wire verbosity + full through `core.run`

**Files:**
- Modify: `python/rustest/core.py`
- Modify: `python/tests/test_core.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `run(..., llm_verbosity: int = 0, llm_full: bool = False)` constructing `LlmRenderer(verbosity=llm_verbosity, full=llm_full)`.

- [ ] **Step 1: Write failing test**

```python
# append to python/tests/test_core.py

def test_run_forwards_llm_verbosity_and_full(monkeypatch: object) -> None:
    """core.run must build LlmRenderer with the given verbosity and full flag."""
    import rustest.core as core

    captured: dict[str, object] = {}

    class SpyRenderer:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def handle(self, event: object) -> None:  # pragma: no cover
            pass

        def finalize(self, report: object) -> None:
            pass

    monkeypatch.setattr(core, "LlmRenderer", SpyRenderer)  # type: ignore[attr-defined]

    core.run(paths=["examples/tests/"], llm=True, llm_verbosity=2, llm_full=True)

    assert captured.get("verbosity") == 2
    assert captured.get("full") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest python/tests/test_core.py -q -k forwards_llm`
Expected: FAIL — `run()` has no `llm_verbosity`/`llm_full` params; renderer built with `verbose=`.

- [ ] **Step 3: Modify `core.py`**

Add params to `run()` signature (after `llm: bool = False`):

```python
    llm_verbosity: int = 0,
    llm_full: bool = False,
```

Replace the renderer construction:

```python
    router = EventRouter()
    if llm:
        renderer: LlmRenderer | RichRenderer = LlmRenderer(
            verbosity=llm_verbosity, full=llm_full
        )
        router.subscribe(renderer)
    else:
        renderer = RichRenderer(use_colors=not no_color, use_ascii=ascii)
        router.subscribe(renderer)
```

The existing `finalize(report)` call at the end stays as-is.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest python/tests/test_core.py -q -k forwards_llm`
Expected: PASS.

- [ ] **Step 5: Run all Python unit tests**

Run: `uv run pytest python/tests -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add python/rustest/core.py python/tests/test_core.py
git commit -m "feat: thread llm verbosity and full flag through core.run"
```

---

### Task 8: End-to-end integration test

Verify real rustest output — every line parses as JSON, sentinel last, node IDs and counts correct.

**Files:**
- Create: `tests/test_llm_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_llm_integration.py
"""End-to-end: `rustest --llm` over a fixture suite emits valid JSONL."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _write_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "suite_test.py"
    suite.write_text(
        "import rustest\n"
        "def test_ok():\n    assert True\n"
        "def test_bad():\n    assert 1 == 2\n"
        "@rustest.mark.skip(reason='wip')\n"
        "def test_wip():\n    assert False\n"
    )
    return suite


def _run(args: list[str]) -> list[dict[str, object]]:
    proc = subprocess.run(
        [sys.executable, "-m", "rustest", *args],
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]  # raises if any line is not JSON


def test_llm_jsonl_end_to_end(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path)
    objs = _run(["--llm", str(suite)])

    assert objs[0]["t"] == "meta"
    assert objs[-1]["t"] == "summary"
    summ = objs[-1]
    assert summ["passed"] == 1
    assert summ["failed"] == 1
    assert summ["skipped"] == 1

    fails = [o for o in objs if o["t"] == "fail"]
    assert len(fails) == 1
    assert fails[0]["id"].endswith("::test_bad")
    assert "test_wip" not in json.dumps(objs)  # skip not emitted by default


def test_llm_verbose_emits_skip_and_code(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path)
    objs = _run(["--llm", "-v", str(suite)])
    assert any(o["t"] == "skip" and o["id"].endswith("::test_wip") for o in objs)
    assert any(o["t"] == "fail" and "code" in o for o in objs)


def test_llm_no_ansi_or_nonascii(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "rustest", "--llm", str(suite)],
        capture_output=True,
        text=True,
    )
    assert "\x1b" not in proc.stdout
    assert proc.stdout.isascii()
```

- [ ] **Step 2: Build and run the integration test**

Run: `uv run maturin develop && uv run pytest tests/test_llm_integration.py -q`
Expected: PASS. (If node-id assertions fail on Windows path separators, confirm `normalize_path` is applied — the `id` must use `/`.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_llm_integration.py
git commit -m "test: end-to-end JSONL integration for --llm"
```

---

### Task 9: Docs, changelog, and full verification

**Files:**
- Modify: `README.md` and/or `docs/` where `--llm` is described (search first).
- Modify: `CHANGELOG` per the `bump-version` conventions (do NOT bump version number unless asked).

- [ ] **Step 1: Find and update existing `--llm` documentation**

Run: `grep -rn "\-\-llm" README.md docs/ 2>/dev/null`
Update any description of the old text format to describe JSONL, and mention `--llm-schema`, `--llm-full`, and `-v`/`-vv`. Mark example blocks that would otherwise be executed with `<!--rustest.mark.skip-->` if they are JSONL samples (not runnable Python).

- [ ] **Step 2: Python format, lint, type-check**

Run: `uv run ruff format --check python && uv run ruff check python && uv run basedpyright python`
Expected: clean; 0 errors.

- [ ] **Step 3: Full Python unit suite**

Run: `uv run pytest python/tests -q`
Expected: PASS.

- [ ] **Step 4: Integration suites via both runners**

Run: `uv run pytest tests/ examples/tests/ -q`
Then: `uv run python -m rustest tests/ examples/tests/`
Expected: PASS in both.

- [ ] **Step 5: Rust checks (no Rust changed, but keep CI green)**

Run: `cargo fmt --check && cargo clippy --lib -- -D warnings`
Expected: clean. (`cargo test --lib` has pre-existing environment-only failures on this machine; CI runs it.)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs: document --llm JSONL output, --llm-schema, --llm-full"
```

---

## Self-Review

**Spec coverage:**
- JSONL, no text mode → Tasks 3–5, Global Constraints. ✓
- Emission model (failures/errors + summary; skips under -v) → Task 3 (gating), Task 4 (skip tests). ✓
- Buffered + sorted + sentinel → Task 3 (`finalize`, sort keys, summary last). ✓
- Line types meta/fail/error/skip/summary with all fields → Task 3 (meta/fail/error/summary), Task 4 (skip/code/frames). ✓
- Node IDs (`path::Class::test[param]`, relative/forward-slash) → Task 1 `node_id`, Task 3. ✓
- expected/actual → Task 1 `extract_expected_actual`, Task 3. ✓
- rerun → Task 3 `_summary_object`. ✓
- Truncation (tail 50, `*_omitted`, `--llm-full`) → Task 3 `_attach_capture`, Task 5 tests, Task 6/7 flag. ✓
- `--llm-schema` → Task 2, Task 6. ✓
- `-v`/`-vv` → Task 6 (count), Task 4 (mapping). ✓
- meta version = pkg version; `meta.v`/schema lockstep → Task 3 `_pkg_version`, Task 2. ✓
- Edge cases (zero collected, all skipped, empty message, `--llm-full`, parallel determinism) → Task 3 tests, Task 4/5 tests. ✓

**Placeholder scan:** `_attach_capture`/`_attach_verbose` appear as stubs in Task 3's first code block but the task explicitly implements `_attach_capture` in the same step and Task 4 implements `_attach_verbose`; no step ships a stub as its deliverable. No TBD/TODO. ✓

**Type consistency:** `LlmRenderer(verbosity, full, root, output)` constructor is consistent across core.py (Task 7), the test `render()` helper (Task 3), and the spy test (Task 7). `node_id`/`file_of`/`normalize_path` signatures match between Task 1 definitions and Task 3 call sites. ✓
