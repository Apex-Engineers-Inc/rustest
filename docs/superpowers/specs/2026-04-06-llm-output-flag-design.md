# `--llm` Flag: Token-Efficient LLM Output Mode

## Overview

Add a `--llm` flag to rustest that produces minimal, token-efficient plain text output optimized for consumption by LLM-based tools (Claude Code, Cursor, Copilot, etc.).

## CLI Interface

**Flag:** `--llm`

Implicit overrides when `--llm` is present:
- Forces `--ascii` (no unicode)
- Forces `--color never` (no ANSI codes)
- Conflicting flags silently overridden (no warnings)

**Verbose mode:** `--llm -v` reuses the existing `-v` flag to add code snippets and assertion values to failure output.

## Output Format

### Default (`--llm`)

**All passing:**
```
32 passed 1.2s
```

**With failures:**
```
FAIL test_login tests/test_auth.py:42 AssertionError: expected 200, got 401
FAIL test_timeout tests/test_net.py:88 TimeoutError: connection timed out after 5s
30 passed 2 failed 1.2s
```

**With skips:**
```
FAIL test_login tests/test_auth.py:42 AssertionError: expected 200, got 401
30 passed 1 failed 1 skipped 1.2s
```

**With collection errors:**
```
ERROR tests/test_broken.py SyntaxError: unexpected indent (line 15)
32 passed 1 error 1.2s
```

**With captured stdout/stderr (failures only):**
```
FAIL test_login tests/test_auth.py:42 AssertionError: expected 200, got 401
stdout: Attempting login for user=admin
stderr: WARNING: rate limit approaching
30 passed 2 failed 1.2s
```

### Verbose (`--llm -v`)

Adds code snippet and assertion values under each failure:
```
FAIL test_login tests/test_auth.py:42 AssertionError: expected 200, got 401
  > assert response.status_code == 200
  values: response.status_code = 401
stdout: Attempting login for user=admin
30 passed 1 failed 1.2s
```

### Pytest Compatibility Mode (`--llm --pytest-compat`)

Single one-liner instead of the full rich banner:
```
pytest-compat mode
FAIL test_login tests/test_auth.py:42 AssertionError: expected 200, got 401
30 passed 1 failed 1.2s
```

### Format Rules

- No blank lines between failures
- Summary line is always last
- Zero counts omitted from summary (no `0 skipped`)
- No progress output during execution — output only at completion
- No headers, banners, or decorative text (except pytest-compat one-liner)
- No truncation of error messages

## Architecture

### Approach: Python-layer renderer (no Rust changes)

New file `python/rustest/renderers/llm_renderer.py` containing `LlmRenderer` class.

### LlmRenderer

Implements the same event consumer interface as `RichRenderer`:
- `handle(event)` receives events from `EventRouter`
- Buffers all results internally during execution (no mid-run output)
- On `SuiteCompletedEvent`, emits final plain text output to stdout
- Accepts a `verbose` flag (from `-v`) to control failure detail level

**Data tracked internally:**
- Pass/fail/skip/error counts
- Duration
- List of failures: test name, file:line, error message, captured stdout/stderr
- In verbose mode: code snippet + assertion values per failure
- Collection errors

### Changes to Existing Files

- `cli.py` — add `--llm` flag to argparse, resolve implicit overrides (ascii=True, color=never)
- `core.py` — when `llm=True`, instantiate `LlmRenderer` instead of `RichRenderer`, replace pytest-compat banner with one-liner
- No Rust changes required

## Edge Cases

- **No tests collected:** `0 collected 0.0s`
- **All skipped:** `5 skipped 0.1s`
- **`--exitfirst`:** outputs the single failure + summary, same format
- **`--last-failed` / `--failed-first`:** no impact on format
- **`--no-capture`:** stdout/stderr streams live to terminal, failure summary still emitted at end
- **`-n` (parallel workers):** no impact, renderer consumes events regardless of source
- **Empty error messages:** `FAIL test_foo tests/test.py:10 (no message)`
- **Very long error messages:** no truncation

## Testing

### Unit Tests (LlmRenderer)
- All pass case
- Mixed pass/fail/skip/error
- Verbose mode output
- Captured stdout/stderr on failure
- Collection errors
- Pytest-compat one-liner
- Zero tests collected

### Integration Tests
- Run rustest with `--llm` on fixture suite, assert no ANSI codes, no unicode, no progress output, correct summary format

### Flag Interaction Tests
- `--llm --color always` silently overrides to no color
- `--llm -v` produces verbose output
- `--llm --pytest-compat` shows one-liner instead of banner
