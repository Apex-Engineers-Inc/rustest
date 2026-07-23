# `--llm` JSONL Output Mode (v2 redesign)

## Status

Supersedes the plain-text `--llm` format defined in
`2026-04-06-llm-output-flag-design.md`. The plain-text renderer is **replaced
entirely** — `--llm` now emits JSONL. There is no text mode.

## Overview

`--llm` produces a JSONL stream (one JSON object per line) optimized for
consumption by LLM-based coding agents. The format is chosen for the shape of
test output: few records, heterogeneous/sparse fields, nested tracebacks, and
delimiter-heavy free text — where JSON's universal parseability and clean string
escaping beat delimiter-based formats (CSV/TOON) and the previous
space-delimited text format, which was ambiguous to parse.

## Design principles

1. **Signal only.** Emit lines for what an agent must act on — failures and
   collection errors. Passes and skips are counts in the summary, not lines.
   (Skips gain lines under `-v`.) Minimizes both token cost and reasoning noise.
2. **Unambiguous.** JSON string escaping eliminates the delimiter/quoting
   ambiguity of the old text format.
3. **Deterministic.** Buffered and sorted, so identical failures produce
   identical bytes across runs — enabling prompt caching and meaningful diffs.
4. **Actionable.** First-class `expected`/`actual`, canonical re-runnable node
   IDs, and a ready-made `rerun` list.
5. **Self-documenting.** `--llm-schema` prints the machine-readable schema.

## CLI surface

| Flag | Effect |
|---|---|
| `--llm` | Emit JSONL: `meta`, `error`, `fail`, `summary`. Buffered, sorted. |
| `--llm -v` | Add `code` (failing source line) to failures; emit `skip` lines. |
| `--llm -vv` | Add `frames` (traceback frame chain) to failures. |
| `--llm-schema` | Print the JSON Schema for the output and exit. |
| `--llm-full` | Disable stdout/stderr truncation. |

Implicit overrides when `--llm` is present (unchanged from v1): forces `--ascii`
and `--color never`; conflicting flags silently overridden.

`--llm` is intent-named (not `--format=jsonl`); a general `--format=` can be
added later if a second machine format is ever needed. YAGNI for now.

## Stream shape

Output is buffered and emitted once at completion, in this fixed order. Each
group is internally sorted for determinism:

1. `meta` — first line (header / version).
2. `error` — collection/setup errors, sorted by `path`.
3. `fail` — sorted by `(file, line)`.
4. `skip` — only under `-v`, sorted by `id`.
5. `summary` — last line.

**Completion sentinel:** the `summary` line is always the final line of a
completed run (even for zero tests). If it is absent, the run was interrupted.
This is the contract consumers rely on; it recovers most of the crash-resilience
benefit of streaming without the nondeterminism cost.

## Line types

### `meta`
```json
{"t":"meta","v":1,"tool":"rustest","version":"0.17.0"}
```
- `v` (int) — schema version. Bumped on breaking format changes.
- `tool` (string) — always `"rustest"`.
- `version` (string) — rustest package version.

### `fail`
```json
{"t":"fail","id":"tests/test_auth.py::test_login","line":42,"error":"AssertionError","msg":"assert status == 200","expected":"200","actual":"401","stdout":"Attempting login user=admin"}
```
- `id` (string) — canonical node ID: `path::Class::test[param]`. Class and
  param segments appear only when present. The file path is `id` up to the first
  `::`; no separate `file` field is emitted.
- `line` (int) — the failing source line number.
- `error` (string) — exception type name (e.g. `AssertionError`, `TimeoutError`).
- `msg` (string) — exception message (last meaningful traceback line). `""` when
  the exception carried no message.
- `expected` / `actual` (string, optional) — present only when rustest's
  assertion rewriting captured comparison values.
- `stdout` / `stderr` (string, optional) — present only when non-empty. Subject
  to truncation (see below).
- `stdout_omitted` / `stderr_omitted` (int, optional) — number of lines dropped
  by truncation; present only when truncation occurred.
- `code` (string, optional, `-v`) — the failing source line, read from the file.
  Kept separate from `msg`: different source (file vs exception) and
  verbose-only, even though for a bare `assert` the two often coincide.
- `frames` (array, optional, `-vv`) — traceback frame chain, outermost →
  innermost. Each: `{"file":..., "line":..., "fn":...}`.

