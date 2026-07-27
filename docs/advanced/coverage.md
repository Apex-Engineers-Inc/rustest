# Coverage (`--cov`)

rustest measures line coverage itself, with [PEP 669](https://peps.python.org/pep-0669/)
`sys.monitoring`, and writes the result as an ordinary **coverage.py data file**. Nothing about
the output is rustest-specific: `coverage report`, `coverage html`, Codecov, diff-cover and
everything else that reads `.coverage` works on it unchanged.

## Install

`coverage` is an optional extra, so a run that never asks for coverage never pays for it:

```bash
pip install 'rustest[cov]'
```

Without it, `--cov` exits 4 with a message saying exactly that.

## Use

```bash
rustest --cov=src tests/                     # measure src/, print the terminal table
rustest --cov tests/                         # measure the whole rootdir
rustest --cov=src --cov=plugins tests/       # two source trees
rustest --cov=src --cov-report=xml tests/    # Cobertura XML at coverage.xml
rustest --cov=src --cov-report=xml:build/cov.xml --cov-report=term tests/
```

`--cov` takes a **path to a directory**. With no value it measures the rootdir — the directory
whose config file rustest resolved, which is not necessarily the one you are standing in.

The combined data lands in `.coverage`, in the directory you ran from, which is coverage.py's
own default. So this works with no further arguments:

```bash
rustest --cov=src tests/
coverage html          # or: coverage report, coverage json, coverage lcov, coverage annotate
```

Your project's `[report]` settings — `[tool.coverage.report]` in `pyproject.toml`, or the
`[report]` section of `.coveragerc` — apply to the terminal and XML output, because those
reports *are* coverage.py's: `exclude_lines`, `exclude_also`, `omit`, `include`, `precision`,
`skip_covered`, `sort`. `fail_under` is **read but not acted on**: rustest's exit code belongs
to the tests, so a coverage shortfall does not change it. Run `coverage report` after the run
if you want `fail_under` enforced.

## What is supported

| | |
| --- | --- |
| `--cov[=DIR]` | ✅ repeatable; no value means the rootdir |
| `--cov-report=term` | ✅ the default when `--cov` is given |
| `--cov-report=xml[:PATH]` | ✅ Cobertura, default `coverage.xml` |
| `--cov-report=html`, `json`, `lcov`, `annotate`, `term-missing` | ❌ run the `coverage` CLI on the `.coverage` file rustest wrote |
| `--cov-branch` | ❌ **refused loudly** — see below |
| `branch = True` in `.coveragerc` / `[tool.coverage.run]` | ❌ refused the same way, before the run — see below |
| `--cov-append`, `--cov-config`, `--cov-context`, `--cov-fail-under`, `--no-cov` | ❌ not implemented |

## Branch coverage is deferred, not approximated

`--cov-branch` exits 4 rather than measuring lines and calling it branches. Reporting line
coverage against a threshold a user set for *branches* overstates it, and a coverage tool that
reports a number higher than the truth is worse than one that refuses.

**`branch = True` in your coverage configuration is refused the same way**, and that is the
case worth knowing about: it is invisible on the command line. On a suite whose honest branch
coverage is 75 %, the silently degraded line report reads 81 % — with no Branch or BrPart
columns to hint that the setting was ignored. rustest exits 4 before running anything instead:

```
ERROR: branch = True in the coverage configuration asks for branch coverage, which rustest
does not implement: it measures line coverage only. ...
```

If you need branch data today:

```bash
coverage run --branch --source=src -m rustest tests/
coverage report
```

That runs rustest under coverage.py's own tracer, which measures branches — at coverage.py's
cost rather than `sys.monitoring`'s.

## How it works, and what it costs

Every worker enables `sys.monitoring` `PY_START` events before it imports anything. The handler
decides once per file whether that file is inside a measured tree; if it is, it arms `LINE`
events **on that code object only** and, either way, answers `DISABLE` so the code object never
fires `PY_START` again. The `LINE` handler records the line and answers `DISABLE` too, retiring
that location for the rest of the run. This is the shape coverage.py's own `sysmon` core uses.

The consequence is the cost profile:

| | measured |
| --- | --- |
| a run **without** `--cov` | no monitoring tool is registered at all — exactly zero |
| a line in a loop, after its first execution | +0.001–0.004 µs per call (indistinguishable from noise) |
| the first execution of a code object in a measured file | ≈ 3–7 µs, once |
| the first execution of a code object **outside** the measured trees | ≈ 0.4–0.5 µs, once |
| `import coverage`, per worker, at start-up | ≈ 250 ms |

So the per-test cost is proportional to how much *new* code a test reaches, not to how long it
runs, and it is zero when `--cov` is absent.

Each worker writes its own `.coverage.<host>.<pid>.<random>` file — coverage.py's parallel-mode
naming — and the orchestrator merges them with `Coverage.combine`, the same code path
`coverage combine` runs after `coverage run -p`.

## Accuracy

rustest's executed line sets are diffed against coverage.py's own runs of the same suites. On
CPython 3.14 they are **identical** to coverage.py's default (`sysmon`) core, including
import-time lines, conftest bodies, fixture teardowns run at session end, generators, async
tests, `unittest.TestCase` classes and files the suite never imported (which report 0 %, as
they do under `coverage run`).

One documented difference, and it is a difference between coverage.py's *own* two cores rather
than between rustest and coverage.py: a bare annotation in a class body (`x: int`) exists only
in the PEP 649 `__annotate__` code object. coverage.py's `sysmon` core skips those code objects
outright and its C tracer does not, so the C tracer records the line and `sysmon` does not.
rustest skips them, i.e. it agrees with `sysmon` — coverage.py's default on 3.14.
