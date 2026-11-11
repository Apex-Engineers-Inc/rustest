# pytest compatibility

**pytest compatibility is not a mode. It is what rustest is.**

`rustest tests/` runs your existing pytest suite. `import pytest` inside a test module
resolves to rustest's own implementation, always. There is no flag to turn it on, because
there is no configuration in which it is off. The boundary is pytest's plugin and hook
system, which rustest does not support; see [plugins](pytest-plugins.md).

```bash
# no installation needed
uvx "rustest==1.0.0rc1" tests/

# or install first
pip install "rustest==1.0.0rc1"
rustest tests/
```

::: {.callout-warning title="`--pytest-compat` was removed"}
It used to opt into a *compatibility mode*. The rewrite made that mode the default
and the only behaviour, so the flag could only have been a no-op or a lie.
Passing it now exits **4** with a pointer to `CHANGELOG.md`, and so does `--v1`. See
[The legacy engine](#the-legacy-engine) below.
:::

There is one engine, so everything on this page describes it.

## What this page is for

Compatibility is a claim, and a claim needs evidence. rustest's is a
[conformance corpus](https://github.com/Apex-Engineers-Inc/rustest/tree/main/conformance):
every case in it is run through **real pytest** and through rustest, and the collected node
IDs, the per-outcome tallies and the process exit code are diffed. Every known divergence
is written down in a ledger with its mechanism. This page is the human-readable summary of
the same territory: what works, what does not yet, and what the gap costs you.

## Supported

### Decorators

- `@pytest.fixture`, with `function`, `class`, `module`, `package` and `session` scopes
- `@pytest.fixture(params=[...])`, fixture parametrization, with `request.param`
- `@pytest.mark.parametrize()`, including stacked and `pytest.param(..., id=...)`
- `@pytest.mark.skip()` / `@pytest.mark.skipif()`, called **and bare**
- `@pytest.mark.xfail()`, including `strict=`, `raises=` and `run=False`
- `@pytest.mark.usefixtures()`
- `@pytest.mark.asyncio`, for async tests, with no plugin needed
- Custom marks, including module-level `pytestmark` and class-level marks
- `unittest.TestCase` classes, with `setUp`/`tearDown`/`setUpClass` and `unittest`'s own
  skip decorators

### Functions

`pytest.raises()`, `pytest.skip()`, `pytest.xfail()`, `pytest.fail()`, `pytest.approx()`,
`pytest.warns()`, `pytest.deprecated_call()`, `pytest.param()`, `pytest.importorskip()`.

### Built-in fixtures

The engine provides **`tmp_path`, `tmp_path_factory`, `tmpdir`, `tmpdir_factory`,
`monkeypatch`, `capsys`, `capfd`, `caplog`, `cache`, `mocker`, `pytestconfig`, `recwarn`**
and `request`.

`capfd` captures at the **file-descriptor** level, as pytest's does, so a `subprocess`, a C
extension or a bare `os.write(1, ...)` is captured and not just a `print()`. As under
pytest, `capsys` and `capfd` cannot both be requested by one test (`cannot use capfd and
capsys at the same time`).

`caplog` installs its handler on the root logger and, like pytest's, **changes no level**:
the root logger keeps its default `WARNING`, so `logging.info(...)` is not captured until
you call `caplog.set_level(logging.INFO)`. A logger with `propagate = False` is not captured
either, and it is not captured under pytest.

`cache` is pytest's `config.cache` API over `.rustest_cache`, sharing the store `--lf`
writes: `cache.get("cache/lastfailed", {})` answers with the last run's failures.

The remaining pytest built-ins are **not implemented yet**, and requesting one is a loud
error that names it rather than pytest's generic `fixture 'x' not found`. "Not found" would
send you hunting for a missing `@fixture` that was never yours to write:

```text
FixtureLookupError: fixture 'pytester' is not supported by the rustest worker yet
(supported builtins: tmp_path_factory, tmp_path, tmpdir_factory, tmpdir, monkeypatch,
capsys, capfd, caplog, cache, mocker, pytestconfig, recwarn)
```

The full not-yet list: `capfdbinary`, `capsysbinary`, `capteesys`, `doctest_namespace`,
`pytester`, `record_property`, `record_testsuite_property`, `record_xml_attribute`,
`testdir`. Each needs a distinct piece of machinery rather than a variation on one that
already exists. The `*binary` capture pair and `capteesys` need a bytes-flavoured capture
class, and `capteesys` a tee besides; the `record_*` trio write JUnit XML attributes and
there is no XML report; `doctest_namespace` belongs to a doctest collector this engine does
not have; `pytester` and `testdir` are pytest's own in-process harness.

### The `request` object

`request.param`, `request.scope`, `request.fixturename`, `request.node`,
`request.addfinalizer()`, `request.getfixturevalue()`, `request.applymarker()`,
`request.instance`, `request.cls`, `request.function`, `request.module`, `request.path`,
`request.keywords`, `request.config`.

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

`request.node` is a **façade** over the execution plan rather than a collection-tree node,
because the engine replaced pytest's tree with a flat manifest. It carries the attributes real conftests
read: `name`, `nodeid`, `originalname`, `path`, `fspath`, `cls`, `module`, `function`,
`instance`, `keywords`, `own_markers`, `config`, and the methods `iter_markers()`,
`get_closest_marker()` and `add_marker()`.

**What is missing is missing on purpose.** There is no `node.session`, no `node.parent` and
no `node.listchain()`, and `request.session` does not exist either. Each would need the
tree. Accessing one raises `AttributeError`, which is the deliberate choice over the
alternative: a stub that answers plausibly turns a missing feature into a silent wrong
answer inside a fixture that decides which database to connect to. An `AttributeError`
naming the attribute is a worse morning and a better outcome.

`request.config` exists but is a **subset**, and it is loud past its edge. It answers
`rootpath`, `rootdir`, `inipath` (always `None`, because the worker is not told which
config file was found), `invocation_params.dir`, `cache`, `getini` and `getoption`.
`pytestconfig` and `node.config` return the same object.

`getini` answers for the six values the run carries to the worker: `python_files`,
`python_classes`, `python_functions`, `asyncio_mode`, `asyncio_default_fixture_loop_scope`
and `asyncio_default_test_loop_scope`. Any other name raises, and the two refusals are
worded apart. A real pytest ini this engine does not carry says so; an unrecognised name
gets pytest's `unknown configuration value`.

`getoption(name, default)` returns the default. Without one it raises `no option named
'x'`, because no command-line option travels to the worker and a fabricated answer would
let a suite report on a mode it never ran in. If you have
`request.config.getoption("--db-url")` in a conftest, read the environment instead for now:

```python
import os

import pytest


def connect(url):
    return {"url": url}


@pytest.fixture
def database():
    return connect(os.environ.get("DB_URL", "sqlite:///:memory:"))
```

## Not supported

**pytest plugins.** By design. See the
[Plugin Compatibility Guide](pytest-plugins.md) for alternatives to the popular ones.

**`_pytest` internals, mostly.** The import surface exists so that a module which *imports*
them still loads: `_pytest.monkeypatch`, `_pytest.config`, `_pytest.outcomes`,
`_pytest.nodes`, `_pytest.mark`, `_pytest.mark.structures`, `_pytest.assertion`,
`_pytest.assertion.rewrite` and `_pytest.main`. Some of those are genuine **aliases** to
rustest's own implementations, which is what makes `from _pytest.outcomes import Skipped`
catch the exception `pytest.skip()` actually raises. Others satisfy the import and do
nothing. Anything not on that list, `_pytest.fixtures` among them, does not resolve at all.

**The hook system, in rustest itself.** A conftest's hooks run **natively** when they are
ones rustest's hook runtime models: `pytest_addoption`, `pytest_configure`,
`pytest_sessionstart` and `pytest_collection_modifyitems` (including `trylast`/`tryfirst`
ordering around `-k` and `-m`, `item.add_marker`, reordering, and
`config.hook.pytest_deselected`). `config.getoption`, `config.getini`, `config.option` and
attributes a `pytest_configure` sets on `config` answer from what those hooks declared.
Everything else (`pytest_generate_tests`, `pytest_runtest_*`, `pytest_sessionfinish`, ...), and
any use of `config`, `item` or `session` that the runtime does not provide, is handed to
pytest whole, see
[When rustest hands a session to pytest](#when-rustest-hands-a-session-to-pytest). The hand-off
also happens when a hook swallows the runtime's refusal in an `except Exception`: the refusal
is recorded first. With `--no-pytest-fallback` the supported hooks still run natively, the
others are not run, and one `NOTE:` line on stderr names each one.

rustest runs `pytest_configure` and `pytest_sessionstart` once per worker process, as
pytest-xdist does: the first worker runs them before any other starts, and a session that has
to go to pytest goes then. A hook whose side effect only works once (creating a directory, say)
therefore fails in the second worker, and the session ends with pytest's configure error
(exit 3) rather than being handed to pytest to run the hook yet again; `-n 1` runs it once.
When a `pytest_collection_modifyitems` hook runs with more than one worker, every worker first
imports every test module the session collected, as each pytest-xdist worker does.

**Warning diagnostics.** `recwarn` and `pytest.warns()` record warnings in-process and both
work. What is missing is the reporting channel that surfaces pytest's own diagnostics: the
`PytestCollectionWarning` for a class with `__init__`, the "usefixtures() without arguments
has no effect" note. The *behaviour* in each case matches pytest; only the message is
missing.

## When rustest hands a session to pytest

Some things only pytest can do correctly. A hook such as `pytest_runtest_setup`
in one directory's `conftest.py` runs for the items of *every* directory, so running part of a
session under rustest and part under pytest would be wrong, not just awkward. Instead the
session is **whole-run, one runner or the other**: when rustest finds something it cannot
honour, it runs `python -m pytest` with your own arguments, streams pytest's output
unmodified, and returns pytest's exit code. This is on by default. `--no-pytest-fallback`
turns it off.

### What hands a session over

| What rustest finds | Why |
|---|---|
| A `conftest.py`, or a module a `pytest_plugins` assignment names, defines a `pytest_*` hook that is neither one rustest runs natively (`pytest_addoption`, `pytest_configure`, `pytest_sessionstart`, `pytest_collection_modifyitems`) nor one of the four terminal-text hooks below: `pytest_generate_tests`, `pytest_sessionfinish`, `pytest_runtest_*` and the rest | They change what runs, how it is reported, or the exit status |
| A native hook uses something the hook runtime does not provide (for example `config.pluginmanager.register`, or reading a parametrized item's markers) | The runtime cannot answer it the way pytest would, so the session goes to pytest rather than guess |
| A conftest, or a module it names, raises when rustest's worker imports it (`rustest could not import tests/conftest.py: ...`) | Conftests that import `_pytest` internals rustest does not provide are exactly the hook-writing ones |
| `-p NAME` (not `-p no:NAME`) | Loads a pytest plugin |
| `-o KEY=VALUE` arriving through `addopts` or `PYTEST_ADDOPTS` (rustest reads only the command line's `-o`) | pytest applies it |
| An argument rustest does not have: `--runslow`, `--doctest-modules`, `--timeout=5`, `-n auto`. Abbreviations count: `--report=html` is not `--report-json` | pytest may know it. A typo gets pytest's own usage error, exit 4 |
| `-o KEY=VALUE` for any key other than `addopts` | pytest accepts any ini key |
| `PYTEST_PLUGINS` is set, or `PYTEST_ADDOPTS` contains something rustest does not understand | pytest applies them; rustest does not |

Hooks that do **not** hand the session over, because they only change terminal text:
`pytest_report_header`, `pytest_report_collectionfinish`, `pytest_terminal_summary` and
`pytest_assertrepr_compare`. They are still named in a `NOTE:` line on stderr and still not
run.

A rustest flag with a bad value (`-n abc`, `--color=blue`) is rustest's own usage error, and
so are the removed `--pytest-compat` and `--v1`.

### What you see

One line on stderr before pytest starts. It names the reasons, says the run will be at
pytest's speed, and, only when it is true for those reasons, says that
`--no-pytest-fallback` would run the session with rustest anyway and what that would skip.
A reason that came from `addopts` or `PYTEST_ADDOPTS` names where it came from. After that the
output is pytest's own, live, and the exit code is pytest's.

{{< termshow file="fallback" autoplay="false" loop="false" >}}

That is a recorded run of a project whose `conftest.py` skips every `slow` test in
`pytest_runtest_setup`: only pytest can honour that, so the session goes to pytest
(here with `-q`, so pytest prints its bare letters and summary).

pytest's cache is pointed at `.rustest_cache` (`-o cache_dir=.rustest_cache`), the directory
rustest already writes, so `--lf` and `--ff` agree between the two runners. A project that
relocates its pytest cache will find `.rustest_cache` created in the rootdir instead.

### Options under a handed-over session

- `-k`, `-m`, `-x`, `--maxfail`, `--lf`, `--ff`, `-v`, `-q`, node ids and paths go to pytest
  as written, with pytest's own semantics. `--pattern`, `--marks`, `--no-capture` and
  `--color always|never` are respelled `-k`, `-m`, `-s` and `--color yes|no`.
- `-n` and `--workers` become `-n`, and work only if pytest-xdist is installed; otherwise they
  are dropped, the notice says so, and the run is serial.
- `--cov` goes through, so pytest-cov is needed.
- **A `.md` argument is refused** (exit 4): pytest cannot run rustest's documentation code
  blocks, and pytest-codeblocks numbers and skips them differently.
- If pytest is not installed the run exits 4 and says so. It never carries on silently.

### `--report-json`, `--llm` and `--collect-only`

All three work. A small plugin bundled with rustest is loaded into the pytest child and
records what pytest did; rustest then writes the report or prints the stream itself.

- `--report-json` writes a schema-version-2 report with one extra top-level key,
  `"pytest_fallback"`, listing the reasons, so a consumer can tell which runner produced it.
  The human output is pytest's, live.
- `--llm` / `--llm-full`: stdout is the JSONL stream and nothing else; pytest's own terminal
  output goes to stderr (run with `--tb=long`, whatever `--tb` you passed, so a failure's `msg`
  is a full traceback). The `meta` line carries the same `pytest_fallback` array as the report,
  and `tool` stays `rustest`.
- `--collect-only` prints the node ids pytest collected, in rustest's format (ids on stdout,
  the summary on stderr). `--llm` with `--collect-only` is refused, as it always is.

The report is built from pytest's side, and some fields cannot be filled as the engine fills
them:

- `teardown_errors`, `worker_stderr` and `session_exit` are absent (there are no workers, and
  pytest attributes a teardown failure to its own test).
- A failure's `message` is pytest's rendering of the traceback, so its wording differs from the
  engine's, and under `--report-json` it follows your own `--tb`. The `line` of a failure is
  pytest's own, recorded by the plugin.
- Each test has one reduced status. A body that passes or fails with a teardown that raises is
  one `error` in the report; pytest's own summary counts it as `1 passed, 1 error` or
  `1 failed, 1 error`. A module that skips itself at import counts as one skip, as it does in
  the engine. An outcome other than passed, failed or skipped (pytest-rerunfailures' `rerun`) is
  ignored.
- Under pytest-xdist only the controller writes the report, and the workers' deselection counts
  are not aggregated, so `deselected` is 0.
- `stopped_early` is true when pytest's should-fail or should-stop state was set, or it ended
  with exit status 2 (Ctrl-C, `pytest.exit`).

### What it costs

The session runs at **pytest's speed**, and rustest's collection is thrown away first (about
a second of pool start and imports). A test module's import-time code runs twice, once in
rustest's workers and once in pytest. The speed figures on this site were measured with
`--no-pytest-fallback`.

### What is still not detected

These can still make rustest and pytest disagree without a notice, because rustest does not
yet look for them:

- **Plugins that load themselves** through a `pytest11` entry point (pytest-randomly,
  pytest-rerunfailures, pytest-timeout, pytest-django and so on).
- **Ini keys rustest does not honour**, such as plugin keys (`timeout`, `reruns`).
- **Plugin marks**, such as `@pytest.mark.timeout`.
- **A `conftest.py` in a directory with no test files**: rustest only follows the conftests above
  directories that hold tests, so its session-wide hooks are not seen. (pytest registers it
  while walking the tree.)

## Known gaps, with their cost

These are the divergences the conformance corpus has found and pinned. Each is a real case
in the corpus, so none of them can regress unnoticed or be quietly forgotten.

| gap | what happens | cost |
| --- | --- | --- |
| **Selecting a class that contains no tests** | `rustest test_m.py::TestEmpty` exits 4 (`ERROR: not found`), where pytest exits 5 (no tests collected). | Loud, and never runs the wrong tests; only the exit code differs. |
| **`pytest.exit(returncode=N)`** | `pytest.exit()` itself stops the session and exits 2, pytest's answer. The `returncode=` payload is not honoured (the worker exit code is the whole channel), and an `exit()` at *import* time surfaces as exit 3 rather than 2. | A custom exit code is lost. Corpus case `marks/pytest-exit`. |
| **Session fixtures across workers** | A `session`-scoped fixture is built once per **worker** process, and a worker is handed a subset of the files. At `-n 1` that is pytest's behaviour exactly; across a pool it is pytest-xdist's. | Cross-file shared state works within a worker, not across the pool. Corpus case `fixtures/session-scope`. |
| **No item reordering** | pytest groups tests that share a higher-scoped parametrized fixture; rustest keeps source order. | A module-scoped `params=["a", "b"]` fixture costs 2 setups under pytest and 4 here. Corpus case `fixtures/module-param-reorder`. |
| **`package` scope** | Cached for the worker's lifetime; not torn down at the package boundary. | Late teardown. |
| **Async concurrency** | Async tests in the same loop scope run sequentially, as they do under pytest-asyncio, which drives each coroutine through a non-re-entrant `asyncio.Runner.run`. | No wall-clock overlap, and none under pytest either. Listed so it is not mistaken for a divergence; `loop_scope` itself is implemented. |
| **`PYTEST_ADDOPTS`** | The `addopts` **ini** is applied; the environment variable is not. | Options exported into the environment are ignored. Set them in the ini or on the command line. |
| **Markdown code blocks** | Off by default; `--codeblocks`, or `[tool.rustest] codeblocks = true`, turns it on. With it off, a `.md` argument is a usage error and rustest's answer matches pytest's **exactly**, so this is no longer a divergence. With it on, rustest collects python fences out of a `.md` file **named as an argument**; a *directory* walk still collects none. | `rustest user_guide/` runs nothing either way, so name the files once enabled: `rustest user_guide/*.md --codeblocks`. pytest has no equivalent without a plugin, which is why the walk does not, even with the tier on. |
| **Reporting-only flags** | Accepted and ignored, with a note on stderr naming each one. | See the table below. |
| **`capsys` is stream-level** | `sys.stdout`/`sys.stderr` are redirected, not the file descriptors, exactly as pytest's `capsys` is. | Use `capfd` for a subprocess or a C extension; it redirects the descriptors and does catch them (probed both ways). |
| **`--runxfail`** | Not implemented. | Remove the `xfail` mark to see the test's own result. |
| **Warnings** | The `filterwarnings` ini and `@pytest.mark.filterwarnings` are applied as pytest applies them, around collection and around each test, so `filterwarnings = error` fails the same tests. Unraisable exceptions (a `__del__` that raises, an unclosed file) and exceptions in threads become `PytestUnraisableExceptionWarning` / `PytestUnhandledThreadExceptionWarning`, and an unregistered mark is `PytestUnknownMarkWarning`, as in pytest. Warnings that are not errors are listed in a warnings summary and counted in the summary line. `-W` on the command line, and `-p no:warnings`, hand the session to pytest. | Four counts differ: `--collect-only` lists no warnings; a warning raised while a skipped directory's `conftest.py` is imported under `--lf` is applied but not counted; with `-n` above 1 a warning raised while a conftest or a library is imported is counted once per worker, and a `once` filter means once per worker; a mark used inside a `conftest.py` is never checked against the registry (pytest checks the ones imported after `pytest_configure`). |
| **Unknown `pytest.*` attributes** | `pytest.Item`, `pytest.Collector` and any other public name this shim does not define return an inert generated stub class rather than raising `AttributeError`. The catch-all exists so a module that *imports* a pytest internal still loads — the `pytest_asyncio` shim depends on it. | An `isinstance()` or `issubclass()` check against one of these is silently false rather than loudly wrong. This is the one place the shim answers plausibly instead of refusing; `request.node`'s missing attributes go the other way, and deliberately. Not yet a conformance corpus case. |

### Reporting flags rustest accepts and ignores

These change how pytest *reports*, not what it runs, and they live in a project's `addopts`
forever. rustest drops each one with a single line on stderr rather than refusing the run,
because erroring on a cosmetic flag would make an ordinary `addopts = "-ra --tb=short"`
unrunnable. Anything **not** on this list is still a usage error (exit 4), because a flag
that changes what runs must never be ignored quietly.

| Flag | Note |
| --- | --- |
| `-ra`, `-rfE`, any `-r<chars>` | rustest's summary is always the full one. |
| `--tb=...` | Tracebacks are rendered rustest's way. |
| `--durations=...`, `--durations-min=...` | No timing table. Per-test durations are in `--report-json`. |
| `--strict-config` | Unknown ini keys are never an error here. |
| `-p <plugin>` / `-p no:<plugin>` | rustest has no plugin manager to load or disable. |
| `--import-mode=...` | Import mode is not configurable. |
| `--showlocals` / `-l`, `--full-trace` | Traceback detail is not configurable. |

`--color` takes pytest's `yes`/`no` as well as rustest's `always`/`never`, with pytest's
precedence. In a terminal rustest draws its own live display rather than pytest's lines;
piped, it writes pytest's progress section and colours it as pytest does, with the summary
line coloured as a whole in the run's main colour (pytest colours each count separately) and
failure sections plain. `--ascii` draws the terminal display with ASCII only. See
[CLI usage](cli.md#output-control).

The same stderr channel carries one more `NOTE:` line, for a conftest's `pytest_*` hooks, which
are not run; see [The hook system](#not-supported) above.

`--maxfail=N` **is** implemented. With more than one worker it stops dispatching at N
rather than mid-flight, so a parallel run can report a few more than N, which is the same
granularity `pytest-xdist` has.

Two behaviours that used to be gaps are worth naming, because a suite written against the
older versions may still be working around them:

- **`pythonpath` ini** is read, `type="paths"` and all, and applied to the worker's
  `sys.path` before anything is imported. A `src/` layout works with no editable install.
- **`indirect=`** is *pytest's* `indirect=`: the value is routed through a fixture of the
  same name as `request.param`. It was once a rustest-only feature that read the value as a
  fixture *name*. See the
  [parametrization guide](parametrization.md#indirect-parametrization) for the
  rewrite, including the one-liner that reproduces the old behaviour.

### Where rustest is more permissive than pytest

The gaps above all run one way: pytest does something rustest does not. This one runs the
other way, which makes it a migration hazard in the opposite direction. It is **not yet a
conformance corpus case**, unlike everything in the table above, so it is recorded here
rather than pinned.

**`pytest_plugins` below the rootdir.** rustest honours a `pytest_plugins` declaration in
any `conftest.py`. pytest only accepts it in the **rootdir** `conftest.py`, and refuses it
anywhere else:

```text
Defining 'pytest_plugins' in a non-top-level conftest is no longer supported:
It affects the entire test suite instead of just below the conftest as expected.
```

pytest's stated reason is the run-wide registration itself, which is the same behaviour
rustest implements deliberately: a declared module's fixtures are visible to the whole run,
not only below the conftest that named it.

The refusal is invocation-dependent, which is what makes it easy to ship. `pytest tests/`
loads that conftest as an initial one and passes; `pytest` or `pytest .` from the project
root is a collection error that stops the run. So a suite can pass locally and fail in CI
on an unchanged tree.

If a suite must run under both runners, keep the declaration in the rootdir `conftest.py`.
Moving it up costs nothing, because the fixtures were already registered run-wide.

## Migration

### Step 1: just run it

```bash
rustest tests/
```

A failing run tells you exactly what is missing: unsupported fixtures are named, and
failures are reported in pytest's own `FAILURES` / `short test summary info` sections.

### Step 2: migrate imports (optional)

Nothing requires this. It buys better IDE completion and type checking, since `rustest`'s
exports are typed and `import pytest` inside a rustest run is a shim:

```python
# before
import pytest

# after
from rustest import approx, fixture, mark, parametrize, raises
```

### Step 3: keep both runners working (optional)

If your suite has to run under real pytest as well, during a migration or in a repo where
not everyone has switched, nothing on this page stops it. rustest's shim only exists inside
a rustest run; under pytest, `import pytest` is pytest.

## The legacy engine

**It is gone.** `--v1` used to run the previous engine, frozen, as somewhere for a suite the
current engine could not yet run to go. The rewrite deleted both halves of it, the Rust
discovery/execution core and the Python runtime around it, roughly 15 000 lines and six Rust
dependencies, rather than keep a second answer alive behind a flag that no gate measured.

Passing `--v1` exits **4** with a message naming the change. `rustest.run()`, which used to
be that engine's Python API, now drives the default engine and returns pytest's exit code
(see [the API overview](../reference/index.html)). If your suite ran under `--v1` and does not run
under the default, that is a bug worth filing: the conformance corpus and the seventeen-suite
real-world sweep exist to make it one.

## Performance

Correctness came first on purpose: a fast runner that reports a failing suite as green is
not a faster runner. The compatibility work above is what the speed numbers are allowed to
rest on.

Across seventeen real open-source pytest suites rustest runs **1.1x to 5.7x** faster, and
the ceiling in any given case is that suite's framework share, meaning the fraction of wall
clock that is not your own test bodies. Collection is where the gap is widest. The
per-suite table, the methodology and every caveat are in [Performance](performance.md).

## Troubleshooting

### `fixture 'X' is not supported by the rustest worker yet`

Exactly what it says: `X` is a pytest built-in that has not been ported. Check the
[not-yet list](#built-in-fixtures). For `capsysbinary`/`capfdbinary`, read `capsys`/`capfd`
and encode the result yourself.

### `ValueError: the ini value 'markers' is not available to a rustest worker`

`request.config.getini` answers only for the values the run carries to the worker; see
[the `request` object](#the-request-object). The wording is deliberately different from
pytest's `unknown configuration value`, which you get for a name that is not an ini at all.
This one is a real pytest option that this engine does not send, not a typo.

### `ModuleNotFoundError: No module named 'mypackage'` in a `src/` layout

Set the `pythonpath` ini, which rustest reads and applies to the worker's `sys.path` before
any import:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
```

Installing the package (`pip install -e .`) or exporting `PYTHONPATH=src` works too. What
rustest will not do is silently insert `src/` for you, and neither does real pytest.

### Tests hang with `@mark.asyncio`

The decorated function must actually be `async def`:

```python
import asyncio

from rustest import mark


async def do_something():
    await asyncio.sleep(0)
    return True


@mark.asyncio
async def test_async():
    result = await do_something()
    assert result
```

### `error: unrecognized arguments: --pytest-compat`, or exit 4

The flag is gone; drop it. `rustest tests/` already does what it used to ask for.

## See also

- [Plugin Compatibility Guide](pytest-plugins.md), alternatives to popular pytest plugins
- [Upgrade guide](migration-guide.md), breaking changes if you are coming from an older rustest
- [Comparison with pytest](comparison.md)