### `error` (collection/setup error)
```json
{"t":"error","path":"tests/test_broken.py","error":"SyntaxError","msg":"unexpected indent (line 15)"}
```
- `path` (string) — file that failed to collect.
- `error` (string) — exception type.
- `msg` (string) — message.

### `skip` (`-v` only)
```json
{"t":"skip","id":"tests/test_slow.py::test_big","reason":"not ready"}
```
- `id` (string) — node ID.
- `reason` (string) — skip reason, or `""` if none given.

### `summary` (always last)
```json
{"t":"summary","passed":30,"failed":2,"skipped":1,"errors":1,"duration":1.24,"rerun":["tests/test_auth.py::test_login","tests/test_net.py::TestNet::test_timeout","tests/test_broken.py"]}
```
- `passed` / `failed` / `skipped` / `errors` (int) — counts. All always present
  (including zeros) so the shape is stable.
- `duration` (float) — wall-clock seconds.
- `rerun` (array of strings, optional) — node IDs of failures plus paths of
  collection errors — arguments an agent can pass straight back to rustest to
  reproduce just the failures. Present only when there is something to re-run.

**Zero tests collected:** only `meta` and `summary` are emitted; `summary` has
all-zero counts and no `rerun`.

## Truncation

`stdout`/`stderr` default to the **last 50 lines** (the tail — output
immediately preceding the failure is the most relevant). When trimmed, the
dropped line count is reported in `stdout_omitted` / `stderr_omitted`.
`--llm-full` disables the cap and emits captured output in full.

## `--llm-schema`

Prints the JSON Schema describing every line type to stdout and exits 0. This is
the self-documenting contract: an agent runs `rustest --llm-schema` to learn the
format without reading external docs. The schema is versioned to match the
`meta.v` value.

## Architecture

Same integration points as v1 (Python-layer renderer, no Rust changes beyond the
already-landed `@mark.skip` discovery fix):

- `python/rustest/renderers/llm_renderer.py` — `LlmRenderer` rewritten to buffer
  results and emit JSONL at `finalize`. Sorting, truncation, node-ID
  construction, and expected/actual extraction live here.
- `python/rustest/cli.py` — `--llm` (unchanged trigger), new `--llm-schema` and
  `--llm-full` flags; `-v`/`-vv` verbosity mapped to renderer options.
- `python/rustest/core.py` — unchanged renderer swap; pass verbosity/full/schema
  options through.
- A JSON Schema document (embedded in the renderer module or a sibling constant)
  drives `--llm-schema` and stays in lockstep with `meta.v`.

### Node ID construction

`id` is the canonical pytest-style node ID. Implementation must assemble
`path::Class::test[param]` from available event data. If class/param context is
not exposed by the current events, the plan resolves how to surface it; the
minimum acceptable `id` is `path::test_name`.

## Edge cases

- **No tests collected:** `meta` + `summary` (all zeros), nothing else.
- **All skipped (default):** counted only; `meta` + `summary`. Under `-v`, one
  `skip` line each.
- **Empty exception message:** `msg` is `""` (not omitted).
- **`--llm-full`:** no truncation; `*_omitted` fields never appear.
- **Parallel workers (`-n`):** buffered + sorted output is identical regardless
  of worker completion order.
- **`--exitfirst` / `--last-failed` / `--failed-first`:** no format impact.

## Testing

Rewrite `python/tests/test_llm_renderer.py` around JSON parsing (assert on parsed
objects, not string matching):

- `meta` line present and first; `summary` present and last.
- All-pass: only `meta` + `summary`, zero counts, no `rerun`.
- Mixed fail/error/skip: correct line types, counts, and sort order.
- `expected`/`actual` populated for comparison assertions; absent otherwise.
- `stdout`/`stderr` attached to the correct failure; truncation adds
  `*_omitted`; `--llm-full` disables it.
- Verbose `-v`: `code` and `skip` lines appear. `-vv`: `frames` appears.
- `rerun` contains failing node IDs and errored paths; absent when all pass.
- Zero collected: `meta` + all-zero `summary`.
- Every emitted line is valid JSON; no ANSI, no non-ASCII decorators.
- `--llm-schema` prints valid JSON Schema and exits 0; `meta.v` matches schema
  version.

Integration: run rustest with `--llm` over a fixture suite; assert every stdout
line parses as JSON and the summary sentinel is last.

## Out of scope

- A general `--format=` system (add later if a second format is needed).
- Streaming output (buffered + sorted is deliberate; revisit only if a
  concrete very-large-suite need appears).
- Text/human output under `--llm` (removed entirely).
