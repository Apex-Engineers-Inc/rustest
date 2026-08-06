# Comparison with pytest

Rustest implements the parts of pytest that most suites actually use, and runs them through
a Rust orchestrator. This page is the ledger: what both runners do, what only pytest does,
and, secondarily, how much faster rustest is in practice, including the suites where the
answer is "barely".

The feature table is the part that matters. Compatibility is what this release was built
for; the performance section at the end reports where speed happens to stand, and is not
the reason to switch.

## Feature comparison table

| Feature | pytest | rustest | Notes |
|---------|--------|---------|-------|
| **Core Test Discovery** |
| `test_*.py` / `*_test.py` files | ✅ | ✅ | The file walk and the `python_files` ini are handled in Rust |
| Test function detection (`test_*`) | ✅ | ✅ | |
| Test class detection (`Test*`) | ✅ | ✅ | Including fixtures defined as class methods |
| Pattern-based filtering | ✅ | ✅ | `-k`, matching pytest's keyword expression grammar against path segments, class names, the function name with its `[param]` suffix, and mark names |
| `unittest.TestCase` | ✅ | ✅ | `setUp` / `tearDown` / `setUpClass` and unittest's own skip decorators |
| Markdown code block testing | ✅ (`pytest-codeblocks`) | ✅ (off by default; `--codeblocks`) | Python fences in `.md` files, when the file is named as an argument rather than reached by a directory walk. Each block executes at module level, so a `def test_*` inside one collects and runs as its own node |
| **Fixtures** |
| `@fixture` decorator | ✅ | ✅ | |
| Fixture dependency injection | ✅ | ✅ | Resolved in the Python worker, as pytest does |
| Fixture scopes (function/class/module/package/session) | ✅ | ✅ | `package` is cached for the worker's lifetime rather than torn down at the package boundary |
| Yield fixtures (setup/teardown) | ✅ | ✅ | |
| Fixture methods within test classes | ✅ | ✅ | |
| Fixture parametrization | ✅ | ✅ | `@fixture(params=[...])` with `request.param` |
| `conftest.py` | ✅ | ✅ | Fixtures are used; hooks defined in the same file are ignored |
| **Built-in Fixtures** |
| `tmp_path` / `tmp_path_factory` | ✅ | ✅ | Temporary directories as `pathlib.Path` |
| `tmpdir` / `tmpdir_factory` | ✅ | ✅ | Legacy `py.path` spelling |
| `monkeypatch` | ✅ | ✅ | Attributes, env vars, dict items, `sys.path` |
| `capsys` / `capfd` | ✅ | ✅ | Stream-level and descriptor-level capture respectively, matching pytest |
| `caplog` | ✅ | ✅ | Root-logger handler; sets no level, as pytest's does not |
| `cache` | ✅ | ✅ | Persistent store under `.rustest_cache`, shared with `--lf` |
| `mocker` | ✅ (`pytest-mock`) | ✅ | Built in |
| `pytestconfig` | ✅ | ✅ | The same object `request.config` returns |
| `recwarn` | ✅ | ✅ | |
| `request` | ✅ | ✅ | See the row group below |
| `capsysbinary`, `capfdbinary`, `capteesys`, `doctest_namespace`, `pytester`, `testdir`, `record_*` | ✅ | ❌ | Requesting one is an error that names it |
| **The `request` object** |
| `request.param` | ✅ | ✅ | Parameter value for parametrized fixtures |
| `request.node` | ✅ | ✅ | A façade over the execution plan: `name`, `nodeid`, `path`, `cls`, `module`, `function`, `instance`, `keywords`, `own_markers`, `iter_markers`, `get_closest_marker`, `add_marker`, `config`. No `session`, `parent` or `listchain` |
| `request.config` | ✅ | ⚠️ | `rootpath`, `inipath`, `invocation_params.dir`, `cache`, `getini` for six carried values, and `getoption` that returns your default or raises |
| `request.session` | ✅ | ❌ | There is no session object; the collection tree it belongs to does not exist |
| **Test Utilities** |
| `pytest.raises()` | ✅ | ✅ | Exception assertion context manager |
| `pytest.skip()` | ✅ | ✅ | Dynamically skip a test |
| `pytest.xfail()` | ✅ | ✅ | Mark test as expected to fail |
| `pytest.fail()` | ✅ | ✅ | Explicitly fail a test |
| `pytest.approx()` | ✅ | ✅ | Numeric comparison with tolerance |
| `pytest.warns()` | ✅ | ✅ | Warning assertion context manager |
| `pytest.deprecated_call()` | ✅ | ✅ | Check for deprecation warnings |
| `pytest.importorskip()` | ✅ | ✅ | Skip if module unavailable |
| `pytest.exit()` | ✅ | ⚠️ | Stops the session and exits 2; the `returncode=` argument is not honoured |
| **Async Support** |
| `@pytest.mark.asyncio` | ✅ (plugin) | ✅ | Built in, no plugin needed |
| Async fixtures | ✅ (plugin) | ✅ | |
| Event loop scopes | ✅ (plugin) | ✅ | `loop_scope` for function, module and session |
| **Parametrization** |
| `@parametrize` decorator | ✅ | ✅ | Including stacking and `pytest.param(..., id=...)` |
| Multiple parameter sets | ✅ | ✅ | |
| Parametrize with fixtures | ✅ | ✅ | |
| `indirect=` | ✅ | ✅ | Routed through a fixture of the same name, as pytest's is |
| **Marks** |
| `@mark.skip` / `@mark.skipif` | ✅ | ✅ | Called and bare |
| `@mark.xfail` | ✅ | ✅ | Including `strict=`, `raises=` and `run=False`. The `xfail_strict` ini and `--runxfail` are not implemented |
| Custom marks (`@mark.slow`, etc.) | ✅ | ✅ | Including module-level `pytestmark` and class-level marks |
| Mark with arguments | ✅ | ✅ | `@mark.timeout(30)` |
| Selecting tests by mark (`-m`) | ✅ | ✅ | Full boolean expressions: `and` / `or` / `not`, parentheses, mark kwargs |
| **Configuration** |
| `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg` | ✅ | ✅ | rootdir and ini resolution follow pytest's rules |
| `addopts` ini | ✅ | ✅ | |
| `PYTEST_ADDOPTS` env var | ✅ | ❌ | Set the options in the ini or on the command line |
| `pythonpath` ini | ✅ | ✅ | Applied to the worker's `sys.path` before any import |
| **Test Execution** |
| Detailed assertion introspection | ✅ | ✅ | Assertions are rewritten; a failure reports the values (`assert 41 == 42`) |
| Parallel execution | ✅ (`pytest-xdist`) | ✅ | Built in: `-n` / `--workers`, a process pool the Rust orchestrator drives |
| Test isolation | ✅ | ✅ | |
| Stdout/stderr capture | ✅ | ✅ | `--no-capture` / `-s` |
| Item reordering by fixture scope | ✅ | ❌ | rustest keeps source order, so a parametrized higher-scoped fixture is set up more often |
| **Reporting** |
| Pass/fail/skip summary | ✅ | ✅ | |
| Failure tracebacks | ✅ | ✅ | Full Python tracebacks |
| Duration reporting | ✅ | ⚠️ | Per-test timing is in the JSON report; `--durations` is accepted and ignored |
| JSON report | ❌ (plugin) | ✅ | `--report-json`, and `--llm` for JSONL |
| Coverage | ✅ (`pytest-cov`) | ✅ | `--cov` with `term` and `xml` reports, line coverage only |
| JUnit XML output | ✅ | ❌ | Not implemented |
| HTML reports | ✅ (`pytest-html`) | ❌ | Not implemented |
| Colored output | ✅ | ❌ | `--color` is accepted for compatibility and has no effect |
| **Advanced Features** |
| Plugins | ✅ | ❌ | Not supported by design ([see why](pytest-plugins.md)) |
| Hooks | ✅ | ❌ | Not supported by design |
| Custom collectors | ✅ | ❌ | Not supported by design |

