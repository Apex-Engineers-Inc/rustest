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
rustest --pytest-compat tests/
```

See the speedup immediately, then migrate to native rustest for full features.

## Why Rustest?

- 🚀 **8.5× average speedup** over pytest (up to 19× on large suites)
- 🧪 **pytest-compatible** — Run existing tests with `--pytest-compat`
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

## Installation

<!--pytest.mark.skip-->
```bash
pip install rustest
# or
uv add rustest
```

**Python 3.10-3.14 supported.** [📖 Installation Guide →](https://apex-engineers-inc.github.io/rustest/getting-started/installation/)

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
