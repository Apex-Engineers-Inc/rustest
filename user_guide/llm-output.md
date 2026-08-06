# LLM output mode (`--llm`)

`--llm` makes rustest emit **JSONL**, one JSON object per line, instead of the
human-oriented terminal output. It is built for LLM coding agents (Claude Code,
Cursor, Copilot, and similar tools) that *parse* test output rather than read it.

```bash
rustest tests/ --llm
```

```json
{"t":"meta","schema_version":2,"tool":"rustest","version":"1.0.0rc1","rootdir":"/repo","total":412}
{"t":"fail","id":"tests/test_auth.py::test_login","file":"tests/test_auth.py","line":42,"status":"failed","msg":"Traceback (most recent call last):\n  File \"/repo/tests/test_auth.py\", line 42, in test_login\n    assert response.status == 200\nAssertionError: assert 401 == 200","stdout":"POST /login user=admin"}
{"t":"summary","total":412,"passed":411,"failed":1,"skipped":0,"xfailed":0,"xpassed":0,"error":0,"deselected":0,"collection_errors":0,"duration":1.24,"exit_code":1}
```

**stdout is JSONL and nothing else.** Every diagnostic that has no line type (the
workers' own stderr, a `pytest.exit()` banner, a `--cov` table) goes to stderr, so
`rustest --llm > results.jsonl` is a valid document with nothing to strip. `--ascii`
and `--color` are accepted and inert, because rustest's output is already plain ASCII,
so there is nothing to override.

`--llm` **never changes the exit code**. 0 clean, 1 failures, 2 collection errors,
5 nothing collected, the same codes the human renderer returns for the same run.

## Why JSONL

An agent consuming test output needs two things: *what broke and why*, to act on;
and *did everything else stay green*, as one confirmation number. JSONL delivers
both with three properties that matter for machine consumption:

- **Unambiguous.** JSON string escaping removes the parsing ambiguity of
  space-delimited text. Test names, paths and messages all contain spaces and
  colons.
- **Deterministic.** Lines are emitted in manifest order (pytest's collection
  order), which the engine reassembles independently of which worker finished
  first. Identical failures produce identical bytes across runs and across `-n`,
  which is what makes prompt caching and run-to-run diffs work.
- **Signal-only.** Only failures and errors get lines by default. Passing and
  skipped tests are counted in the summary, so a green-but-for-two-failures run of
  5 000 tests costs three lines.

## Output structure

Lines are emitted once, at completion, in this fixed order:

1. `meta`, first line, the wire header.
2. `error`, one per collection error, then one per unattributable teardown failure.
3. `fail`, one per failed or errored test, in manifest order.
4. `skip`, one per skipped/xfailed/xpassed test, **only** under `-v`.
5. `summary`, last line.

Errors come before failures deliberately: a file that did not import is a larger
problem than an assertion that did not hold, because none of its tests were even
attempted.

::: {.callout-note title="Completion sentinel"}
The `summary` line is always the final line of a completed run. If it is
absent, the run was interrupted, a signal your tooling can rely on.
:::

## Line types

### `meta`

```json
{"t":"meta","schema_version":2,"tool":"rustest","version":"1.0.0rc1","rootdir":"/repo","total":412}
```

| Field | Meaning |
|-------|---------|
| `schema_version` | Wire version (integer). Bumped on any breaking change. |
| `tool` | Always `"rustest"`. |
| `version` | The rustest package version. |
| `rootdir` | Absolute rootdir, forward slashes. Every `id` and `file` below is relative to it. |
| `total` | Tests selected for this run, after `-k`/`-m` deselection, so a consumer knows the scale of the run from the first line. |

### `fail`

```json
{"t":"fail","id":"tests/test_net.py::TestNet::test_timeout","file":"tests/test_net.py","line":88,"status":"failed","msg":"Traceback (most recent call last):\n  File \"/repo/tests/test_net.py\", line 88, in test_timeout\n    client.connect()\nTimeoutError: connection timed out after 5s"}
```

| Field | Meaning |
|-------|---------|
| `id` | Node ID `path::Class::test[param]`, path relative to `rootdir`. |
| `file` | The `id` up to the first `::`, split out so you never have to parse it. |
| `line` | Line of the innermost traceback frame. **Omitted** when the message is not a traceback. |
| `status` | `failed` (the body raised) or `error` (setup or teardown raised, so the body never ran). |
| `msg` | The whole failure message. See [The message is the payload](#the-message-is-the-payload). |
| `stdout` / `stderr` | Captured output, present only when non-empty. See [Captured output](#captured-output). |
| `stdout_omitted` / `stderr_omitted` | Lines dropped by truncation, when it occurred. |

### `error`

A failure with no node ID to hang it on.

```json
{"t":"error","scope":"collection","file":"tests/test_broken.py","msg":"SyntaxError: unexpected indent (line 15)"}
{"t":"error","scope":"teardown","msg":"session fixture 'db': RuntimeError: pool already closed"}
```

| Field | Meaning |
|-------|---------|
| `scope` | `collection` means the file never imported, so none of its tests ran. `teardown` means a module- or session-scoped fixture raised at shutdown, after the last test it owned had already been reported. |
| `file` | Present for `scope: "collection"` only. A teardown failure the engine could not attribute has no path, and inventing one would be a guess. |
| `msg` | The engine's message, verbatim. |

::: {.callout-warning title="A collection error interrupts the run"}
Exactly as under pytest: one unimportable file exits 2 even when other files
collected, and the tests in them were never attempted. The sentinel says so,
because `total` will be 0, which is what stops an agent reading "no failures"
off an aborted run.
:::

### `skip` (with `-v`)

```json
{"t":"skip","id":"tests/test_slow.py::test_big","file":"tests/test_slow.py","status":"skipped","reason":"not ready"}
```

`status` is `skipped`, `xfailed` or `xpassed`. `xpassed` is here rather than
silently dropped: a non-strict XPASS is a test whose `xfail` mark has gone stale,
which is worth seeing. (A *strict* XPASS is reported `failed` and gets a `fail`
line.)

### `summary`

```json
{"t":"summary","total":412,"passed":409,"failed":1,"skipped":1,"xfailed":1,"xpassed":0,"error":0,"deselected":0,"collection_errors":0,"duration":1.24,"exit_code":1}
```

| Field | Meaning |
|-------|---------|
| `total` | Tests selected (post-deselection). |
| `passed` / `failed` / `skipped` / `xfailed` / `xpassed` / `error` | rustest's six status buckets, always present including zeros. |
| `deselected` | Removed by `-k` / `-m`. |
| `collection_errors` | Files that failed to import. Each has an `error` line above. |
| `duration` | Wall-clock seconds over the run, 3 decimal places. |
| `exit_code` | The code the process will return. `--llm` never changes it. |
| `stopped_early` | `true`, and **present only**, when `-x`/`--maxfail` cut the run short. |

The counts are the engine's own, not a re-tally of the lines above, so they are
correct at `-q`, where no `skip` line was emitted, and under `--maxfail`, where
`total` is the selection rather than what ran.

::: {.callout-important title="`stopped_early` matters"}
`{"failed":1,"passed":0,"total":9}` reads as "one failure in a suite of nine"
when eight of the nine were never attempted. Check for `stopped_early` before
concluding anything about the tests that produced no line.
:::

### `error` versus `error`

Two counts share the word and they are deliberately not the same number:

- `summary.error` counts **tests** whose setup or teardown raised. They ran, and
  they have `fail` lines with `"status":"error"`.
- `summary.collection_errors` counts **files** that never imported. They have
  `error` lines with `"scope":"collection"`, and only they mean the report is
  incomplete.

The human summary line folds them together, because a reader wants one number. A
machine reader can afford the distinction and needs it.

## The message is the payload

`msg` carries the worker's failure message **whole**: the frame-filtered traceback
whose final line is the assertion-rewritten comparison.

```
Traceback (most recent call last):
  File "/repo/tests/test_auth.py", line 42, in test_login
    assert response.status == 200
AssertionError: assert 401 == 200
```

That last line is the point. rustest rewrites assertions, so the message reports
the **values**, not just the source text: `assert 401 == 200`, not
`assert response.status == 200`. It is the single highest-value string in the whole
run for a coding agent, and it arrives without any parsing.

::: {.callout-note title="Changed in schema 2"}
Schema 1 (rustest 0.18) split the same string into `error`, `msg`, `expected`,
`actual`, `code` and `frames` using six regexes over the traceback. Six fields
said what one string already said, the `expected`/`actual` pair only appeared
for comparisons a regex happened to recognise, and the rewriting was lost in
the shredding. Schema 2 emits the message and lets the model read it.

The one thing still derived from the text is `line`, because the runner's
internal wire carries no line number. It is read off the last frame and
**omitted**, never `0` as schema 1 emitted, when there is no frame.
:::

## Verbosity

`-q` and `-v` shift what each failure costs, without changing the line order:

```bash
rustest tests/ --llm -q     # failures, no captured output: the cheapest useful mode
rustest tests/ --llm        # + captured stdout/stderr, last 50 lines
rustest tests/ --llm -v     # + one skip line per skipped/xfailed/xpassed test
```

`-qv` cancels out to the default rung, exactly as it does for the human output.

## Captured output

Captured `stdout`/`stderr` is attached to the failure that produced it, truncated
to the **last 50 lines**, because the output right before a failure is the part
worth spending tokens on. The number of dropped lines is reported in
`stdout_omitted` / `stderr_omitted`, so you can tell an empty prologue from a
discarded one, and decide whether re-running is worth it. `--llm-full` keeps
everything:

```bash
rustest tests/ --llm --llm-full
```

`--llm-full` on its own is a usage error (exit 4): with no `--llm` it would change
nothing, and rustest refuses inert flags rather than accepting them silently.

## Discovering the schema

`--llm-schema` prints a JSON Schema (draft 2020-12) describing every line type and
exits 0, so a tool can learn the format without external documentation:

```bash
rustest --llm-schema
```

It is a *query*, not a run: it answers before any collection happens, and it works
from a directory whose configuration would make the run itself fail. You should
not have to be standing in a working project to discover an output format. The
document's `version` matches the `schema_version` field on the `meta` line.

## The agent loop

The mode is designed around one loop, and the second half of it is what makes it
cheap:

```bash
rustest --llm           # find what is broken
# ...fix it...
rustest --lf --llm      # re-check only what failed
```

`--lf` (last-failed) narrows the *selection* to the tests that failed last time, so
the narrowing shows up in the JSONL for free: the second run's stream carries only
the relevant `fail` lines and a `total` equal to the number of failures. A warm
collect over a large suite is roughly 230 ms, so the inner loop costs a fraction of
a second and a handful of tokens instead of a full run and a full report.

This is why the `summary` line carries no `rerun` array. Schema 1 shipped one
because it had to: the loop was "read the ids back out and pass them to the next
invocation". rustest's last-failed cache does the same job with no id round-trip
and a warm collect, so the array would be duplicated state whose only consumer is a
worse version of `--lf`. The `fail` lines still carry every `id`, if you want to
select a subset yourself. Note that an `id` is **not** accepted as a path
argument: `rustest tests/test_x.py::test_a` runs the whole file. Narrow with `-k`,
or let `--lf` do it.

## Composition

| Flag | Under `--llm` |
|------|---------------|
| `-n` | No effect on the output. Lines are in manifest order at any worker count. |
| `-k` / `-m` | Narrow the run; `total` and `deselected` in the summary explain each other. |
| `--lf` / `--ff` | Reorder or narrow the selection; the stream follows. See above. |
| `-x` / `--maxfail` | Cut the run short and set `stopped_early` on the summary. |
| `--cov` | Works. The coverage table goes to **stderr** so stdout stays JSONL. |
| `--report-json` | Works. Independent of `--llm`; write both if you want the full report on disk. |
| `-s` | Works, but captures are then empty, because the tests wrote straight through to the terminal. |
| `--collect-only` | **Refused** (exit 4). It runs no test, so a `summary` line would be a well-formed lie; its stdout is already machine-readable, one node ID per line. |

## Edge cases

- **No tests collected:** `meta` and an all-zero `summary` (`exit_code: 5`). Still a
  complete document, so your parse should not need to special-case it.
- **All skipped (default verbosity):** counted in `summary`; individual `skip`
  lines appear only under `-v`.
- **Non-ASCII node IDs:** escaped (`\uXXXX`) rather than emitted raw, so the
  document survives a console whose encoding is not UTF-8.
- **Line endings in captures:** normalised to `\n`, so a Windows worker and a Linux
  one emit the same bytes for the same output.