**Legend:**
- ✅ Supported
- ⚠️ Partial, see the note
- ❌ Not implemented

## Philosophy

Rustest implements the pytest surface that ordinary suites depend on and leaves out the
extension machinery. That trade is what makes the speed possible: no plugin manager, no
hook dispatch, and no collection tree to walk.

### When to use rustest

- Your suite spends a meaningful share of its wall clock in the framework rather than in
  your own test bodies. That share is the ceiling on any speedup, it is measurable, and
  [Performance](performance.md) shows you how to estimate yours before you migrate.
- You want faster collection in particular. Collection is where the gap is widest.
- You use standard pytest features: fixtures, parametrization, marks, `conftest.py`.
- You want the Python examples in your markdown documentation to genuinely execute as
  tests, `def test_*` functions included, with no `pytest-codeblocks` to add. Off by
  default; see [Markdown Testing](markdown-testing.md).

### When to use pytest

- You need pytest plugins such as pytest-django or pytest-cov. See the
  [plugin migration guide](pytest-plugins.md).
- Your suite defines hooks or custom collectors.
- You need JUnit XML or HTML reports.
- Your test bodies dominate the wall clock. A runner cannot make your own code faster, and
  two of the seventeen suites benchmarked are in exactly that position.

## Migration from pytest

### Easy migration

