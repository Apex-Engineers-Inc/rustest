#!/usr/bin/env python3
"""Generate a detailed performance comparison report from recorded benchmarks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


def _load_results(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _format_seconds(value: float) -> str:
    return f"{value:.3f}s"


def _tests_per_second(test_count: int, duration: float) -> float:
    return test_count / duration if duration else 0.0


def generate_markdown_table(results_path: Path | None = None) -> str:
    """Generate markdown table comparing pytest and rustest."""

    path = results_path or Path("benchmark_results.json")
    data = _load_results(path)
    suites: List[Dict[str, object]] = data["suites"]
    summary: Dict[str, float] = data["summary"]
    config: Dict[str, object] = data.get("config", {})

    lines: List[str] = []
    lines.append("## Performance Comparison")
    lines.append("")
    counts: List[int] = list(config.get("test_counts", [suite["test_count"] for suite in suites]))
    lines.append(
        "We benchmarked pytest and rustest on synthetically generated suites ranging from "
        f"{min(counts):,} to {max(counts):,} tests. "
        "Each entry in the table reflects the mean runtime across multiple runs."
    )
    lines.append("")
    lines.append(
        "| Test Count | pytest (mean) | rustest (mean) | Speedup | pytest tests/s | rustest tests/s |"
    )
    lines.append("|-----------:|--------------:|---------------:|--------:|----------------:|-----------------:|")

    for suite in suites:
        test_count = suite["test_count"]
        pytest_mean = suite["pytest"]["mean"]
        rustest_mean = suite["rustest"]["mean"]
        speedup = suite["speedup"]
        lines.append(
            "| {count:>10,} | {pytest:>12} | {rustest:>13} | {speedup:>7.2f}x | {pytest_tps:>15.1f} | {rustest_tps:>16.1f} |".format(
                count=test_count,
                pytest=_format_seconds(pytest_mean),
                rustest=_format_seconds(rustest_mean),
                speedup=speedup,
                pytest_tps=_tests_per_second(test_count, pytest_mean),
                rustest_tps=_tests_per_second(test_count, rustest_mean),
            )
        )

    lines.append("")
    lines.append("### Aggregate results")
    lines.append("")
    lines.append(
        "- **Average speedup:** {avg:.2f}×\n- **Geometric mean speedup:** {geo:.2f}×\n- **Weighted by tests:** {weighted:.2f}×".format(
            avg=summary["average_speedup"],
            geo=summary["geometric_mean_speedup"],
            weighted=summary["weighted_speedup"],
        )
    )
    lines.append("")
    lines.append(
        "Across the entire benchmark matrix pytest required {py:.2f}s total execution time, "
        "while rustest completed in {rs:.2f}s.".format(
            py=summary["total_time_pytest"], rs=summary["total_time_rustest"],
        )
    )
    lines.append("")
    lines.append("### Reproducing the benchmarks")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 profile_tests.py --runs {runs}".format(runs=config.get("runs_per_command", 5)))
    lines.append("python3 generate_comparison.py")
    lines.append("```")
    lines.append("")
    lines.append("`profile_tests.py` generates synthetic suites in `target/generated_benchmarks/` and records the results in `benchmark_results.json`. `generate_comparison.py` then renders the Markdown summary in `BENCHMARKS.md`.")

    return "\n".join(lines)


if __name__ == "__main__":
    report = generate_markdown_table()
    print(report)
    with open("BENCHMARKS.md", "w") as f:
        f.write(report.strip() + "\n")
    print("\n\nBenchmark report saved to BENCHMARKS.md")
