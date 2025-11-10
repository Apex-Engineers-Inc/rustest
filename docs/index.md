# Overview

<div style="text-align: center; margin-bottom: 2rem;">
  <img src="assets/logo.svg" alt="rustest logo" style="height: 300px; width: 300px;">
</div>

**Rust-powered pytest-compatible test runner**

Rustest (pronounced like Russ-Test) is a Rust-powered test runner that aims to provide the most common pytest ergonomics with a focus on raw performance. Get **massive speedups (8.5× average, up to 19× faster)** with familiar syntax and minimal setup.

## Why rustest?

- :material-rocket-launch: **8.5× average speedup** over pytest on the benchmark matrix (reaching 19× on 5k-test suites)
- :material-check-circle: Familiar `@fixture`, `@parametrize`, `@skip`, and `@mark` decorators
- :material-magnify: Automatic test discovery (`test_*.py` and `*_test.py` files)
- :material-file-document: **Built-in markdown code block testing** (like pytest-codeblocks, but faster)
- :material-bullseye-arrow: Simple, clean API—if you know pytest, you already know rustest
- :material-calculator: Built-in `approx()` helper for tolerant numeric comparisons across scalars, collections, and complex numbers
- :material-bug-check: `raises()` context manager for precise exception assertions with optional message matching
- :material-package-variant: Easy installation with pip or uv
- :material-lightning-bolt: Low-overhead execution keeps small suites feeling instant

## Quick Example

```python
from rustest import fixture, parametrize, mark, approx, raises

@fixture
def numbers() -> list[int]:
    return [1, 2, 3, 4, 5]

def test_sum(numbers: list[int]) -> None:
    assert sum(numbers) == approx(15)

@parametrize("value,expected", [(2, 4), (3, 9), (4, 16)])
def test_square(value: int, expected: int) -> None:
    assert value ** 2 == expected

@mark.slow
def test_expensive_operation() -> None:
    result = sum(range(1000000))
    assert result > 0

def test_division_by_zero() -> None:
    with raises(ZeroDivisionError, match="division by zero"):
        1 / 0
```

Run your tests:

```bash
rustest
```

## Performance

Rustest is designed for speed. The benchmark matrix generates identical pytest and rustest suites ranging from 1 to 5,000 tests and runs each command five times. Rustest delivers an **8.5× average speedup** and hits **19× faster** execution on the largest suite:

| Test Count | pytest (mean) | rustest (mean) | Speedup | pytest tests/s | rustest tests/s |
|-----------:|--------------:|---------------:|--------:|----------------:|-----------------:|
|          1 |       0.428s |        0.116s |    3.68x |             2.3 |              8.6 |
|          5 |       0.428s |        0.120s |    3.56x |            11.7 |             41.6 |
|         20 |       0.451s |        0.116s |    3.88x |            44.3 |            171.7 |
|        100 |       0.656s |        0.133s |    4.93x |           152.4 |            751.1 |
|        500 |       1.206s |        0.146s |    8.29x |           414.4 |           3436.1 |
|      1,000 |       1.854s |        0.171s |   10.83x |           539.4 |           5839.4 |
|      2,000 |       3.343s |        0.243s |   13.74x |           598.3 |           8219.9 |
|      5,000 |       7.811s |        0.403s |   19.37x |           640.2 |          12399.7 |

**What to expect:** tiny suites (≤20 tests) still run **~3–4× faster**, growing suites around 100–500 tests see **~5–8× speedups**, and large suites with 1,000+ tests jump to **~11–19× faster** execution.

Our integration suite (~200 tests) remains a great proxy for day-to-day development and continues to show **~2.1× wall-clock speedups**. See the [Performance](advanced/performance.md) page for breakdowns, methodology, and replication instructions.

## Next Steps

- [Installation](getting-started/installation.md) - Install rustest
- [Quick Start](getting-started/quickstart.md) - Write your first tests
- [User Guide](guide/writing-tests.md) - Learn about fixtures, parametrization, and more
- [API Reference](api/overview.md) - Complete API documentation

## License

rustest is distributed under the terms of the MIT license.
