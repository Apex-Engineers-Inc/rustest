# Performance

Rustest is designed for speed. This page details benchmark results and explains why rustest is faster than pytest.

## Benchmark Results

We benchmarked pytest against rustest using the **actual rustest integration test suite** with **~199 tests** covering real-world scenarios:

- Basic functionality tests: Test discovery, execution, and reporting
- Fixture tests: Simple fixtures, nested fixtures, fixture dependencies
- Fixture scopes: Function, class, module, and session-scoped fixtures
- Yield fixtures: Setup/teardown with proper cleanup
- Parametrized tests: Multiple parameter combinations
- conftest.py integration: Shared fixtures across test files
- Error handling: Test failures, skips, and edge cases
- Marks: Skip marks and custom test markers

### Integration Suite (~200 tests)

| Test Runner | Wall Clock | Speedup | Command |
|-------------|------------|---------|---------|
| pytest      | 1.33–1.59s | 1.0x (baseline) | `pytest tests/ examples/tests/ -q` |
| rustest     | 0.69–0.70s | **~2.1x faster** | `python -m rustest tests/ examples/tests/` |

### Large Parametrization Stress Test (10,000 tests)

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

The ~2x speedup has noticeable effects on development workflow:

### Small Suite (200 tests)

Like the rustest project itself:

- **pytest**: ~1.46s wall time
- **rustest**: ~0.70s wall time
- **Time saved**: ~0.76s per run

!!! tip "Development Impact"
    During active development, you might run tests 50+ times per day. At 0.76s savings per run, that's **38 seconds saved daily**—keeping you in flow state.

### Medium Suite (1,000 tests, projected)

- **pytest**: ~7.3s
- **rustest**: ~3.4s
- **Time saved**: ~3.9s per run
- **Daily savings** (50 runs): ~3.25 minutes

### Large Suite (10,000 tests, projected)

- **pytest**: ~73s
- **rustest**: ~34s
- **Time saved**: ~39s per run
- **Daily savings** (50 runs): ~32.5 minutes

### CI/CD Impact

For a typical CI pipeline running tests on every commit:

**Repository with 1,000 tests:**
- 100 commits/day
- pytest: ~12 minutes total
- rustest: ~5.6 minutes total
- **Time saved**: ~6.4 minutes of CI time per day

**Repository with 10,000 tests:**
- 100 commits/day
- pytest: ~2 hours total
- rustest: ~56 minutes total
- **Time saved**: ~1 hour of CI time per day

## Performance Characteristics

### Scales with Test Count

Rustest's overhead is mostly fixed (startup time), so the benefits increase with more tests:

```
200 tests:    2.1x faster
1,000 tests:  2.1x faster (projected)
10,000 tests: 2.1x faster (projected)
```

### Parametrization Benefits

Heavy parametrization sees even bigger gains due to efficient dispatch:

```
Standard tests:      ~2.1x faster
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

### Integration Suite

```bash
# pytest
pytest tests/ examples/tests/ -q

# rustest
rustest tests/ examples/tests/
```

### Stress Test

```bash
# Using the profile script
python3 profile_tests.py

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