Most pytest suites run under rustest without edits. `import pytest` inside a rustest run
resolves to rustest's own implementation, so the common case is that nothing changes:

<!--rustest.mark.skip-->
```python
# pytest code
import pytest

@pytest.fixture
def database():
    return setup_database()

@pytest.mark.parametrize("value,expected", [(1, 2), (2, 4)])
def test_double(value, expected):
    assert value * 2 == expected
```

The same file runs under both runners, unchanged.

### What stays the same

- Test discovery patterns
- Fixture syntax and scopes
- Parametrization syntax
- Mark syntax
- Exception testing with `raises()`
- Numeric comparison with `approx()`
- Test class structure
- `pytest.ini` / `pyproject.toml` config, including `addopts` and `pythonpath`

### What changes

#### Import statements

Migrating imports is optional. It buys typed exports and better IDE completion, since
`rustest`'s API is typed and `import pytest` inside a rustest run goes through a shim:

<!--rustest.mark.skip-->
```python
# before
import pytest

# after
from rustest import approx, fixture, mark, parametrize, raises
```

Note that `parametrize` is a top-level export in rustest and is not one in pytest, where it
is only reachable as `pytest.mark.parametrize`.

#### Running tests

```bash
# pytest
pytest tests/

# rustest
rustest tests/
```

Markdown is the one argument shape that differs: rustest can collect code blocks from a
`.md` file **named** as an argument, and a directory walk collects none either way. The tier
itself is **off by default**: `--codeblocks`, or `[tool.rustest] codeblocks = true`, has to
turn it on, or naming a `.md` file is a usage error, matching `pytest`'s own answer for the
same argument.

```bash
rustest README.md user_guide/*.md --codeblocks
```

#### Configuration

Both runners read the same files. rustest resolves rootdir and ini values following
pytest's own rules, from `pytest.ini`, `.pytest.ini`, `pyproject.toml`, `tox.ini` or
`setup.cfg`:

```toml
# Read by both
[tool.pytest.ini_options]
testpaths = ["tests"]
```

The gap is `PYTEST_ADDOPTS`, which pytest reads from the environment and rustest does not.
Move those options into `addopts` or onto the command line.

### Compatibility layer

Nothing is needed to keep both runners working. rustest's shim only exists inside a rustest
run, so under pytest `import pytest` is pytest. A suite in the middle of a migration can be
run either way from the same source:

```bash
pytest tests/
rustest tests/
```

## Feature deep dive

