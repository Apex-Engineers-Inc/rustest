# pytest Compatibility

**pytest compatibility is not a mode. It is what rustest is.**

`rustest tests/` runs your existing pytest suite. `import pytest` inside a test module
resolves to rustest's own implementation, always — there is no flag to turn it on, because
there is no configuration in which it is off.

```bash
# no installation needed
uvx rustest tests/

# or install first
pip install rustest
rustest tests/
```

!!! warning "`--pytest-compat` was removed"
    It used to opt into a *compatibility mode*. The v2 engine flip made that mode the
    default and the only behaviour, so the flag could only have been a no-op or a lie.
    Passing it now exits **4** with a pointer to `CHANGELOG.md`, and so does `--v1` — see
    [The legacy engine](#the-legacy-engine) below.

There is one engine, so everything on this page describes it.

## What this page is for

Compatibility is a claim, and a claim needs evidence. rustest's is a
[conformance corpus](https://github.com/rustest/rustest/tree/main/conformance): every case
in it is run through **real pytest** and through rustest, and the collected node IDs, the
per-outcome tallies and the process exit code are diffed. Every known divergence is written
down in a ledger with its mechanism. This page is the human-readable summary of the same
territory: what works, what does not yet, and what the gap costs you.

## Supported

### Decorators

- `@pytest.fixture` — `function`, `class`, `module`, `package` and `session` scopes
- `@pytest.fixture(params=[...])` — fixture parametrization, with `request.param`
- `@pytest.mark.parametrize()`, including stacked and `pytest.param(..., id=...)`
- `@pytest.mark.skip()` / `@pytest.mark.skipif()`, called **and bare**
- `@pytest.mark.xfail()`, including `strict=`, `raises=` and `run=False`
- `@pytest.mark.usefixtures()`
- `@pytest.mark.asyncio` — async tests, no plugin needed
- Custom marks, including module-level `pytestmark` and class-level marks
- `unittest.TestCase` classes, with `setUp`/`tearDown`/`setUpClass` and `unittest`'s own
  skip decorators

### Functions

`pytest.raises()`, `pytest.skip()`, `pytest.xfail()`, `pytest.fail()`, `pytest.approx()`,
`pytest.warns()`, `pytest.deprecated_call()`, `pytest.param()`, `pytest.importorskip()`.

### Built-in fixtures

The default engine provides **`tmp_path`, `tmp_path_factory`, `tmpdir`, `tmpdir_factory`,
`monkeypatch`, `capsys`, `capfd`, `caplog`, `cache`, `mocker`, `pytestconfig`** and
`request`.

`capfd` captures at the **file-descriptor** level, as pytest's does — a `subprocess`, a C
extension or a bare `os.write(1, ...)` is captured, not just a `print()`. As under pytest,
`capsys` and `capfd` cannot both be requested by one test (`cannot use capfd and capsys at
the same time`).

`caplog` installs its handler on the root logger and, like pytest's, **changes no level**:
the root logger keeps its default `WARNING`, so `logging.info(...)` is not captured until
you call `caplog.set_level(logging.INFO)`. A logger with `propagate = False` is not captured
— under pytest either.

`cache` is pytest's `config.cache` API over `.rustest_cache/v2`, sharing the store `--lf`
writes: `cache.get("cache/lastfailed", {})` answers with the last run's failures.

The remaining pytest built-ins are **not implemented yet**, and requesting one is a loud
error that names it rather than pytest's generic `fixture 'x' not found` — because "not
found" would send you hunting for a missing `@fixture` that was never yours to write:

```text
FixtureLookupError: fixture 'recwarn' is not supported by the rustest v2 worker yet
(supported builtins: tmp_path_factory, tmp_path, tmpdir_factory, tmpdir, monkeypatch,
capsys, capfd, caplog, cache, mocker, pytestconfig)
```

The full not-yet list: `capfdbinary`, `capsysbinary`, `capteesys`, `doctest_namespace`,
`pytester`, `record_property`, `record_testsuite_property`, `record_xml_attribute`,
`recwarn`, `testdir`. The `*binary` capture pair and `capteesys` need a bytes-flavoured
capture class; `recwarn` needs a warnings channel; `record_*` need an XML report;
`pytester`/`testdir` are pytest's own in-process harness.

### The `request` object

`request.param`, `request.scope`, `request.fixturename`, `request.node`,
`request.addfinalizer()`, `request.getfixturevalue()`, `request.applymarker()`,
`request.instance`, `request.cls`, `request.function`, `request.module`, `request.path`,
`request.keywords`, `request.config`.

`request.config` is a **subset** and loud past its edge: `rootpath`, `inipath` (always
`None`), `invocation_params.dir`, `cache`, and `getini` for the six values the run carries
(`python_files`, `python_classes`, `python_functions`, `asyncio_mode`,
`asyncio_default_fixture_loop_scope`, `asyncio_default_test_loop_scope`). Any other ini name
raises, and the two refusals are worded apart — a real pytest ini this engine does not carry
says so, an unrecognised name gets pytest's `unknown configuration value`. `getoption(name,
default)` returns the default; without one it raises `no option named 'x'`, because no
command-line option travels to the worker and a fabricated answer would let a suite report
on a mode it never ran in.

```python
import pytest


@pytest.fixture(params=[1, 2, 3])
def number(request):
    return request.param


@pytest.fixture
def conditional_setup(request):
    if request.node.get_closest_marker("slow"):
        pytest.skip("skipping slow test")
    return request.node.name
```

`request.node` is a **façade** over the execution plan, not a collection-tree node — v2
replaced the tree with a flat manifest — so it answers `name`, `nodeid`, `own_markers`,
`iter_markers()`, `get_closest_marker()` and `add_marker()`, and nothing else.

**`request.config` and `request.session` do not exist**, and neither does `node.config`,
`node.session` or `node.parent`. Accessing one raises `AttributeError`. This is a
deliberate choice over the alternative: a stub that answers plausibly — `getoption()`
returning your default, `getini()` returning `None` — turns a missing feature into a silent
wrong answer inside a fixture that decides which database to connect to. An
`AttributeError` naming the attribute is a worse morning and a better outcome.

If you have `request.config.getoption("--db-url")` in a conftest, read the environment
instead for now:

```python
import os

import pytest


@pytest.fixture
def database():
    return connect(os.environ.get("DB_URL", "sqlite:///:memory:"))
```

## Not supported

**pytest plugins.** By design. See the
[Plugin Compatibility Guide](pytest-plugins.md) for alternatives to the popular ones.

**`_pytest` internals.** `_pytest.assertion.rewrite`, `_pytest.fixtures`, `_pytest.config`,
`_pytest.nodes` and friends are import-shimmed so that a module which *imports* them still
loads, but they are non-functional stubs. Code that actually calls into them will not work.

**The hook system.** No `pytest_configure`, no `pytest_collection_modifyitems`, no
`pytest_generate_tests`. A `conftest.py` that defines hooks loads fine and its **fixtures
are used**; its hooks are ignored.

**Warnings.** There is no warnings channel yet, so pytest's diagnostics — the
`PytestCollectionWarning` for a class with `__init__`, the "usefixtures() without arguments
has no effect" note — are not printed. The *behaviour* in each case matches pytest; only
the message is missing.

## Known gaps, with their cost

These are the divergences the conformance corpus has found and pinned. Each is a real case
in the corpus, so none of them can regress unnoticed or be quietly forgotten.

| gap | what happens | cost |
| --- | --- | --- |
| **`pytest.exit(returncode=N)`** | `pytest.exit()` itself now stops the session and exits 2, pytest's answer. The `returncode=` payload is not honoured (the worker exit code is the whole channel), and an `exit()` at *import* time surfaces as exit 3 rather than 2. | A custom exit code is lost. Corpus case `marks/pytest-exit`. |
| **Session fixtures across workers** | A `session`-scoped fixture is built once per **worker** process, and a worker is handed a subset of the files. At `-n 1` that is pytest's behaviour exactly; across a pool it is pytest-xdist's. | Cross-file shared state works within a worker, not across the pool. Corpus case `fixtures/session-scope`. |
| **No item reordering** | pytest groups tests that share a higher-scoped parametrized fixture; rustest keeps source order. | A module-scoped `params=["a", "b"]` fixture costs 2 setups under pytest and 4 here. Corpus case `fixtures/module-param-reorder`. |
| **`package` scope** | Cached for the worker's lifetime; not torn down at the package boundary. | Late teardown. |
| **Async concurrency** | Async tests in the same loop scope run sequentially — as they do under pytest-asyncio, which drives each coroutine through a non-re-entrant `asyncio.Runner.run`. | No wall-clock overlap, and none under pytest either. Listed so it is not mistaken for a divergence; `loop_scope` itself is implemented. |
| **`PYTEST_ADDOPTS`** | The `addopts` **ini** is applied; the environment variable is not. | Options exported into the environment are ignored. Set them in the ini or on the command line. |
| **Markdown code blocks** | rustest collects python fences out of a `.md` file **named as an argument**; a *directory* walk collects none. | `rustest docs/guide/` runs nothing — name the files (`rustest docs/guide/*.md`). pytest has no equivalent without a plugin, which is why the walk does not. |
| **Reporting-only flags** | Accepted and ignored, with a note on stderr naming each one. | See the table below. |
| **`capsys` is stream-level** | `sys.stdout`/`sys.stderr` are redirected, not the file descriptors — exactly as pytest's `capsys` is. | Use `capfd` for a subprocess or a C extension; it redirects the descriptors and does catch them (probed both ways). |
| **`xfail_strict` ini, `--runxfail`** | Not implemented. The `strict=` *keyword* works. | Set `strict=` on the mark. |

### Reporting flags rustest accepts and ignores

These change how pytest *reports*, not what it runs, and they live in a project's `addopts`
forever. rustest drops each one with a single line on stderr rather than refusing the run —
erroring on a cosmetic flag would make an ordinary `addopts = "-ra --tb=short"` unrunnable.
Anything **not** on this list is still a usage error (exit 4), because a flag that changes
what runs must never be ignored quietly.

| Flag | Note |
| --- | --- |
| `-ra`, `-rfE`, any `-r<chars>` | rustest's summary is always the full one. |
| `--tb=...` | Tracebacks are rendered rustest's way. |
| `--durations=...`, `--durations-min=...` | No timing table yet. |
| `--strict-markers`, `--strict-config`, `--strict` | Unknown marks are never an error here. |
| `-p <plugin>` / `-p no:<plugin>` | rustest has no plugin manager to load or disable. |
| `--import-mode=...` | Import mode is not configurable. |
| `--showlocals` / `-l`, `--full-trace` | Traceback detail is not configurable. |

`--color` **is** implemented, and takes pytest's `yes`/`no` as well as rustest's
`always`/`never`. `--maxfail=N` is implemented too; with more than one worker it stops
dispatching at N rather than mid-flight, so a parallel run can report a few more than N —
the same granularity `pytest-xdist` has.

Two entries that used to live in that table are gone as of 0.18, and both were among the
sharpest:

- **`pythonpath` ini** is now read, `type="paths"` and all, and applied to the worker's
  `sys.path` before anything is imported. A `src/` layout works with no editable install.
- **`indirect=`** is now *pytest's* `indirect=`: the value is routed through a fixture of
  the same name as `request.param`. It used to be a rustest-only feature that read the value
  as a fixture *name*. See the
  [parametrization guide](intro-parametrization.md#indirect-parametrization) for the
  rewrite, including the one-liner that reproduces the old behaviour.

## Migration

### Step 1 — just run it

```bash
rustest tests/
```

A failing run tells you exactly what is missing: unsupported fixtures are named, and
failures are reported in pytest's own `FAILURES` / `short test summary info` sections.

### Step 2 — migrate imports (optional)

Nothing requires this. It buys better IDE completion and type checking, since `rustest`'s
exports are typed and `import pytest` inside a rustest run is a shim:

```python
# before
import pytest

# after
from rustest import approx, fixture, mark, parametrize, raises
```

### Step 3 — keep both runners working (optional)

If your suite has to run under real pytest as well — during a migration, or in a repo where
not everyone has switched — nothing on this page stops it. rustest's shim only exists inside
a rustest run; under pytest, `import pytest` is pytest.

## The legacy engine

**It is gone.** `--v1` used to run the pre-flip engine, frozen, as somewhere for a suite the
current engine could not yet run to go. Phase 4 deleted both halves of it — the Rust
discovery/execution core and the Python runtime around it, roughly 15 000 lines and six Rust
dependencies — rather than keep a second answer alive behind a flag that no gate measured.

Passing `--v1` exits **4** with a message naming the change. `rustest.run()`, which used to
be that engine's Python API, now drives the default engine and returns pytest's exit code
(see [the API overview](../reference/index.html)). If your suite ran under `--v1` and does not run
under the default, that is a bug worth filing: the conformance corpus and the seventeen-suite
real-world sweep exist to make it one.

## Performance

The default engine is **not yet faster than pytest** on every shape, and this page will not
pretend otherwise. It spawns worker processes and has no static collection tier, both of
which are the explicit subject of the next phase of work. The numbers that matter are
recorded, with their methodology and their caveats, in
[`conformance/README.md`](https://github.com/rustest/rustest/tree/main/conformance) —
including the measurement bias that makes several of the older published ratios unsafe to
quote.

Correctness came first on purpose: a fast runner that reports a failing suite as green is
not a faster runner.

## Troubleshooting

### `fixture 'X' is not supported by the rustest v2 worker yet`

Exactly what it says — `X` is a pytest built-in that has not been ported. Check the
[not-yet list](#built-in-fixtures). `capsysbinary`/`capfdbinary` → read `capsys`/`capfd` and
encode; `recwarn` → use `pytest.warns()`.

### `ValueError: the ini value 'markers' is not available to a rustest v2 worker`

`request.config.getini` answers only for the values the run carries to the worker; see
[the `request` object](#the-request-object). The wording is deliberately different from
pytest's `unknown configuration value`, which you get for a name that is not an ini at all —
this one is a real pytest option that this engine does not send, not a typo.

### `ModuleNotFoundError: No module named 'mypackage'` in a `src/` layout

The default engine does not read pytest's `pythonpath` ini, and neither does it silently
insert `src/` the way the legacy engine did. **Real pytest reports the same error on the
same tree** — the legacy behaviour was the outlier. Install your package (`pip install -e .`)
or set `PYTHONPATH=src`.

### Tests hang with `@mark.asyncio`

The decorated function must actually be `async def`:

```python
from rustest import mark


@mark.asyncio
async def test_async():
    result = await do_something()
    assert result
```

### `error: unrecognized arguments: --pytest-compat` — or exit 4

The flag is gone; drop it. `rustest tests/` already does what it used to ask for.

## See also

- [Plugin Compatibility Guide](pytest-plugins.md) — alternatives to popular pytest plugins
- [Migration Guide](migration-guide.md)
- [Comparison with pytest](comparison.md)
