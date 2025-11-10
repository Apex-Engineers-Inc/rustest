#!/usr/bin/env python3
"""Profile script to compare pytest and rustest performance across suites."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List


DEFAULT_TEST_COUNTS = (1, 5, 20, 100, 500, 1000, 2000, 5000)
DEFAULT_RUNS = 5
DEFAULT_CHUNK_SIZE = 250


@dataclass
class CommandResult:
    """Timing result for a single command."""

    command: List[str]
    runs: List[float]

    @property
    def mean(self) -> float:
        return statistics.mean(self.runs)

    @property
    def median(self) -> float:
        return statistics.median(self.runs)

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.runs) if len(self.runs) > 1 else 0.0

    @property
    def minimum(self) -> float:
        return min(self.runs)

    @property
    def maximum(self) -> float:
        return max(self.runs)

    def as_dict(self) -> Dict[str, float | List[float]]:
        return {
            "mean": self.mean,
            "median": self.median,
            "stdev": self.stdev,
            "min": self.minimum,
            "max": self.maximum,
            "runs": list(self.runs),
        }


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def generated_root() -> Path:
    root = repo_root() / "target" / "generated_benchmarks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def add_basic_test(lines: List[str], test_index: int) -> int:
    lines.extend(
        [
            f"def test_basic_case_{test_index}(module_offset):",
            f"    value = module_offset + {test_index}",
            f"    assert value - module_offset == {test_index}",
            "",
        ]
    )
    return 1


def add_fixture_test(lines: List[str], test_index: int) -> int:
    lines.extend(
        [
            f"def test_with_fixture_{test_index}(module_offset, combine, sample_data):",
            f"    combined = combine({test_index})",
            f"    assert combined - module_offset == {test_index}",
            "    assert sample_data['offset'] == module_offset",
            "",
        ]
    )
    return 1


def add_parametrized_test(
    lines: List[str],
    test_index: int,
    remaining: int,
) -> int:
    param_count = min(3, max(2, remaining)) if remaining >= 2 else 1
    params = []
    for offset in range(param_count):
        base = test_index * 5 + offset
        params.append(f"    ({base}, {base + 1}),")

    lines.append("@parametrize(\"value, expected\", [")
    lines.extend(params)
    lines.append("])")
    lines.extend(
        [
            f"def test_parametrized_{test_index}(module_offset, combine, value, expected):",
            "    result = combine(value)",
            "    assert result - module_offset == value",
            "    assert expected == value + 1",
            "",
        ]
    )
    return param_count


def add_class_tests(lines: List[str], class_index: int, remaining: int) -> int:
    method_count = min(3, max(2, remaining)) if remaining >= 2 else 1
    lines.extend(
        [
            f"class TestGeneratedClass{class_index}:",
        ]
    )
    for method in range(method_count):
        lines.extend(
            [
                f"    def test_method_{class_index}_{method}(self, class_shared, combine, module_offset):",
                f"        result = combine({class_index} + {method})",
                "        class_shared['values'].append(result)",
                "        assert class_shared['offset'] == module_offset",
                "        assert result >= module_offset",
                "",
            ]
        )
    lines.append("")
    return method_count


def module_preamble(module_index: int, start_index: int) -> List[str]:
    return [
        '"""Auto-generated benchmarks for pytest/rustest comparison."""',
        "",
        "try:",
        "    from rustest import fixture as rustest_fixture, parametrize as rustest_parametrize",
        "except ImportError:",
        "    import pytest",
        "    fixture = pytest.fixture",
        "    parametrize = pytest.mark.parametrize",
        "else:",
        "    import sys",
        "    if '_pytest' in sys.modules:",
        "        import pytest",
        "        fixture = pytest.fixture",
        "        parametrize = pytest.mark.parametrize",
        "    else:",
        "        fixture = rustest_fixture",
        "        parametrize = rustest_parametrize",
        "",
        f"MODULE_INDEX = {module_index}",
        f"MODULE_OFFSET = {start_index}",
        "",
        "@fixture(scope=\"module\")",
        "def module_offset():",
        "    return MODULE_OFFSET",
        "",
        "@fixture",
        "def sample_data(module_offset):",
        "    return {\"offset\": module_offset, \"double\": module_offset * 2}",
        "",
        "@fixture",
        "def combine(module_offset):",
        "    def _combine(value: int) -> int:",
        "        return module_offset + value",
        "    return _combine",
        "",
        "@fixture(scope=\"class\")",
        "def class_shared(module_offset):",
        "    return {\"offset\": module_offset, \"values\": []}",
        "",
    ]


def create_test_suite(test_count: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Path:
    """Generate a synthetic test suite with the requested number of tests."""

    suite_dir = generated_root() / f"suite_{test_count}"
    if suite_dir.exists():
        import shutil

        shutil.rmtree(suite_dir)

    suite_dir.mkdir(parents=True, exist_ok=True)

    total_created = 0
    module_index = 0
    while total_created < test_count:
        module_target = min(chunk_size, test_count - total_created)
        module_path = suite_dir / f"test_generated_{module_index:03d}.py"
        lines = module_preamble(module_index, total_created)

        tests_emitted = 0
        feature_cycle = 0
        while tests_emitted < module_target:
            remaining = module_target - tests_emitted
            global_index = total_created + tests_emitted
            pattern = feature_cycle % 4

            if pattern == 0:
                produced = add_basic_test(lines, global_index)
            elif pattern == 1:
                produced = add_fixture_test(lines, global_index)
            elif pattern == 2:
                produced = add_parametrized_test(lines, global_index, remaining)
            elif pattern == 3:
                produced = add_class_tests(lines, global_index, remaining)
            else:
                produced = add_basic_test(lines, global_index)

            tests_emitted += produced
            feature_cycle += 1

        module_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        total_created += tests_emitted
        module_index += 1

    assert total_created == test_count, f"Expected {test_count}, created {total_created}"
    return suite_dir


def run_command(command: List[str], *, cwd: Path, runs: int, env: Dict[str, str] | None = None) -> CommandResult:
    """Run a command multiple times and collect precise timing statistics."""

    durations: List[float] = []
    for iteration in range(1, runs + 1):
        start = time.perf_counter()
        result = subprocess.run(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        end = time.perf_counter()
        elapsed = end - start
        durations.append(elapsed)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            stdout = result.stdout.decode("utf-8", errors="replace")
            raise RuntimeError(
                (
                    f"Command {' '.join(command)} failed on run {iteration} with code {result.returncode}:\n"
                    f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                )
            )
        print(f"    Run {iteration}/{runs}: {elapsed:.4f}s")

    return CommandResult(command=command, runs=durations)


def benchmark_suite(
    test_count: int,
    *,
    runs: int,
    chunk_size: int,
    python_executable: str,
) -> Dict[str, object]:
    suite_dir = create_test_suite(test_count, chunk_size=chunk_size)
    repo = repo_root()
    relative_suite = suite_dir.relative_to(repo)

    base_env = os.environ.copy()
    pythonpath_entries = [str(repo / "python")]
    if base_env.get("PYTHONPATH"):
        pythonpath_entries.append(base_env["PYTHONPATH"])
    base_env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    pytest_env = base_env.copy()
    pytest_env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")

    pytest_command = [python_executable, "-m", "pytest", str(relative_suite)]
    rustest_command = [python_executable, "-m", "rustest", str(relative_suite)]

    print(f"  pytest ({test_count} tests)")
    pytest_result = run_command(pytest_command, cwd=repo, runs=runs, env=pytest_env)

    print(f"  rustest ({test_count} tests)")
    rustest_result = run_command(rustest_command, cwd=repo, runs=runs, env=base_env.copy())

    speedup = pytest_result.mean / rustest_result.mean
    pytest_speed = test_count / pytest_result.mean
    rustest_speed = test_count / rustest_result.mean

    return {
        "test_count": test_count,
        "pytest": pytest_result.as_dict(),
        "rustest": rustest_result.as_dict(),
        "speedup": speedup,
        "pytest_tests_per_second": pytest_speed,
        "rustest_tests_per_second": rustest_speed,
    }


def compute_overall_summary(suites: List[Dict[str, object]]) -> Dict[str, float]:
    speedups = [suite["speedup"] for suite in suites]
    weighted_numerator = sum(suite["test_count"] * suite["speedup"] for suite in suites)
    weighted_denominator = sum(suite["test_count"] for suite in suites)
    pytest_total = sum(suite["pytest"]["mean"] for suite in suites)
    rustest_total = sum(suite["rustest"]["mean"] for suite in suites)

    return {
        "average_speedup": statistics.mean(speedups),
        "geometric_mean_speedup": math.prod(speedups) ** (1 / len(speedups)),
        "weighted_speedup": weighted_numerator / weighted_denominator,
        "total_time_pytest": pytest_total,
        "total_time_rustest": rustest_total,
    }


def parse_test_counts(raw: Iterable[str] | None) -> List[int]:
    if not raw:
        return list(DEFAULT_TEST_COUNTS)
    counts: List[int] = []
    for value in raw:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            counts.append(int(part))
    return counts


def main(argv: Iterable[str] | None = None) -> Dict[str, object]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tests",
        nargs="*",
        help=(
            "List of test counts to benchmark. Can be provided as space-separated values "
            "or comma-separated groups. Defaults to the recommended suite."
        ),
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Number of runs per framework")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Maximum number of tests to emit per generated module",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root() / "benchmark_results.json",
        help="Where to write the benchmark results JSON",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to use for invoking pytest and rustest",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    test_counts = parse_test_counts(args.tests)
    test_counts = sorted(set(test_counts))

    print("=" * 70)
    print("Performance Comparison: pytest vs rustest")
    print("=" * 70)
    print(f"Test counts: {', '.join(str(c) for c in test_counts)}")
    print(f"Runs per command: {args.runs}")
    print()

    suites: List[Dict[str, object]] = []
    for test_count in test_counts:
        print("-" * 70)
        print(f"Benchmarking suite with {test_count} tests")
        suite_result = benchmark_suite(
            test_count,
            runs=args.runs,
            chunk_size=args.chunk_size,
            python_executable=args.python,
        )
        suites.append(suite_result)
        print(
            f"    Speedup: {suite_result['speedup']:.2f}x | "
            f"pytest {suite_result['pytest_tests_per_second']:.1f} tests/s | "
            f"rustest {suite_result['rustest_tests_per_second']:.1f} tests/s"
        )
        print()

    summary = compute_overall_summary(suites)

    results = {
        "config": {
            "test_counts": test_counts,
            "runs_per_command": args.runs,
            "chunk_size": args.chunk_size,
            "python_executable": args.python,
        },
        "suites": suites,
        "summary": summary,
        "timestamp": time.time(),
    }

    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("=" * 70)
    print("Overall summary")
    print("=" * 70)
    print(
        f"Average speedup: {summary['average_speedup']:.2f}x | "
        f"Geometric mean: {summary['geometric_mean_speedup']:.2f}x | "
        f"Weighted by tests: {summary['weighted_speedup']:.2f}x"
    )
    print(
        f"Total runtime (pytest): {summary['total_time_pytest']:.2f}s | "
        f"Total runtime (rustest): {summary['total_time_rustest']:.2f}s"
    )
    print(f"Detailed results saved to {args.output}")

    return results


if __name__ == "__main__":
    main()
