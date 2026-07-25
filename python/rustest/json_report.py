"""Machine-readable JSON report (schema v1) for tooling and conformance."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .reporting import RunReport

SCHEMA_VERSION = 1


def write_json_report(report: RunReport, path: str | os.PathLike[str]) -> None:
    """Serialize a RunReport to the schema-v1 JSON document at *path*."""
    payload = {
        "version": SCHEMA_VERSION,
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "duration": report.duration,
        },
        "tests": [
            {
                "id": f"{result.path}::{result.name}",
                "name": result.name,
                "path": result.path,
                "status": result.status,
                "duration": result.duration,
                "message": result.message,
            }
            for result in report.results
        ],
        "collection_errors": [
            {"path": error.path, "message": error.message} for error in report.collection_errors
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
