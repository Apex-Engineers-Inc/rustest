# rustest

**Rust-powered pytest-compatible test runner**

Rustest (pronounced like Russ-Test) is a Rust-powered test runner that aims to provide the most common pytest ergonomics with a focus on raw performance. Get **~2x faster** test execution with familiar syntax and minimal setup.

## Why rustest?

- :material-rocket-launch: **About 2x faster** than pytest on the rustest integration test suite
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

Rustest is designed for speed. Our latest benchmarks on the rustest integration suite (~200 tests) show a consistent **2.1x wall-clock speedup** over pytest:

| Test Runner | Wall Clock | Speedup | Command |
|-------------|------------|---------|---------|
| pytest      | 1.33–1.59s | 1.0x (baseline) | `pytest tests/ examples/tests/ -q` |
| rustest     | 0.69–0.70s | **~2.1x faster** | `python -m rustest tests/ examples/tests/` |

!!! tip "Why is rustest faster?"
    - **Near-zero startup time**: Native Rust binary minimizes overhead
    - **Rust-native test discovery**: Minimal imports until test execution
    - **Optimized fixture resolution**: Efficient dependency graph resolution
    - **Lean orchestration**: Rust handles scheduling and reporting

See the [Performance](advanced/performance.md) page for detailed benchmarks and analysis.

## Next Steps

- [Installation](getting-started/installation.md) - Install rustest
- [Quick Start](getting-started/quickstart.md) - Write your first tests
- [User Guide](guide/writing-tests.md) - Learn about fixtures, parametrization, and more
- [API Reference](api/overview.md) - Complete API documentation

## License

rustest is distributed under the terms of the MIT license.
