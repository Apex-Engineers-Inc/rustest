"""Conformance CLI: python -m conformance [--only PREFIX]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .harness.grade import CaseResult, grade_case, load_case_args, load_waivers
from .harness.runners import run_pytest, run_rustest

ROOT = Path(__file__).parent


def main() -> int:
    parser = argparse.ArgumentParser(prog="conformance")
    parser.add_argument("--only", default="", help="Only run cases whose name starts with PREFIX")
    args = parser.parse_args()

    waivers = load_waivers(ROOT / "waivers.toml")
    corpus = ROOT / "corpus"
    cases = sorted(
        d for d in corpus.glob("*/*/") if any(d.glob("test_*.py")) or (d / "case.toml").exists()
    )
    results: list[CaseResult] = []
    for case_dir in cases:
        name = f"{case_dir.parent.name}/{case_dir.name}"
        if not name.startswith(args.only):
            continue
        case_args = load_case_args(case_dir)
        result = grade_case(
            name, run_pytest(case_dir, case_args), run_rustest(case_dir, case_args), waivers
        )
        results.append(result)
        flag = {"MATCH": "ok", "WAIVED": "~~", "DIVERGE": "XX"}[result.status]
        print(f"[{flag}] {result.name}" + (f"  ({result.detail})" if result.detail else ""))

    diverged = [r for r in results if r.status == "DIVERGE"]
    matched = sum(r.status == "MATCH" for r in results)
    waived = sum(r.status == "WAIVED" for r in results)
    summary = f"{len(results)} cases: {matched} match, {waived} waived, {len(diverged)} diverged"
    print(f"\n{summary}")
    return 1 if diverged else 0


if __name__ == "__main__":
    sys.exit(main())
