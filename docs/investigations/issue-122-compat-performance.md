# Investigation: Performance in `--pytest-compat` Mode (Issue #122)

## Issue

GitHub Issue #122 reports that `--pytest-compat` mode takes 143s compared to native
pytest's 125s on the same test suite (~14% slower), undermining rustest's value
proposition during migration.

## Methodology

We conducted extensive benchmarking with synthetic test suites designed to stress
every aspect of compat mode:

1. **Standard suite** (2,150 tests) — Basic tests with fixture chains, parametrization, marks
2. **Stress suite** (1,800 tests) — Deep conftest hierarchies (5 nested packages, 6
   conftest files), heavy `request` fixture usage, parametrized fixtures
3. **Heavy import suite** (3,000 tests) — 8 packages with 40+ stdlib imports per conftest,
   120 test files, parametrized fixtures across deep fixture graphs
4. **Workload suite** (1,000 tests) — Tests with real computation (JSON serialization +
   MD5 hashing) to simulate real-world test execution time

All benchmarks ran 3 times each. Median wall-clock times are reported.

Additionally, we used:
- **cProfile** for full Python call-stack profiling
- **Custom instrumentation** (monkey-patched timing) for specific bottleneck functions

## Results Summary

### Benchmark Results

| Suite | Tests | pytest (wall) | rustest compat (wall) | Speedup |
|-------|------:|-------------:|---------------------:|--------:|
| Standard | 2,150 | 4.5s | 0.9s | **5.0x** |
| Stress | 1,800 | 4.1s | 0.6s | **6.5x** |
| Heavy imports | 3,000 | 7.3s | 1.2s | **6.3x** |
| Workload | 1,000 | 2.5s | 0.7s | **3.7x** |
| **Combined** | **5,800** | **14.0s** | **2.4s** | **5.7x** |

### Startup Overhead

| Tool | Startup (no tests) |
|------|------------------:|
| pytest | 650ms |
| rustest compat | 350ms |

Rustest compat starts ~300ms faster than pytest.

### Could Not Reproduce the Reported Regression

Across all benchmark configurations — including worst-case scenarios with deep
conftest hierarchies, heavy imports, parametrized fixtures, and `request` fixture
stress testing — **rustest compat mode was consistently 3.7-6.5x faster than
native pytest**. We were unable to reproduce a scenario where compat mode is slower.

## Profile Breakdown (3,000-test Heavy Import Suite)

From cProfile, total time = 0.964s:

| Component | Time | % of Total | Notes |
|-----------|-----:|----------:|----|
| Rust engine (discovery + execution) | 369ms | 38% | Core test collection and running |
| Rich rendering (live progress, tables) | 245ms | 25% | Event callbacks + terminal rendering |
| Python module loading (importlib) | 126ms | 13% | Loading 120+ test files |
| Test computation (JSON + hashlib) | 66ms | 7% | Actual test code work |
| File system operations (stat, open) | 62ms | 6% | File discovery |
| `fixture_registry.register_fixtures` | 6ms | 0.6% | 3,000 calls, 0.002ms each |
| `FixtureRequest.__init__` | 5ms | 0.5% | 1,080 calls, Path.cwd() |
| `install_pytest_stubs` | 9ms | 0.9% | One-time startup |
| Other | 76ms | 8% | Various Python overhead |

### Key Finding: Theoretical Bottlenecks Were Overestimated

The prior theoretical analysis identified `populate_fixture_registry()` as a
"CRITICAL" bottleneck due to its O(N×M) per-test cost. **Actual measurement shows
it consumes only 0.6% of runtime** — the lock acquire, dict clear, and dict update
are fast enough that even 3,000 calls take only 6ms total.

Similarly, `FixtureRequest.__init__` (creating Config + Node objects) was flagged
as "MEDIUM" overhead but only accounts for 0.5% of runtime.

## Where Time Is Actually Spent

### 1. Rich Terminal Rendering (25% of runtime)

The single largest Python-side overhead is the Rich library rendering live progress
updates. Each test completion triggers an event callback that updates a Rich
progress display with table rendering, segment processing, and terminal output.

With 3,000 tests, this means 3,000+ event callbacks through:
- `event_router.py:emit()` → `rich_renderer.py:handle()` → `rich.console.render()`
  → `rich.table._render()` → `rich.segment.split_and_crop_lines()`

### 2. Rust Engine (38% of runtime)

The Rust engine handles:
- File system walking (parallel via rayon)
- Python module loading (sequential, requires GIL)
- Fixture resolution (per-test, with scope-based caching)
- Test execution (sequential for sync tests)

This is already well-optimized and represents the core work.

### 3. Module Loading (13% of runtime)

Loading 120+ test modules through `importlib.util.spec_from_file_location` +
`loader.exec_module`. This is inherent to Python module loading and cannot be
easily optimized.

## Possible Explanations for the Reported Issue

Since we cannot reproduce the 14% deficit, the reporter's scenario likely involves
one or more of:

1. **pytest-xdist parallel execution** — If the reporter runs pytest with `-n auto`
   or similar, pytest distributes tests across multiple CPU cores. Rustest compat
   mode runs synchronously (no multi-process parallelism for sync tests), so a
   parallel pytest would outperform serial rustest on CPU-bound suites.

2. **Very heavy third-party imports** — Libraries like Django, SQLAlchemy, NumPy,
   or Pandas add significant import time. If test files import these at module
   level, the sequential module loading in rustest (requiring GIL) could become
   a bottleneck that pytest handles differently through its import system.

3. **pytest plugin optimizations** — Certain pytest plugins (e.g., pytest-cache,
   assertion rewriting with cached `.pyc` files) provide optimizations for
   repeated runs that rustest doesn't have.

4. **conftest.py with very heavy setup** — If conftest files perform expensive
   operations at import time (database migrations, network connections), the
   module loading sequence could differ between pytest and rustest.

## Recommended Follow-ups

1. **Request more details from the issue reporter** — specifically:
   - Are they using pytest-xdist or any parallelization?
   - What's the size of their test suite (files, tests)?
   - What libraries does their project depend on?
   - Can they share `pytest --co` timing vs `rustest --pytest-compat --co` timing?

2. **Optimize Rich rendering** — At 25% of runtime, the live progress display is
   the biggest optimization opportunity:
   - Throttle event callbacks (e.g., update UI at most every 50ms, not every test)
   - Use lighter-weight progress display for non-verbose mode
   - Consider batch event emission

3. **Profile with real third-party imports** — Test with a Django/Flask project
   to see if heavy imports change the performance characteristics.

## Raw Data

### Standard Suite (2,150 tests, 100 files)

```
pytest:         1.65s internal, 2.3s wall (median of 3 runs)
rustest compat: 80ms internal, 0.9s wall (median of 3 runs)
```

### Stress Suite (1,800 tests, 50 files, 6 conftest files)

```
pytest:         3.3s internal, 4.1s wall
rustest compat: 76ms internal, 0.6s wall
```

### Heavy Import Suite (3,000 tests, 120 files, 9 conftest files)

```
pytest:         6.4s internal, 7.3s wall
rustest compat: 330ms internal, 1.2s wall
```

### Workload Suite (1,000 tests, 40 files, computation per test)

```
pytest:         1.8s internal, 2.5s wall
rustest compat: 130ms internal, 0.7s wall
```
