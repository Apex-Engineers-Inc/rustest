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
