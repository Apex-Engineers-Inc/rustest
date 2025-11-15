## Performance Comparison

We benchmarked pytest and rustest on synthetically generated suites ranging from 1 to 5,000 tests. Each entry in the table reflects the mean runtime across multiple runs.

| Test Count | pytest (mean) | rustest (mean) | Speedup | pytest tests/s | rustest tests/s |
|-----------:|--------------:|---------------:|--------:|----------------:|-----------------:|
|          1 |       0.601s |        0.167s |    3.60x |             1.7 |              6.0 |
|          5 |       0.621s |        0.159s |    3.91x |             8.0 |             31.5 |
|         20 |       0.680s |        0.204s |    3.34x |            29.4 |             98.3 |
|        100 |       0.836s |        0.210s |    3.99x |           119.6 |            476.8 |
|        500 |       1.799s |        0.288s |    6.25x |           277.9 |           1737.2 |
|      1,000 |       3.306s |        0.384s |    8.61x |           302.5 |           2605.9 |
|      2,000 |       5.261s |        0.564s |    9.33x |           380.1 |           3546.2 |
|      5,000 |      13.281s |        1.127s |   11.79x |           376.5 |           4437.0 |

### Aggregate results

- **Average speedup:** 6.35×
- **Geometric mean speedup:** 5.70×
- **Weighted by tests:** 10.41×

Across the entire benchmark matrix pytest required 26.39s total execution time, while rustest completed in 3.10s.

### Reproducing the benchmarks

```bash
python3 profile_tests.py --runs 5
python3 generate_comparison.py
```

`profile_tests.py` generates synthetic suites in `target/generated_benchmarks/` and records the results in `benchmark_results.json`. `generate_comparison.py` then renders the Markdown summary in `BENCHMARKS.md`.
