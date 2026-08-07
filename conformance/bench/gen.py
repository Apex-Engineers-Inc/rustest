"""Generate synthetic trivial test suites for benchmarking framework overhead."""

from __future__ import annotations

from pathlib import Path


def generate_suite(root: Path, files: int, tests_per_file: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for file_index in range(files):
        lines: list[str] = []
        for test_index in range(tests_per_file):
            lines.append(f"def test_case_{test_index}():")
            lines.append(f"    assert {test_index} + 1 == {test_index + 1}")
            lines.append("")
        (root / f"test_gen_{file_index:04d}.py").write_text("\n".join(lines), encoding="utf-8")
