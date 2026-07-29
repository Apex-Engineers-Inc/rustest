"""The JSON Schema for ``--llm`` output, printed verbatim by ``--llm-schema``.

Kept in its own module for two reasons.  It is imported **only** by the ``--llm`` path, so an
ordinary run never pays for a ~4 KB dict literal; and it is the one place the wire is
described, so :mod:`rustest._llm` and this document cannot drift into disagreeing about a
field name without :func:`python.tests.test_llm_output` noticing.

**Why this is a real JSON Schema and not a prose dictionary.**  The 0.18 implementation on
``main`` printed a hand-rolled ``{"lines": {"fail": {"fields": {...}}}}`` document whose values
were English sentences.  It read well and validated nothing.  A consumer that wants to *check*
a line -- which is the entire reason a tool asks for a schema rather than reading the guide --
had to reimplement the shape from prose.  This is draft 2020-12, discriminated on ``t``, so
``jsonschema.validate(line, SCHEMA)`` is the whole integration.
"""

from __future__ import annotations

import json

#: The wire's major version, carried on every ``meta`` line as ``schema_version``.
#:
#: **2, and the jump from ``main``'s 1 is deliberate rather than incidental.**  The v2 engine
#: reports *six* status buckets (``passed``/``failed``/``skipped``/``xfailed``/``xpassed``/
#: ``error``) where v1 had three, and its failure ``message`` is an assertion-rewritten string
#: the worker already composed -- so the v1 shape (``error``/``msg``/``expected``/``actual``
#: split out of a traceback by regex, a three-bucket summary, no ``exit_code``) cannot be
#: produced from v2's data without inventing fields.  A consumer pinned to ``schema_version: 1``
#: should refuse a ``2`` stream rather than half-read it, which is exactly what a major bump is
#: for.  See ``docs/guide/llm-output.md`` for the field-by-field mapping.
SCHEMA_VERSION = 2

#: Statuses a ``fail`` line can carry -- the two that make a run red.
#:
#: Duplicated as a literal in :data:`SCHEMA` (JSON Schema wants the values inline) and derived
#: from here in :mod:`rustest._llm`, so the schema and the emitter are pinned to one list by
#: ``test_llm_schema_statuses_match_the_emitter``.
FAIL_STATUSES: tuple[str, ...] = ("failed", "error")

#: Statuses a ``skip`` line can carry -- the three that are neither a pass nor a failure.
#:
#: ``xpassed`` is here rather than silently dropped: a non-strict xpass is a test whose
#: ``xfail`` mark has gone stale, which is a thing an agent should be able to see at ``-v``.
#: (A *strict* xpass is reported ``failed`` by the engine and gets a ``fail`` line.)
SKIP_STATUSES: tuple[str, ...] = ("skipped", "xfailed", "xpassed")

_NODE_ID = "Node id, ``path::Class::test[param]``, path relative to rootdir with forward slashes."

SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/Apex-Engineers-Inc/rustest/schemas/llm-2.json",
    "title": "rustest --llm JSONL",
    # Repeated at the top level as a plain integer so a consumer can version-gate without
    # walking into `$defs`; it is the same number every `meta` line carries.
    "version": SCHEMA_VERSION,
    "description": (
        "One JSON object per line, all emitted at completion. Line order is fixed: meta,"
        " then error lines (collection and unattributable teardown), then fail lines,"
        " then skip lines (only under -v), then exactly one summary line. Fail and skip"
        " lines are in manifest order -- pytest's collection order -- which the engine"
        " reassembles independently of which worker finished first, so the byte stream is"
        " identical across runs and across -n. The summary line is the completion"
        " sentinel: if it is absent, the run was interrupted."
    ),
    "oneOf": [
        {"$ref": "#/$defs/meta"},
        {"$ref": "#/$defs/error"},
        {"$ref": "#/$defs/fail"},
        {"$ref": "#/$defs/skip"},
        {"$ref": "#/$defs/summary"},
    ],
    "$defs": {
        "meta": {
            "type": "object",
            "title": "meta",
            "description": "First line. Identifies the wire and sizes the run.",
            "properties": {
                "t": {"const": "meta"},
                "schema_version": {
                    "const": SCHEMA_VERSION,
                    "description": "Major version of this wire. Bumped on any breaking change.",
                },
                "tool": {"const": "rustest"},
                "version": {"type": "string", "description": "The rustest package version."},
                "rootdir": {
                    "type": "string",
                    "description": "Absolute rootdir, forward slashes. Every id is relative to it.",
                },
                "total": {
                    "type": "integer",
                    "description": (
                        "Tests selected for this run, after -k/-m deselection. Present on the"
                        " first line so a consumer knows the scale of the run before reading it."
                    ),
                },
            },
            "required": ["t", "schema_version", "tool", "version", "rootdir", "total"],
            "additionalProperties": False,
        },
        "error": {
            "type": "object",
            "title": "error",
            "description": (
                "A failure with no node id to hang it on: a file that could not be collected,"
                " or a fixture teardown that raised after the last test it owned had already"
                " been reported. Distinguished from `fail` by shape, not by severity."
            ),
            "properties": {
                "t": {"const": "error"},
                "scope": {
                    "enum": ["collection", "teardown"],
                    "description": (
                        "`collection`: the file never imported, so none of its tests ran."
                        " `teardown`: a module- or session-scoped fixture raised at shutdown."
                    ),
                },
                "file": {
                    "type": "string",
                    "description": "Path relative to rootdir. Present for scope=collection only.",
                },
                "msg": {"type": "string", "description": "The engine's message, verbatim."},
            },
            "required": ["t", "scope", "msg"],
            "additionalProperties": False,
        },
        "fail": {
            "type": "object",
            "title": "fail",
            "description": "One test that made the run red. Emitted at every verbosity.",
            "properties": {
                "t": {"const": "fail"},
                "id": {"type": "string", "description": _NODE_ID},
                "file": {
                    "type": "string",
                    "description": "The id up to the first `::`, split out so it need not be parsed.",
                },
                "line": {
                    "type": "integer",
                    "description": (
                        "Line of the innermost traceback frame. Omitted when the message is not"
                        " a traceback (a `rustest.fail()` reason, for instance)."
                    ),
                },
                "status": {
                    "enum": list(FAIL_STATUSES),
                    "description": (
                        "`failed`: the test body raised. `error`: setup or teardown raised, so"
                        " the body never ran (or its result cannot be trusted)."
                    ),
                },
                "msg": {
                    "type": "string",
                    "description": (
                        "The full failure message the worker composed: a frame-filtered"
                        " traceback whose final line carries the assertion-rewritten"
                        " comparison (`AssertionError: assert 41 == 42`). Not decomposed into"
                        " error/expected/actual -- see the guide for why."
                    ),
                },
                "stdout": {
                    "type": "string",
                    "description": (
                        "Captured stdout, last 50 lines unless --llm-full. Omitted when empty"
                        " and under -q."
                    ),
                },
                "stderr": {"type": "string", "description": "Captured stderr, same rules."},
                "stdout_omitted": {
                    "type": "integer",
                    "description": "Leading stdout lines dropped by truncation. Omitted when none.",
                },
                "stderr_omitted": {"type": "integer", "description": "Same, for stderr."},
            },
            "required": ["t", "id", "file", "status", "msg"],
            "additionalProperties": False,
        },
        "skip": {
            "type": "object",
            "title": "skip",
            "description": (
                "One test that neither passed nor failed. Emitted **only under -v**: at the"
                " default verbosity these are counted in the summary and cost no tokens."
            ),
            "properties": {
                "t": {"const": "skip"},
                "id": {"type": "string", "description": _NODE_ID},
                "file": {"type": "string", "description": "The id up to the first `::`."},
                "status": {"enum": list(SKIP_STATUSES)},
                "reason": {
                    "type": "string",
                    "description": "The mark's reason, or `''` when the test gave none.",
                },
            },
            "required": ["t", "id", "file", "status", "reason"],
            "additionalProperties": False,
        },
        "summary": {
            "type": "object",
            "title": "summary",
            "description": (
                "Last line, always present on a completed run. Carries the engine's six status"
                " buckets verbatim -- they are the report's own counts, not a re-tally of the"
                " lines above, so they are true even at -q where nothing else is emitted."
            ),
            "properties": {
                "t": {"const": "summary"},
                "total": {"type": "integer", "description": "Tests selected (post-deselection)."},
                "passed": {"type": "integer"},
                "failed": {"type": "integer"},
                "skipped": {"type": "integer"},
                "xfailed": {"type": "integer"},
                "xpassed": {"type": "integer"},
                "error": {
                    "type": "integer",
                    "description": (
                        "Tests whose setup or teardown raised. Does **not** include collection"
                        " errors, which have no test to count; those are the `error` lines"
                        " above and are counted in `collection_errors`."
                    ),
                },
                "deselected": {"type": "integer", "description": "Removed by -k / -m."},
                "collection_errors": {
                    "type": "integer",
                    "description": "Files that failed to import. Each has an `error` line above.",
                },
                "duration": {
                    "type": "number",
                    "description": "Wall-clock seconds over the staged run, 3 decimal places.",
                },
                "exit_code": {
                    "type": "integer",
                    "description": (
                        "pytest's exit code, which --llm never changes: 0 clean, 1 failures,"
                        " 2 collection errors, 5 nothing collected."
                    ),
                },
                "stopped_early": {
                    "const": True,
                    "description": (
                        "Present **only** when -x/--maxfail cut the run short, so the counts"
                        " above describe a partial selection. Absent means the run was complete."
                    ),
                },
            },
            "required": [
                "t",
                "total",
                "passed",
                "failed",
                "skipped",
                "xfailed",
                "xpassed",
                "error",
                "deselected",
                "collection_errors",
                "duration",
                "exit_code",
            ],
            "additionalProperties": False,
        },
    },
}


def schema_json() -> str:
    """The schema as one line of compact JSON -- what ``--llm-schema`` prints.

    One line, like every other line ``--llm`` writes, so a tool can read the schema with the
    same JSONL reader it uses for the output rather than switching to a whole-stream parse.
    ``ensure_ascii`` for the same reason the emitter uses it: the output survives a console
    whose encoding is not UTF-8, which on Windows is the default.
    """
    return json.dumps(SCHEMA, separators=(",", ":"), ensure_ascii=True)
