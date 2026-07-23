# LLM Output Mode (`--llm`)

The `--llm` flag makes rustest emit **JSONL** — one JSON object per line —
instead of the human-oriented rich output. It is built for LLM coding agents
(Claude Code, Cursor, Copilot, and similar tools) that *parse* test output rather
than read it.

```bash
rustest tests/ --llm
```

```json
{"t":"meta","v":1,"tool":"rustest","version":"0.17.0"}
{"t":"fail","id":"tests/test_auth.py::test_login","line":42,"error":"AssertionError","msg":"expected 200, got 401","expected":"200","actual":"401","stdout":"login user=admin"}
{"t":"summary","passed":30,"failed":1,"skipped":1,"errors":0,"duration":1.24,"rerun":["tests/test_auth.py::test_login"]}
```

`--llm` implies `--ascii` and `--color never`; any conflicting flags are silently
overridden. There is no ANSI, no Unicode, and no progress output — stdout is pure
JSONL.

## Why JSONL

An agent consuming test output needs two things: *what broke and why* (to act on)
and *did everything else stay green* (one confirmation number). JSONL delivers
exactly that with three properties that matter for machine consumption:

- **Unambiguous.** JSON string escaping removes the parsing ambiguity of
  space-delimited text (test names, paths, and messages all contain spaces and
  colons).
- **Deterministic.** Output is buffered and sorted, so identical failures produce
  identical bytes across runs — enabling prompt caching and meaningful diffs
  between runs.
- **Signal-only.** Only failures and collection errors get lines. Passing and
  skipped tests are counted in the summary, keeping both token cost and reasoning
  noise minimal.

## Output structure

Lines are emitted once, at completion, in this fixed order (each group sorted):

1. `meta` — first line, a version header.
2. `error` — one per collection/setup error, sorted by path.
3. `fail` — one per failure, sorted by `(file, line)`.
4. `skip` — one per skipped test, **only** under `-v`, sorted by id.
5. `summary` — last line.

!!! note "Completion sentinel"
    The `summary` line is always the final line of a completed run. If it is
    absent, the run was interrupted — a signal your tooling can rely on.

## Line types

### `meta`

```json
{"t":"meta","v":1,"tool":"rustest","version":"0.17.0"}
```

| Field | Meaning |
|-------|---------|
| `v` | Schema version (integer). Bumped on breaking format changes. |
| `tool` | Always `"rustest"`. |
| `version` | The rustest package version. |

### `fail`

```json
{"t":"fail","id":"tests/test_net.py::TestNet::test_timeout","line":88,"error":"TimeoutError","msg":"connection timed out after 5s"}
```

| Field | Meaning |
|-------|---------|
| `id` | Canonical node ID `path::Class::test[param]`. The file is `id` up to the first `::` (no separate `file` field). |
| `line` | The failing source line number. |
| `error` | Exception type name (e.g. `AssertionError`, `TimeoutError`). |
| `msg` | Exception message (`""` when there is none). |
| `expected` / `actual` | Present only when rustest captured comparison values. |
| `stdout` / `stderr` | Present only when non-empty (see [Captured output](#captured-output)). |
| `stdout_omitted` / `stderr_omitted` | Lines dropped by truncation, when it occurred. |
| `code` | The failing source line — added with `-v`. |
| `frames` | Traceback frame chain `[{file, line, fn}]`, outermost first — added with `-vv`. |

### `error` (collection)

```json
{"t":"error","path":"tests/test_broken.py","error":"SyntaxError","msg":"unexpected indent (line 15)"}
```

### `skip` (with `-v`)

```json
{"t":"skip","id":"tests/test_slow.py::test_big","reason":"not ready"}
```

### `summary`

```json
{"t":"summary","passed":30,"failed":2,"skipped":1,"errors":1,"duration":1.24,"rerun":["tests/test_auth.py::test_login","tests/test_broken.py"]}
```

| Field | Meaning |
|-------|---------|
| `passed` / `failed` / `skipped` / `errors` | Counts (always present, including zeros). |
| `duration` | Wall-clock seconds. |
| `rerun` | Node IDs of failures plus paths of collection errors — pass them straight back to reproduce only what failed. Present only when there is something to re-run. |

## Verbosity

The standard `-v` / `-vv` flags add failure detail:

```bash
rustest tests/ --llm        # failures + summary
rustest tests/ --llm -v     # + code line, + skip lines
rustest tests/ --llm -vv    # + frame chain
```

- `-v` adds the failing source line (`code`) to each failure and emits a `skip`
  line (with `reason`) for each skipped test.
- `-vv` additionally adds the traceback `frames` chain.

## Captured output

Captured `stdout`/`stderr` is attached to the failure that produced it. By
default it is truncated to the **last 50 lines** (the output right before a
failure is the most relevant), and the number of dropped lines is reported in
`stdout_omitted` / `stderr_omitted`. Use `--llm-full` to keep everything:

```bash
rustest tests/ --llm --llm-full
```

## Discovering the schema

`--llm-schema` prints a machine-readable description of every line type and
exits, so a tool can learn the format without external documentation:

```bash
rustest --llm-schema
```

The document's `version` matches the `v` field on the `meta` line, so a consumer
can detect format changes.

## Re-running only failures

The `summary` line's `rerun` array lists exactly what to pass back to rustest to
reproduce the failures (and re-collect the errored files):

```bash
# Example: feed the rerun ids straight back
rustest tests/test_auth.py::test_login tests/test_broken.py
```

## Edge cases

- **No tests collected:** only `meta` and `summary` (all-zero counts) are emitted.
- **All skipped (default):** counted in `summary`; individual `skip` lines appear
  only under `-v`.
- **Parallel workers (`-n`):** output is identical regardless of worker
  completion order, because it is buffered and sorted.
