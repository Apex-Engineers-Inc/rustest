<div align="center">

![rustest logo](assets/logo.svg)

</div>

Rustest is a Rust-powered pytest-compatible test runner delivering **8.5× average speedup** with familiar pytest syntax and zero setup.

📚 **[Full Documentation](https://apex-engineers-inc.github.io/rustest)** | [Getting Started](https://apex-engineers-inc.github.io/rustest/getting-started/quickstart/) | [Migration Guide](https://apex-engineers-inc.github.io/rustest/from-pytest/migration/)

## 🚀 Try It Now

Run your existing pytest tests with rustest — no code changes required:

<!--pytest.mark.skip-->
```bash
pip install rustest
rustest tests/
```

pytest compatibility is **on by default** — `import pytest` resolves to rustest's shim, so
existing suites just run. (The old `--pytest-compat` flag is gone; it is now the default.
`--v1` selects the legacy engine while the new one catches up — see CHANGELOG.md.)

See the speedup immediately, then migrate to native rustest for full features.

## Why Rustest?

- 🚀 **8.5× average speedup** over pytest (up to 19× on large suites)
- 🧪 **pytest-compatible** — Run existing tests unchanged; no flag needed
- ✅ **Familiar API** — Same `@fixture`, `@parametrize`, `@mark` decorators
- 🔄 **Built-in async & mocking** — No pytest-asyncio or pytest-mock plugins needed
- 🐛 **Clear error messages** — Vitest-style output with Expected/Received diffs
- 📝 **Markdown testing** — Test code blocks in documentation
- 🛠️ **Rich fixtures** — `tmp_path`, `monkeypatch`, `mocker`, `capsys`, `caplog`, `cache`, and more

## Performance

Rustest delivers consistent speedups across test suites of all sizes:

| Test Count | pytest | rustest | Speedup |
|-----------:|-------:|--------:|--------:|
|         20 | 0.45s  |  0.12s  |  3.8×   |
|        500 | 1.21s  |  0.15s  |  8.3×   |
|      5,000 | 7.81s  |  0.40s  | 19.4×   |

**Expected speedups:** 3-4× for small suites, 5-8× for medium suites, 11-19× for large suites.

**[📊 Full Performance Analysis →](https://apex-engineers-inc.github.io/rustest/advanced/performance/)**

The table above is the **v1** engine (`--v1`). Read on for what the new default (v2)
measures today.

## v2 Baselines (the Phase 2 gate)

`rustest <paths>` with no flag has run the **v2** engine since the Phase 1c flip. The
table below is the first real measurement of it, alongside pytest and the v1 numbers
above, on the same generated all-passing suites used throughout `conformance/bench/`.

**Expectation management: v2 is not yet fast.** Every run today spawns a fresh worker
pool from scratch, there is no static (import-free) collection tier, and there is no
manifest cache — v1 already has the benefit of years of tuning around its own
architecture, and v2 has none of that yet. The gap below is dominated by that
**fixed** per-run cost (worker-pool spawn); the **marginal** per-test cost — what's left
once the fixed cost is subtracted out — is already close to v1's (see the derived
numbers).

| files | tests | pytest run | rustest v1 run | rustest v2 run | rustest v2 collect |
| ----: | ----: | ---------: | --------------: | --------------: | -------------------: |
|    10 |   100 |      0.87s |            0.51s |            5.29s |                5.82s |
|   100 |  1000 |      1.77s |            0.99s |            8.81s |                8.52s |
|   500 |  5000 |      6.33s |            3.27s |           11.29s |                8.51s |

Marginal per-test overhead, derived from the two largest sizes: pytest **1140.7
us/test**, rustest v1 **569.1 us/test**, rustest v2 **618.1 us/test**.

Full methodology (suite generation, command order, the ordering-bias caveat) and the
raw data live in the "Baselines" section of
[`conformance/README.md`](conformance/README.md) and the tracked
[`conformance/baselines.json`](conformance/baselines.json).

**These numbers ARE the Phase 2 baseline.** Phase 2
([`docs/superpowers/plans/2026-07-26-phase2-speed.md`](docs/superpowers/plans/2026-07-26-phase2-speed.md))
targets warm collection **≤ 50ms** on the 5k-test suite above and per-test framework
overhead **< 200µs**, measured against exactly this table — a static Rust collector, a
manifest cache and parallel-dispatch tuning are what close the gap from here.

## Installation

<!--pytest.mark.skip-->
```bash
pip install rustest
# or
uv add rustest
```

**Python 3.12-3.14 supported.** [📖 Installation Guide →](https://apex-engineers-inc.github.io/rustest/getting-started/installation/)

## Quick Start

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

Run your tests:

<!--pytest.mark.skip-->
```bash
rustest                      # Run all tests
rustest tests/               # Run specific directory
rustest -k "test_sum"        # Filter by name
rustest -m "slow"            # Filter by mark
rustest --lf                 # Rerun last failed
rustest -x                   # Exit on first failure
```

**[📖 Full Documentation →](https://apex-engineers-inc.github.io/rustest)**

## Learn More

- **[Getting Started](https://apex-engineers-inc.github.io/rustest/getting-started/quickstart/)** — Complete quickstart guide
- **[Migration from pytest](https://apex-engineers-inc.github.io/rustest/from-pytest/migration/)** — 5-minute migration guide
- **[User Guide](https://apex-engineers-inc.github.io/rustest/guide/writing-tests/)** — Fixtures, parametrization, marks, assertions
- **[API Reference](https://apex-engineers-inc.github.io/rustest/api/overview/)** — Complete API documentation

## Contributing

Contributions welcome! See the [Development Guide](https://apex-engineers-inc.github.io/rustest/advanced/development/) for setup instructions.

## License

MIT License. See [LICENSE](LICENSE) for details.
