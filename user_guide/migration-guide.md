# Upgrade guide

This page covers upgrading **rustest itself**. If you are coming from pytest rather than
from an older rustest, start at [pytest compatibility](pytest-compat.md). You probably do not
need to change any code at all.

For the full record of every change, see the [Changelog](changelog.md).

## Upgrading to the v2 engine

This release replaces rustest's engine. The old one ("v1") is deleted, not frozen, and there
is no flag that runs it. Everything below is a change you may have to act on.

### `--pytest-compat` is gone

It used to opt into a compatibility *mode*. That mode is now the only behaviour: every run
installs the shim, so `import pytest` resolves to rustest's own implementation whether you
ask for it or not. A flag that can only ever be a no-op is worse than no flag, so passing it
now **exits 4** with a message naming the change.

```bash
rustest --pytest-compat tests/   # exit 4
rustest tests/                   # what you want
```

**Action:** delete `--pytest-compat` from any CI command line, pre-commit hook, `addopts` or
script.

### `--v1` is gone

Same exit code, different reason: the engine it selected no longer exists. There is nothing
to fall back to, which means the gap list in [pytest compatibility](pytest-compat.md) is now
the *complete* statement of what rustest does and does not do. No "use `--v1` for this"
escape hatch remains.

### Python 3.12 is the floor

`requires-python` is `>=3.12`. The engine's coverage integration uses `sys.monitoring`
unconditionally, and that module does not exist before 3.12. Python 3.10 and 3.11 are no
longer supported.

### `rustest.run()` changed shape

This is the breaking change most likely to bite a programmatic caller, because it changes
both the arguments and the return type:

| | Before | Now |
|---|---|---|
| Arguments | positional and keyword, including `capture_output`, `pytest_compat`, `ascii`, `no_color`, `verbose` | **keyword-only**, and a different set. See [the Python API page](python-api.md) |
| Returns | a `rustest.reporting.RunReport` object | an **`int`**: pytest's exit code |

`run` is now a plain alias for the v2 entry point rather than a translating wrapper, and that
is deliberate. A compatibility shim would have accepted `pytest_compat=False` and silently
done the opposite, and would have returned an integer where the old type hint promised a
`RunReport`. Instead an old call raises `TypeError` immediately, naming the keyword it does
not recognise.

If you were reading counts off the returned report, pass `report_json=` and read the JSON
file, or use [`--llm`](llm-output.md) if a machine is consuming it.

### `indirect=` parametrization now means what pytest means

`@parametrize(..., indirect=["thing"])` routes the value through the fixture named `thing`
via `request.param`, which is pytest's rule. It used to be rustest-specific: the parameter
*value* was read directly as a fixture name. See [Parametrization](parametrization.md).

### `--llm` output is schema 2

If you have tooling pinned to rustest 0.18's `--llm` JSONL, it will not read this output. The
`meta` line now carries `"schema_version": 2` and the failure objects changed shape: one
whole `msg` instead of six shredded fields, all six status buckets in the summary, and `line`
omitted rather than reported as `0` when there is no frame. A consumer pinned to version 1
should **refuse** rather than half-read, and the published schema marks `schema_version` as
`const: 2` so it can. Run `rustest --llm-schema` for the current contract, and see
[LLM output](llm-output.md).

### Markdown code blocks are off by default, and now execute for real

This is not the same feature it used to be. Two changes, both breaking:

**It is off by default.** Naming a `.md` file used to collect its python fences
unconditionally; now nothing collects until `--codeblocks`, `[tool.rustest] codeblocks =
true`, or the pytest ini section's `codeblocks = true` turns it on. Without one of those,
naming a `.md` file is a usage error, exit 4, `found no collectors for <path>` — pytest's
own answer for the same argument.

```bash
rustest README.md user_guide/*.md               # exit 4 unless something enables it
rustest README.md user_guide/*.md --codeblocks   # tests the docs
rustest tests/                                   # a directory never picks up .md, either way
```

**A block's `def test_*` functions really run now, each as its own node.** The old mechanism
indented a block into `def run_codeblock(): <body>` and called the wrapper, so any test
function the block defined was a local of that wrapper — defined, never called, its
assertions never checked. A block now execs at module level, at collect time, and is
enumerated the same way a `.py` file is: a `def test_*` inside it collects and runs as its
own node, `page.md::codeblock_N_line_M::test_name`, and fixtures, `@parametrize`, `Test*`
classes and xunit hooks all work inside it. This surfaced 109 previously invisible failures
on this repository's own docs — the feature doing its job, not a regression, but real work
if your own markdown relies on the tier.

Everything that follows from real execution is a further breaking change in its own right:
node ids for a block with tests gain a segment, a broken block is now a failing *test*
rather than a file-level collection error (exit 1, not 2), a stale `--lf` entry for the old
single-node id will not match, and an autouse fixture no longer reaches a block's top-level
statements (only the tests inside it) because the body now runs before any fixture closure
exists. See the Changelog's "documentation code block execution" entry for the full
seven-item list, and [Markdown testing](markdown-testing.md) for the mechanism itself.

## Migrating from pytest

Most pytest suites need no changes at all: run `rustest tests/` and the shim does the rest.
If you want to write against rustest's own API instead, the decorators have the same names:

```python
from rustest import fixture, parametrize, mark, approx, raises


@fixture
def account():
    return {"balance": 100}


@parametrize("amount,expected", [(10, 110), (25, 125)])
def test_deposit(account, amount, expected):
    account["balance"] += amount
    assert account["balance"] == expected


def test_overdraft(account):
    with raises(KeyError):
        account["overdraft"]
```

**Using pytest plugins?** rustest supports none by design. See the
[plugin migration guide](pytest-plugins.md) for what replaces the ten most popular ones,
several of which are built in (`--cov` for pytest-cov, `@mark.asyncio` for pytest-asyncio,
the `mocker` fixture for pytest-mock).

See [Comparison with pytest](comparison.md) for the feature-by-feature table.

## On the roadmap

Not yet implemented, and tracked rather than promised:

- **JUnit XML output.** `--report-json` exists today; JUnit does not.
- **HTML reports.**
- **General test timeouts.** `@mark.timeout()` is accepted as a mark but enforces nothing.
  Async tests are the exception: `@mark.asyncio(timeout=N)` is rustest's own extension and is
  enforced with `asyncio.wait_for`.

Three entries that used to sit on this list have shipped. **Parallel execution control** is
`-n` / `--workers` (never `-j`, which this page once listed and rustest has never had).
**Coverage integration** is `--cov` / `--cov-report`, which needs the `cov` extra. And
**better error messages** arrived as assertion rewriting, so a failed `assert` now reports
the values (`AssertionError: assert 41 == 42`).

See the [GitHub issues](https://github.com/Apex-Engineers-Inc/rustest/issues) for the live
roadmap.

## Older releases

The 0.3 to 0.4 to 0.5 upgrade notes that used to live here have been removed. They described
a runner that predates the current engine by a dozen releases, and
[`CHANGELOG.md`](changelog.md) is the actual historical record.

## Links

- [GitHub Repository](https://github.com/Apex-Engineers-Inc/rustest)
- [Issue Tracker](https://github.com/Apex-Engineers-Inc/rustest/issues)
- [PyPI Package](https://pypi.org/project/rustest/)
