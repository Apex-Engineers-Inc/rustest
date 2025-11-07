## Performance Comparison

We benchmarked pytest against rustest using the **actual rustest integration test suite** with **~199 tests** covering real-world scenarios:

- **Basic functionality tests**: Test discovery, execution, and reporting
- **Fixture tests**: Simple fixtures, nested fixtures, fixture dependencies
- **Fixture scopes**: Function, class, module, and session-scoped fixtures
- **Yield fixtures**: Setup/teardown with proper cleanup
- **Parametrized tests**: Multiple parameter combinations
- **conftest.py integration**: Shared fixtures across test files
- **Error handling**: Test failures, skips, and edge cases
- **Marks**: Skip marks and custom test markers

### Benchmark Results (local measurements)

| Test Runner | Reported Runtime† | Wall Clock‡ | Speedup (wall) | Command |
|-------------|------------------|-------------|----------------|---------|
| pytest      | 0.43–0.59s       | 1.33–1.59s  | 1.0x (baseline) | `pytest tests/ examples/tests/ -q`
| rustest     | 0.003s           | 0.69–0.70s  | **~2.1x faster** | `python -m rustest tests/ examples/tests/`*

† pytest and rustest only include active test execution time in their summaries. rustest's number omits Python interpreter start-up.

‡ Wall-clock timing captured with the shell `time` builtin across two consecutive runs in the same container.

*Executed with `PYTHONPATH=python` to exercise the in-repo sources without installing the package.

### Why is rustest faster?

The improvement is smaller than earlier marketing claims but still significant on the rustest suite:

#### 1. **Reduced Startup Overhead**
- **pytest**: Python interpreter startup, import overhead, plugin loading (~0.2s) dominate small suites.
- **rustest**: Native Rust binary reaches test execution quickly, so less wall time is spent booting the runner.

#### 2. **Rust-Native Test Discovery**
- **pytest**: Imports every test module for discovery, paying the Python import cost.
- **rustest**: Scans the filesystem from Rust and delays Python imports until execution.

#### 3. **Optimized Fixture Resolution**
- Rust-based dependency graphs reduce per-test setup work compared to Python-driven orchestration.

#### 4. **Lean Orchestration**
- Scheduling and reporting happen in Rust, minimizing Python bookkeeping in between tests.

### Real-world Impact

For typical test suites, the ~2x speedup has noticeable effects:

**Small suite (200 tests, like rustest itself):**
- **pytest**: ~1.46s wall time (average of two runs)
- **rustest**: ~0.70s wall time
- **Time saved**: ~0.76s per run

**Medium suite (1,000 tests, projected):**
- **pytest**: ~7.3s assuming similar scaling
- **rustest**: ~3.4s
- **Time saved**: ~3.9s per run

**Large suite (10,000 tests, projected):**
- **pytest**: ~73s
- **rustest**: ~34s
- **Time saved**: ~39s per run

This speedup is especially impactful during development when running tests frequently. Even a ~0.7s loop for ~200 tests keeps feedback tight and scales to bigger suites with noticeably less wait time.

### Large Parametrization Stress Test

Synthetic stress cases highlight the scheduling overhead differences even more starkly. We added [`benchmarks/test_large_parametrize.py`](benchmarks/test_large_parametrize.py), which drives a single test function through **10,000 parametrized invocations** asserting `value + value == 2 * value`. Averaging three runs per tool produced the following:

| Test Runner | Avg. Wall Clock | Speedup | Command |
|-------------|-----------------|---------|---------|
| pytest      | 9.72s           | 1.0x    | `pytest benchmarks/test_large_parametrize.py -q`* |
| rustest     | 0.41s           | **~24x** | `python -m rustest benchmarks/test_large_parametrize.py`* |

The large parameter matrix magnifies rustest's lean execution pipeline: minimal Python bookkeeping keeps the dispatch loop tight even when thousands of cases are queued. Pytest, by contrast, spends significantly more time orchestrating parametrized calls from Python.

*Commands executed with `PYTHONPATH=python` from this repository so both runners exercise the local sources. Measurements average three `time.perf_counter()` samples captured via a Python timing harness with output suppressed.

### Running the Benchmarks

To reproduce these benchmarks:

```bash
# Run the rustest integration test suite with pytest
pytest tests/ -q

# Run with rustest (requires installation)
rustest tests/

# Or run the synthetic benchmark suite
python3 profile_tests.py  # Uses benchmarks_pytest/ directory
```