### Fixtures

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import fixture  # or from pytest import fixture

@fixture(scope="session")
def database():
    db = setup()
    yield db
    db.cleanup()
```

One difference at scope boundaries: a `session` fixture is built once per worker process,
and each worker gets a subset of the files. At `-n 1` that is pytest's behaviour exactly;
across a pool it is pytest-xdist's. Cross-file shared state works within a worker, not
across the pool.

### Parametrization

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import parametrize  # or pytest.mark.parametrize

@parametrize("x,y", [(1, 2), (3, 4)], ids=["first", "second"])
def test_values(x, y):
    assert x < y
```

Node ids for parametrized tests are byte-identical to pytest's, which is what lets a
`-k` expression or a CI failure list carry over unchanged.

### Marks

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import mark  # or from pytest import mark

@mark.slow
@mark.integration
def test_expensive():
    pass
```

Both runners filter on these with `-m`, and rustest implements pytest's full expression
grammar: `-m "slow and not integration"`, parentheses, and keyword matching on mark
arguments. See [CLI Usage](cli.md).

Unknown marks are never an error in rustest, and `--strict-markers` is accepted and
ignored.

### Assertion helpers

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import approx, raises  # or from pytest import approx, raises

def test_comparison():
    assert 0.1 + 0.2 == approx(0.3)

    with raises(ValueError, match="invalid"):
        raise ValueError("invalid input")
```

### Test classes

<!--rustest.mark.skip-->
```python
# Works identically in both
from rustest import fixture  # or from pytest import fixture

class TestMath:
    @fixture(scope="class")
    def calculator(self):
        return Calculator()

    def test_add(self, calculator):
        assert calculator.add(2, 3) == 5
```

## Performance comparison

**Reported, not advertised.** Speed is not what this release was built for, and it is not
where the work went; deeper performance work comes later. What follows is where things
actually stand.

Seventeen real open-source pytest suites have been run under both runners, with the outcome
tallies diffed to confirm the two runs did the same work. Rustest is between **1.1x and
5.7x** faster on them.

Aggregated over all seventeen the figure is **1.23x**, because two of those suites are
almost entirely their own code. Across the other fifteen it is **2.74x**.

The component numbers are further apart than the end-to-end ones, which is the shape you
would expect: on 500 files and 5,000 tests, warm collection goes from 8.39s to 227.6ms
(**~37x**) and marginal per-test framework overhead from 933.6µs to 117.9µs (**~8x**).

What decides your number is your suite's framework share, meaning the fraction of wall
clock that is not your own test bodies. That fraction is the ceiling on any speedup. The
per-suite table, the methodology and every caveat are in
[Performance](performance.md), including the note that the marginal-overhead figure is
noisy on a loaded machine and a single reading of it should not be quoted as a result.

## Ecosystem

pytest has hundreds of plugins, is the de facto standard for Python testing, and is
supported directly by most Python IDEs.

Rustest has no plugin system, by design. It is a younger project with a much smaller
surface. Tooling that shells out to a test command and reads node ids has the same node ids
to work with, since rustest's are byte-identical to pytest's. Tooling that loads pytest as
a library and hooks its internals has nothing to hook.

## Future roadmap

Not implemented:

- JUnit XML output
- HTML reports
- Colored terminal output

Not planned, by design:

- Plugin system
- Hooks
- Custom collectors

## Conclusion

Use rustest when the framework is a real share of your test time and you are not relying on
plugins, hooks or custom collectors. Use pytest when you are, or when you need an output
format rustest does not produce.

The two are not mutually exclusive during a migration: the same suite can be run under both
from the same source, and that is how rustest's own compatibility claims are checked.

## See also

- [Pytest Plugins](pytest-plugins.md) - Migration guide for popular pytest plugins
- [pytest Compatibility](pytest-compat.md) - The full compatibility ledger
- [Performance](performance.md) - Detailed performance analysis
- [Getting Started](quickstart.md) - Try rustest
- [Development](development.md) - Contribute to rustest
