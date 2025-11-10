# Performance

Rustest is designed for speed. This page details benchmark results and explains why rustest is faster than pytest.

## Benchmark Results

### Synthetic benchmark matrix (1–5,000 tests)

We generated identical pytest and rustest suites with 1, 5, 20, 100, 500, 1,000, 2,000, and 5,000 tests. Each command ran five times and we report the mean wall-clock duration:

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

Key takeaways:

- **8.53× average speedup** (7.03× geometric mean) across the matrix
- **16.22× weighted speedup** when weighting by the number of executed tests
- **16.18s → 1.45s** total runtime reduction when summing all eight suites

### What should my suite expect?

- **Tiny suites (≤20 tests):** Rustest trims startup overhead for **~3–4× faster** runs. Think "still instant" but consistently a few hundred milliseconds quicker than pytest.
- **Growing suites (≈100–500 tests):** Expect **~5–8× faster** execution as file-system discovery, fixture setup, and orchestration all move into Rust.
- **Large suites (≥1,000 tests):** The fixed startup cost disappears in the noise, unlocking **~11–19× faster** runs and dramatic CI savings.

### Integration suite (~200 tests)

The production integration suite still shows a consistent **~2.1× wall-clock speedup**, providing a realistic reference point for everyday development:

| Test Runner | Wall Clock | Speedup | Command |
|-------------|------------|---------|---------|
| pytest      | 1.33–1.59s | 1.0x (baseline) | `pytest tests/ examples/tests/ -q` |
| rustest     | 0.69–0.70s | **~2.1x faster** | `python -m rustest tests/ examples/tests/` |

### Large parametrization stress test (10,000 tests)

We created a synthetic stress test in `benchmarks/test_large_parametrize.py` with **10,000 parametrized invocations** to test scheduling overhead:

| Test Runner | Avg. Wall Clock | Speedup | Command |
|-------------|-----------------|---------|---------|
| pytest      | 9.72s           | 1.0x    | `pytest benchmarks/test_large_parametrize.py -q` |
| rustest     | 0.41s           | **~24x faster** | `python -m rustest benchmarks/test_large_parametrize.py` |

!!! info "Why the difference?"
    The large parameter matrix magnifies rustest's lean execution pipeline. Minimal Python bookkeeping keeps the dispatch loop tight even when thousands of cases are queued.

## Why is rustest Faster?

### 1. Reduced Startup Overhead

**pytest:**
- Python interpreter startup
- Import overhead for plugins
- Plugin loading and initialization
- ~0.2s overhead before tests even run

**rustest:**
- Native Rust binary reaches test execution quickly
- Minimal imports until actual test execution
- Less wall time spent booting the runner

### 2. Rust-Native Test Discovery

**pytest:**
- Imports every test module during discovery
- Pays the Python import cost upfront
- Plugin hooks slow down discovery

**rustest:**
- Scans the filesystem from Rust
- Pattern matching in native code
- Delays Python imports until execution

### 3. Optimized Fixture Resolution

**pytest:**
- Python-driven dependency resolution
- Dynamic lookup for each test
- Plugin hooks add overhead

**rustest:**
- Rust-based dependency graph
- Efficient resolution algorithm
- Minimal Python overhead per test

### 4. Lean Orchestration

**pytest:**
- Python bookkeeping between tests
- Plugin hook calls
- Result collection overhead

**rustest:**
- Scheduling happens in Rust
- Reporting happens in Rust
- Minimal Python overhead

## Real-World Impact

The benchmark matrix shows how the gap widens as suites grow:

### Small suite (100 tests)

- **pytest**: 0.66s mean (0.62s median)
- **rustest**: 0.13s mean (0.13s median)
- **Time saved**: ~0.52s per run (**~26s/day** at 50 local runs)

### Medium suite (1,000 tests)

- **pytest**: 1.85s mean (1.48s median)
- **rustest**: 0.17s mean (0.17s median)
- **Time saved**: ~1.68s per run (**~1.4 minutes/day** at 50 local runs)

### Large suite (5,000 tests)

- **pytest**: 7.81s mean (5.75s median)
- **rustest**: 0.40s mean (0.36s median)
- **Time saved**: ~7.41s per run (**~6.2 minutes/day** at 50 local runs)

### CI/CD Impact

For a typical CI pipeline running on every commit:

**Repository with 1,000 tests:**
- 100 commits/day
- pytest: ~185s total (~3.1 minutes)
- rustest: ~17s total (~0.3 minutes)
- **Time saved**: ~168s per day (~2.8 minutes)

**Repository with 5,000 tests:**
- 100 commits/day
- pytest: ~781s total (~13.0 minutes)
- rustest: ~40s total (~0.7 minutes)
- **Time saved**: ~741s per day (~12.3 minutes)

## Performance Characteristics

### Scales with Test Count

Rustest's overhead is mostly fixed (startup time), so the benefits increase with more tests:

```
100 tests:    4.9x faster
1,000 tests:  10.8x faster
5,000 tests:  19.4x faster
```

### Parametrization Benefits

Heavy parametrization sees even bigger gains due to efficient dispatch:

```
Standard tests:  up to 19.4x faster (5,000-case matrix)
10,000 parameters:   ~24x faster
```

### Fixture-Heavy Suites

Fixture resolution is optimized in Rust, providing consistent speedup regardless of fixture complexity.

## Measurement Methodology

### Wall Clock Timing

All measurements use wall clock time (what you actually wait), not just reported runtime:

```bash
# Using time command
time pytest tests/ -q
time rustest tests/

# Or Python's time.perf_counter()
import time
start = time.perf_counter()
run_tests()
elapsed = time.perf_counter() - start
```

### Consistency

- All benchmarks run on the same hardware
- Multiple runs averaged (typically 3 runs)
- Cold cache and warm cache both tested
- Same test suite for fair comparison

## Running Benchmarks Yourself

### Benchmark matrix

```bash
# Run the multi-suite benchmark matrix
python3 profile_tests.py --runs 5

# Render the Markdown summary (writes BENCHMARKS.md)
python3 generate_comparison.py
```

### Integration Suite

```bash
# pytest
pytest tests/ examples/tests/ -q

# rustest
rustest tests/ examples/tests/
```

### Stress Test

```bash
# Or manually
pytest benchmarks/test_large_parametrize.py -q
rustest benchmarks/test_large_parametrize.py
```

### Your Own Suite

```bash
# Benchmark your tests
time pytest your_tests/
time rustest your_tests/

# Compare results
```

## Performance Tips

### Maximize Speed

1. **Use rustest's native features**: Avoid pytest-only features that require compatibility shims
2. **Minimize imports**: Put heavy imports inside tests, not at module level
3. **Use appropriate fixture scopes**: Module/session scopes reduce setup overhead
4. **Batch related tests**: Test classes can share class-scoped fixtures

### When pytest Might Be Faster

- Very small suites (<10 tests): Startup overhead matters less
- Tests with heavy pytest plugin dependencies: rustest doesn't support plugins
- Tests using pytest-specific features: Compatibility layer adds overhead

## Future Improvements

Planned performance enhancements:

- **Parallel execution**: Run tests across multiple cores
- **Incremental test running**: Only run tests affected by changes
- **Smarter caching**: Cache fixture results across runs
- **Even faster discovery**: Further optimize file system scanning

## See Also

- [Comparison with pytest](comparison.md) - Feature comparison
- [Development Guide](development.md) - Contributing performance improvements
