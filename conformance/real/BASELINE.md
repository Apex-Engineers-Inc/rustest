# `--real` baseline

Recorded 2026-11-10 on `350f640` plus the uncommitted working tree (fallback, live
display, protocol 11). Regenerate one suite with the commands below and rebuild this table with
`uv run python -m conformance --real-table` (reads `conformance/real/results/*.json`, writes
`conformance/real/results.md`). **Not for user-facing docs yet.**

## Commands

```bash
# one suite, strictly one process tree at a time; writes conformance/real/results/<suite>.json
uv run python -m conformance --real <suite> --real-tree
# a resumable sweep (skips suites that already have a result file)
uv run python -m conformance --real all --real-tree --real-resume
# merge the per-suite files into the table
uv run python -m conformance --real-table
```

Legs per suite, in order, never concurrent: (1) `pytest` serial, the oracle; (2) `rustest -n 4` as a user
runs it (fallback on), graded against 1, with the mode read from the report's `pytest_fallback`;
(3) `pytest -n 4` with pytest-xdist 3.8.0, wall-clock and a status diff, informational; (4) only for a
handed-off suite, `rustest --no-pytest-fallback -n 4`, so the engine is still measured.
`--real-tree` imports rustest from this checkout's `python/` and its built extension instead of the
wheel in the target's venv; it is what made a baseline possible without building a wheel per ABI.
xdist was installed into each target venv by the harness (`pytest-xdist==3.8.0`) when missing.

## Baseline

| Suite | Mode | Verdict | Tests | pytest s | pytest -n 4 s | rustest s | x pytest | x xdist | engine only s |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| attrs | handed off: tests/conftest.py::pytest_configure is a pytest hook rustest does not run | MATCH | 1387 | 12.0 | 8.41 | 10.65 | 1.13x | 0.79x | 5.13 (MATCH) |
| cachetools | native | MATCH | 291 | 5.66 | 7.78 | 5.05 | 1.12x | 1.54x |  |
| click | native | MATCH | 1686 | 6.08 | 6.26 | 2.71 | 2.24x | 2.31x |  |
| dateutil | handed off: tests/conftest.py::pytest_collection_modifyitems is a pytest hook rustest does not run | MATCH | 2095 | 6.27 | 7.03 | 8.2 | 0.76x | 0.86x | 2.36 (MATCH) |
| humanize | native | MATCH | 784 | 2.85 | 4.16 | 1.73 | 1.65x | 2.40x |  |
| jsonschema | native | MATCH | 7815 | 18.22 | 8.84 | 7.74 | 2.35x | 1.14x |  |
| marshmallow | native | EXPLAINED | 1178 | 2.66 | 3.67 | 1.27 | 2.09x | 2.89x |  |
| more-itertools | native | MATCH | 722 | 9.29 | 8.26 | 5.68 | 1.64x | 1.45x |  |
| pillow | handed off: conformance/real/_work/pillow/Tests/conftest.py::pytest_configure is a pytest hook rustest does not run | MATCH | 4036 | 68.08 | 21.3 | 24.34 | 2.80x | 0.88x | 35.59 (MATCH) |
| rich | native | EXPLAINED | 1007 | 7.94 | 6.25 | 4.25 | 1.87x | 1.47x |  |
| sqlparse | native | MATCH | 482 | 2.99 | 4.72 | 1.43 | 2.09x | 3.30x |  |

`x pytest` and `x xdist` are the other runner's seconds divided by rustest's. Engine-only column is the
`--no-pytest-fallback` leg (its verdict in brackets).

## Caveats

- **One reading per cell** on a Windows 11 laptop with 16 logical CPUs and about 2.4 GB of free memory,
  with other agents building at times. Treat differences under about 20 percent as noise. Nothing here
  is a benchmark.
- **A handed-off suite's "rustest" leg is pytest running with `-n 4`**, because `-n 4` is passed through
  to pytest and xdist is installed. Its time is therefore xdist's, plus rustest's thrown-away collection
  (about a second), not "pytest's speed". attrs, dateutil and pillow are handed off by a conftest
  `pytest_configure` or `pytest_collection_modifyitems`.
- **Not run:** jinja2, fastapi, werkzeug, pynite and member-designer, whose venvs are a different ABI
  (3.13 or free-threaded) from the extension built in `python/` and need a wheel per ABI (maturin); and
  psutil (about 30 minutes alone, run monthly). Their previous results are in the old design notes, not here.
- Node ids carry a `conformance/real/_work/<suite>/` prefix for sqlparse and pillow, because the rootdir
  resolves to this repository's `pyproject.toml`; both runners agree, so it grades fine.

## What the baseline found

- **sqlparse and click first diverged** (7 and 5 `error`s, all `tmpdir`/`tmpdir_factory` tests:
  `RuntimeError: the 'py' library is required`). Root cause: pytest's `py.py` imports
  `_pytest._py`, which the worker's `_pytest` stub hid. Before c129388 the worker happened to import
  `py` at module level (in `builtin_fixtures.py`) *before* the stub was installed, which loaded the real
  `_pytest` and made the stub a no-op; that cleanup removed the import and exposed the gap. Fixed by
  exposing the real `_pytest/_py` directory as the stub's `_pytest._py` (`compat/pytest.py`); both suites
  re-ran and are MATCH in the table above. Pinned by `test_builtins.py` and the corpus case
  `builtins/tmpdir-legacy`.
- rich is EXPLAINED by its one structural `test_suppress` ledger entry; marshmallow by its
  nondeterministic datetime ids.

## Entry-point plugin allowlist (re-checked against this baseline)

After the allowlist landed (`_fallback.SAFE_PLUGINS`; scan cost about 5 ms in a small venv and 20 ms in
member-designer's, on every invocation that may fall back), the eight native suites were re-run
one at a time with `--real-tree` and all stayed native with unchanged verdicts: their only
auto-loaded plugin is `xdist`, which the harness installs. Not re-run: attrs and dateutil (already handed
off by a conftest hook; they now also name `hypothesispytest`), pillow (only `xdist`, so unchanged).
The suites that would newly hand off are the ones whose venvs hold `hypothesispytest` (attrs,
dateutil), `anyio` (fastapi, pynite, member-designer) or `timeout` (werkzeug); none of those were in
the native set measured here.
