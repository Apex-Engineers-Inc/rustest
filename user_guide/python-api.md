# Python API

`rustest.run()` runs a suite from inside Python and returns pytest's exit code. It is the
same entry point the `rustest` command uses — the CLI parses arguments and calls this.

!!! warning "This function changed shape in the v2 release"
    `run()` used to take arguments including `capture_output`, `pytest_compat`, `ascii`,
    `no_color` and `verbose`, and it returned a `rustest.reporting.RunReport` object. It is
    now **keyword-only**, takes a different set of arguments, and returns an **`int`**.

    It is an alias for the v2 entry point rather than a translating wrapper, and that is
    deliberate: a shim would have accepted `pytest_compat=False` and silently done the
    opposite (the compatibility shim is unconditional now), and would have returned an
    integer where the old type hint promised a `RunReport`. An old call raises `TypeError`
    immediately, naming the keyword it does not recognise. See the
    [upgrade guide](migration-guide.md).

## Basic usage

<!--rustest.mark.skip-->
```python
from rustest import run

exit_code = run(paths=["tests"])

if exit_code == 0:
    print("all green")
```

`paths` is the only argument most callers pass. An **empty** sequence means "no path
argument was given", which lets a `testpaths` ini setting decide the roots exactly as it
would under pytest.

## Exit codes

`run()` returns pytest's exit codes, unchanged:

| Code | Meaning |
|---:|---|
| `0` | All tests passed |
| `1` | One or more tests failed or errored |
| `2` | Session interrupted — a collection error, or `pytest.exit()` |
| `4` | Usage error |
| `5` | No tests were collected |

<!--rustest.mark.skip-->
```python
import sys

from rustest import run

sys.exit(run(paths=["tests", "examples"]))
```

## Arguments

All keyword-only.

| Argument | Type | Default | CLI equivalent |
|---|---|---|---|
| `paths` | `Sequence[str]` | *required* | positional paths |
| `workers` | `int \| None` | `None` (4, capped by CPU count) | `-n` / `--workers` |
| `keyword` | `str \| None` | `None` | `-k` |
| `mark_expr` | `str \| None` | `None` | `-m` |
| `report_json` | `str \| None` | `None` | `--report-json` |
| `fail_fast` | `bool` | `False` | `-x` |
| `max_fail` | `int` | `0` (no limit) | `--maxfail` |
| `last_failed_mode` | `"none"` / `"only"` / `"first"` | `"none"` | `--lf` / `--ff` |
| `capture` | `bool` | `True` | `-s` clears it |
| `codeblocks` | `bool` | `True` | `--no-codeblocks` clears it |
| `verbosity` | `int` | `0` | `-q` is `-1`, `-v` is `1` |
| `cov` | `Sequence[str] \| None` | `None` | `--cov` |
| `cov_report` | `Sequence[str] \| None` | `None` | `--cov-report` |
| `llm` | `bool` | `False` | `--llm` |
| `llm_full` | `bool` | `False` | `--llm-full` |

`cov=None` and `cov=[""]` are not the same thing: `None` means no coverage at all, and is
the only value that leaves the workers with no `sys.monitoring` tool registered. `[""]` —
what a bare `--cov` produces — means "measure the rootdir".

## Getting results, not just a code

`run()` returns a number. When you need the detail, pass `report_json=` and read the file
it writes:

<!--rustest.mark.skip-->
```python
import json

from rustest import run

run(paths=["tests"], report_json="report.json", verbosity=-1)

with open("report.json", encoding="utf-8") as fh:
    report = json.load(fh)

print(report["summary"]["passed"], "passed")
print(report["summary"]["failed"], "failed")

for test in report["tests"]:
    if test["status"] in {"failed", "error"}:
        print(test["id"], "->", test["message"].splitlines()[-1])
```

The report is schema version 2 and looks like this:

<!--rustest.mark.skip-->
```json
{
  "version": 2,
  "rootdir": "/path/to/project",
  "exit_code": 1,
  "summary": {
    "total": 2, "passed": 1, "failed": 1, "skipped": 0,
    "xfailed": 0, "xpassed": 0, "error": 0, "deselected": 0,
    "duration": 0.438
  },
  "tests": [
    {"id": "test_x.py::test_a", "status": "passed", "duration": 0.0033},
    {"id": "test_x.py::test_b", "status": "failed", "duration": 0.0035,
     "message": "Traceback (most recent call last):\n  ...\nAssertionError: assert 0"}
  ],
  "collection_errors": []
}
```

`tests` is in manifest order — the same order at any `-n` — so two runs over the same tree
produce comparable files without sorting.

There are six statuses, not four: `passed`, `failed`, `skipped`, `xfailed`, `xpassed` and
`error`. `error` means setup or teardown raised, as distinct from the test body failing.

### Or use `--llm` for a stream

If the consumer is a tool rather than a script — an agent, a CI annotator — the
[`--llm`](llm-output.md) JSONL mode is usually a better fit than a JSON file: it is
failures-only by default, it streams as the run proceeds, and it has a published schema
(`rustest --llm-schema`).

## Patterns

### A selective test task

<!--rustest.mark.skip-->
```python
from rustest import run


def check(*, slow: bool = False) -> int:
    """Run the fast tests, or everything."""
    return run(
        paths=["tests"],
        mark_expr=None if slow else "not slow",
        fail_fast=True,
    )
```

### The iterate-on-failures loop

<!--rustest.mark.skip-->
```python
from rustest import run

# First pass: everything.
if run(paths=["tests"]) != 0:
    # ... make a fix ...
    # Second pass: only what failed, off a warm collection.
    run(paths=["tests"], last_failed_mode="only")
```

### Coverage from Python

<!--rustest.mark.skip-->
```python
from rustest import run

run(
    paths=["tests"],
    cov=["src"],
    cov_report=["term", "xml:coverage.xml"],
)
```

Needs the `cov` extra (`pip install 'rustest[cov]'`). See [Coverage](coverage.md).

### Quieting the run

`verbosity=-1` is `-q`: the summary line and nothing else. The human output always goes to
the process's streams — `run()` never returns it as a string — so if you need it captured,
capture the streams or use `report_json`.

## What is not here

`run()` is a *runner*, not a framework API. There is no programmatic hook for modifying
collection, registering fixtures from outside a `conftest.py`, or subscribing to test
events as they happen — rustest has no plugin or hook system, by design
([why](pytest-plugins.md)). If you need to react to individual results, read the
`report_json` file or consume [`--llm`](llm-output.md).

## Next Steps

- [CLI Usage](cli.md) — every argument above, as a flag
- [LLM Output](llm-output.md) — the streaming JSONL alternative to `report_json`
- [API Reference](../reference/index.html) — decorators, fixtures, `approx`, exception types
- [Writing Tests](writing-tests.md) — create tests to run with the API
