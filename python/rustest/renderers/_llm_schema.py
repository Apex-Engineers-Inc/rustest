"""JSON Schema for the --llm JSONL output, printed by --llm-schema."""

from __future__ import annotations

import json

SCHEMA_VERSION = 1

SCHEMA: dict[str, object] = {
    "version": SCHEMA_VERSION,
    "description": (
        "rustest --llm JSONL output. One JSON object per line. "
        "Line order: meta, error(s), fail(s), skip(s) (only with -v), summary (last). "
        "The summary line is the completion sentinel; if absent, the run was interrupted."
    ),
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
