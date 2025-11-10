## Performance Comparison

We benchmarked pytest and rustest on synthetically generated suites ranging from 1 to 5,000 tests. Each entry in the table reflects the mean runtime across multiple runs.

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

### Aggregate results

- **Average speedup:** 8.53×
- **Geometric mean speedup:** 7.03×
- **Weighted by tests:** 16.22×

**Interpreting the numbers:** suites with **≤20 tests** still finish **~3–4× faster**, those around **100–500 tests** jump to **~5–8× faster**, and big **1k+ test** runs see **~11–19× speedups** as rustest's Rust core dominates the work.

Across the entire benchmark matrix pytest required 16.18s total execution time, while rustest completed in 1.45s.

### Reproducing the benchmarks

```bash
python3 profile_tests.py --runs 5
python3 generate_comparison.py
```

`profile_tests.py` generates synthetic suites in `target/generated_benchmarks/` and records the results in `benchmark_results.json`. `generate_comparison.py` then renders the Markdown summary in `BENCHMARKS.md`.
