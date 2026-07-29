<div align="center">

![rustest logo](assets/logo.svg)

</div>

**rustest is a Rust-powered test runner that runs your existing pytest suite unchanged.**

Point it at your tests. There is no compatibility flag, no migration step, and no plugin to
install — `import pytest` resolves to rustest's own implementation on every run.

📚 **[Documentation](https://apex-engineers-inc.github.io/rustest)** · [Quick Start](https://apex-engineers-inc.github.io/rustest/user-guide/quickstart.html) · [Coming from pytest](https://apex-engineers-inc.github.io/rustest/user-guide/pytest-compat.html)

## Try it now

<!--pytest.mark.skip-->
```bash
pip install rustest
rustest tests/
```

That is the whole migration for most suites.

## Does it actually agree with pytest?

This is the question that matters more than speed, so it is measured rather than asserted.
Seventeen real, unmodified open-source pytest suites are run under both runners and graded
on **every node id and every outcome count**:

| Verdict | Count | Meaning |
|---|---:|---|
| **MATCH** | 13 | Every node id and every outcome count identical to pytest's |
| **EXPLAINED** | 4 | Every remaining difference covered by a ledger entry naming its mechanism |
| **DIVERGE** | 0 | — |

The four EXPLAINED rows are worth naming, because none of them is rustest getting a test
result wrong: **fastapi** deselects a different count under the `anyio` plugin;
**marshmallow** puts a wall-clock reading inside a node id and **psutil**'s one differing
test is a live CPU-frequency reading (which failed on *pytest's* side and passed on
rustest's); and **rich**'s `test_suppress` introspects the *identity* of the `pytest`
module object rather than using its API — the one class of test rustest structurally
cannot pass.

Underneath the sweep sits a conformance corpus that diffs rustest against real pytest on
node ids, outcome tallies and exit codes, case by case, on every commit.

## How fast?

**1.1x to 5.7x** across those same seventeen suites. Aggregated over all of them it is
**1.23x**; across the fifteen that are not dominated by their own test bodies it is
**2.74x**.

Those numbers are deliberately not a single headline multiplier, because a test runner can
only make the *framework* part of a run faster — never your code. That share is measurable
per suite, and it is the ceiling:

| Suite | Framework share | Speedup |
|---|---:|---:|
| werkzeug | 1% | 1.62x |
| member-designer (6,132 tests) | 4% | 1.10x |
| more-itertools | 13% | 1.82x |
| jsonschema | 24% | 2.91x |
| jinja2 | 72% | 2.38x |
| sqlparse | 74% | 2.23x |
| click | 78% | 2.50x |
| marshmallow | 90% | 2.73x |

**A 1.2x on a body-bound suite and a 5.7x on a framework-bound one are the same result.**
If your suite spends 95% of its wall clock inside your own functions, no runner will give
you more than a few percent — and the component numbers are where the difference comes
from:

| Component (500 files / 5,000 tests) | pytest | rustest | |
|---|---:|---:|---:|
| Warm collection | 8.39s | 227.6ms | **~37x** |
| Marginal per-test framework overhead | 933.6µs | 117.9µs | **~8x** |

Full table, method, machine conditions and caveats — including the suite where rustest was
*slower* until a fix landed, and why the overhead metric above should be quoted carefully —
in **[Performance](https://apex-engineers-inc.github.io/rustest/user-guide/performance.html)**.

## The agent loop

Sub-second feedback for tools, not just for humans. `--llm` emits the run as JSONL —
failures only, one object per line, with a published schema — and `--lf` reruns only what
failed off a warm collection:

<!--pytest.mark.skip-->
```bash
rustest --llm tests/          # full run; failures as JSONL on stdout
# ... apply a fix ...
rustest --llm --lf tests/     # only what failed, ~230ms to collect
```

```json
{"t":"fail","id":"test_auth.py::test_login","file":"test_auth.py","line":8,
 "status":"failed","msg":"...\nAssertionError: assert 401 == 200",
 "stdout":"POST /login user=admin"}
```

One line, one parse: the id, the file, the line, why it failed **with the values**, and
what the test printed on the way there. `rustest --llm-schema` prints the contract.
[Details](https://apex-engineers-inc.github.io/rustest/user-guide/llm-output.html).

## Quick start

Write a test in `test_example.py`:

```python
from rustest import fixture, parametrize, mark, raises

@fixture
def numbers():
    return [1, 2, 3, 4, 5]

def test_sum(numbers):
    assert sum(numbers) == 15

@parametrize("value,expected", [(2, 4), (3, 9)])
def test_square(value, expected):
    assert value ** 2 == expected

@mark.asyncio
async def test_async():
    result = 42
    assert result == 42

def test_exception():
    with raises(ZeroDivisionError):
        1 / 0
```

pytest's own spellings work too — this is the same file, and it runs identically:

```python
import pytest

@pytest.fixture
def numbers():
    return [1, 2, 3, 4, 5]

@pytest.mark.parametrize("value,expected", [(2, 4), (3, 9)])
def test_square(value, expected):
    assert value ** 2 == expected
```

Run them:

<!--pytest.mark.skip-->
```bash
rustest                      # Run all tests
rustest tests/               # Run a specific directory
rustest -k "test_sum"        # Filter by name
rustest -m "slow"            # Filter by mark
rustest -n 8                 # Set the worker pool size
rustest --lf                 # Rerun last failed
rustest -x                   # Stop on first failure
rustest --cov src            # Coverage, no plugin needed
```

## What's built in

| | Replaces |
|---|---|
| `@mark.asyncio`, loop scopes | pytest-asyncio |
| The `mocker` fixture | pytest-mock |
| `--cov` / `--cov-report` (via `sys.monitoring`) | pytest-cov |
| A worker pool, `-n` | pytest-xdist (at file granularity) |
| `--lf` / `--ff` / `-x` / `--maxfail` | built into pytest |
| Python fences in `.md` files run as tests | pytest-codeblocks |
| `--llm` JSONL, `--report-json` | — |

## Compatibility, stated honestly

Compatibility is the default behaviour, not a mode — but it is not total, and the gaps are
documented rather than discovered. rustest does **not** have:

- **A plugin system or hook system.** By design. A conftest's fixtures load; its hooks are
  ignored. [Why, and what replaces the ten most popular plugins](https://apex-engineers-inc.github.io/rustest/user-guide/pytest-plugins.html)
- **Ten built-in fixtures**, including `pytester`, `recwarn`, `capfdbinary` and
  `record_property`. Requesting one is a loud, named error — never a silent skip
- **`xfail_strict` ini or `--runxfail`** (the `strict=` keyword does work)
- **Item reordering** for shared higher-scoped parametrized fixtures
- **A warnings channel** — behaviour matches pytest, the diagnostic message does not

The complete, current list is
**[pytest compatibility](https://apex-engineers-inc.github.io/rustest/user-guide/pytest-compat.html)**.
There is no `--v1` escape hatch behind it: the previous engine was deleted, not frozen, so
that page is the whole statement of what rustest does.

## Installation

<!--pytest.mark.skip-->
```bash
pip install rustest
# or
uv add rustest
```

**Python 3.12 – 3.14.** [Installation guide](https://apex-engineers-inc.github.io/rustest/user-guide/installation.html)

## Upgrading from an older rustest?

`--pytest-compat` and `--v1` are removed and now exit 4; `rustest.run()` is keyword-only
and returns an exit code rather than a `RunReport`; `indirect=` parametrization follows
pytest's semantics; and `--llm` output is schema 2. See the
**[upgrade guide](https://apex-engineers-inc.github.io/rustest/user-guide/migration-guide.html)**
and [CHANGELOG.md](CHANGELOG.md).

## Learn more

- **[Quick Start](https://apex-engineers-inc.github.io/rustest/user-guide/quickstart.html)** — five minutes, start to finish
- **[New to testing?](https://apex-engineers-inc.github.io/rustest/user-guide/intro-why-test.html)** — a beginner track that assumes nothing
- **[CLI reference](https://apex-engineers-inc.github.io/rustest/user-guide/cli.html)** — every flag
- **[API reference](https://apex-engineers-inc.github.io/rustest/reference/index.html)** — generated from the source

## Contributing

Contributions welcome. See the [development guide](https://apex-engineers-inc.github.io/rustest/user-guide/development.html) for setup.

## License

MIT License. See [LICENSE](LICENSE) for details.
