"""``python -m conformance --survey NAME|all``: pytest beside rustest on public suites.

Three questions, one table each:

1. **Agreement.** Does unmodified rustest report what pytest reports, test by test?
2. **Speed, like for like.** One worker against serial pytest; four against ``pytest -n 4``.
3. **Everything on.** One worker per CPU, warm cache, against ``pytest -n <cpus>``.

Every leg runs ``--survey-rounds`` times and the legs are rotated each round, so machine
drift lands on every leg rather than on the last one. A round's rustest legs are compared
with *that round's* serial pytest. A test whose status changes between pytest's own runs is
counted as the suite's flake, not as a disagreement.

Nothing here reads a ledger: the numbers are the raw diff. Private targets are refused.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import sys
from collections.abc import Callable
from typing import Any, Final

from . import real

SURVEY_DIR: Final = real.REAL_DIR / "survey"
CPUS: Final = os.cpu_count() or 4
#: (label, runner, workers). ``0`` workers is serial pytest.
LEGS: Final = [
    ("pytest", "pytest", 0),
    ("rustest -n 1", "rustest", 1),
    (f"pytest -n {real.WORKERS}", "pytest", real.WORKERS),
    (f"rustest -n {real.WORKERS}", "rustest", real.WORKERS),
    (f"pytest -n {CPUS}", "pytest", CPUS),
    (f"rustest -n {CPUS}", "rustest", CPUS),
]


def _diff(left: dict[str, str], right: dict[str, str]) -> list[str]:
    """Ids present on one side only, or with different statuses."""
    return sorted(i for i in left.keys() | right.keys() if left.get(i) != right.get(i))


def survey_target(name: str, rounds: int, *, tree: bool) -> dict[str, Any]:
    target = real.load_target(name)
    if target.private:
        raise ValueError(f"{name}: private targets are not surveyed")
    real.ensure_repo(target)
    real.ensure_env(target)
    if tree:
        real._verify_tree(target)  # pyright: ignore[reportPrivateUsage]
    else:
        real.verify_env(target)
        real.assert_build_is_current(target)
    real.ensure_xdist(target)
    before = real._git_status(target)  # pyright: ignore[reportPrivateUsage]
    tmp = real.WORK_DIR / "_runs" / f"survey-{name}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    def leg(runner: str, workers: int) -> real.RunOutcome:
        if runner == "pytest":
            return real.run_pytest_real(target, tmp, workers=workers or None)
        return real.run_rustest_real(target, tmp, tree=tree, workers=workers)

    seconds: dict[str, list[float]] = {label: [] for label, _, _ in LEGS}
    warm: dict[str, list[bool | None]] = {label: [] for label, _, _ in LEGS}
    errors: dict[str, str] = {}
    runs: list[dict[str, real.RunOutcome]] = []
    for index in range(rounds):
        this: dict[str, real.RunOutcome] = {}
        for label, runner, workers in LEGS[index % len(LEGS) :] + LEGS[: index % len(LEGS)]:
            try:
                run = leg(runner, workers)
            except Exception as exc:  # noqa: BLE001 - one leg's fault must not lose the rest
                errors[label] = repr(exc)[:300]
                continue
            this[label] = run
            seconds[label].append(round(run.seconds, 2))
            warm[label].append(run.duration_record)
        runs.append(this)

    if real._git_status(target) != before:  # pyright: ignore[reportPrivateUsage]
        errors["tree"] = "the run changed tracked files in the suite's clone"
    oracles = [r["pytest"] for r in runs if "pytest" in r]
    # The suite's own nondeterminism: any id pytest itself did not report identically in
    # every one of its legs, in every round.
    pytest_runs = [run for r in runs for label, run in r.items() if label.startswith("pytest")]
    flaky = {i for other in pytest_runs[1:] for i in _diff(pytest_runs[0].statuses, other.statuses)}
    agreement: dict[str, Any] = {}
    for label, runner, _ in LEGS:
        if runner != "rustest":
            continue
        differing: set[str] = set()
        fallback: set[str] = set()
        for r in runs:
            if label in r and "pytest" in r:
                differing.update(_diff(r["pytest"].statuses, r[label].statuses))
                fallback.update(r[label].fallback)
        agreement[label] = {
            "differing": sorted(differing - flaky),
            "differing_on_flaky_tests": sorted(differing & flaky),
            "handed_to_pytest": sorted(fallback),
        }
    return {
        "suite": name,
        "url": target.url,
        "tag": target.tag,
        "rev": target.rev,
        "cpus": CPUS,
        "rounds": rounds,
        "tests": len(oracles[0].statuses) if oracles else None,
        "pytest_tally": oracles[0].tally() if oracles else None,
        "pytest_exit": oracles[0].exit_code if oracles else None,
        "flaky_under_pytest": sorted(flaky),
        "seconds": seconds,
        "rustest_warm": warm,
        "agreement": agreement,
        "errors": errors,
    }


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def render(rows: list[dict[str, Any]]) -> str:
    """The three tables, from the recorded JSON. Times are medians over the rounds."""

    def table(head: list[str], cells: Callable[[dict[str, Any]], list[object]]) -> str:
        lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        lines += ["| " + " | ".join(str(c) for c in cells(row)) + " |" for row in rows]
        return "\n".join(lines)

    def ratio(row: dict[str, Any], base: str, leg: str) -> str:
        a, b = _median(row["seconds"][base]), _median(row["seconds"][leg])
        return f"{a / b:.2f}x" if a and b else ""

    def agree(row: dict[str, Any]) -> list[object]:
        a = row["agreement"][f"rustest -n {real.WORKERS}"]
        tests = row["tests"] or 0
        same = tests - len(a["differing"]) - len(a["differing_on_flaky_tests"])
        return [
            row["suite"],
            row["tag"],
            tests,
            same,
            len(a["differing"]),
            len(row["flaky_under_pytest"]),
            "handed to pytest" if a["handed_to_pytest"] else "native",
        ]

    def speed(a: str, b: str) -> Callable[[dict[str, Any]], list[object]]:
        return lambda row: [
            row["suite"],
            _median(row["seconds"][a]),
            _median(row["seconds"][b]),
            ratio(row, a, b),
        ]

    n, top = real.WORKERS, rows[0]["cpus"] if rows else CPUS
    return (
        "\n\n".join(
            [
                "## 1. Agreement (rustest -n 4 against serial pytest, no ledger)",
                table(
                    ["suite", "release", "tests", "same", "differ", "flaky under pytest", "ran as"],
                    agree,
                ),
                "## 2. Speed, like for like (median seconds)",
                "### One worker",
                table(
                    ["suite", "pytest", "rustest -n 1", "speedup"], speed("pytest", "rustest -n 1")
                ),
                f"### {n} workers",
                table(
                    ["suite", f"pytest -n {n}", f"rustest -n {n}", "speedup"],
                    speed(f"pytest -n {n}", f"rustest -n {n}"),
                ),
                f"## 3. Everything on ({top} workers, warm cache)",
                table(
                    [
                        "suite",
                        "pytest",
                        f"pytest -n {top}",
                        f"rustest -n {top}",
                        "vs serial",
                        "vs xdist",
                    ],
                    lambda row: [
                        row["suite"],
                        _median(row["seconds"]["pytest"]),
                        _median(row["seconds"][f"pytest -n {top}"]),
                        _median(row["seconds"][f"rustest -n {top}"]),
                        ratio(row, "pytest", f"rustest -n {top}"),
                        ratio(row, f"pytest -n {top}", f"rustest -n {top}"),
                    ],
                ),
            ]
        )
        + "\n"
    )


def main_survey(selection: str, rounds: int, *, tree: bool, resume: bool) -> int:
    SURVEY_DIR.mkdir(exist_ok=True)
    public = sorted(p.stem for p in real.REAL_DIR.glob("*.toml"))
    names = public if selection == "all" else [selection]
    if selection != "all" and selection not in public:
        print(f"conformance: no such public --survey target {selection!r}", file=sys.stderr)
        return 1
    for name in names:
        path = SURVEY_DIR / f"{name}.json"
        if resume and path.exists():
            print(f"[--] {name}: already surveyed, skipped")
            continue
        try:
            row = survey_target(name, rounds, tree=tree)
        except Exception as exc:  # noqa: BLE001 - one suite's fault must not abort the survey
            print(f"[EE] {name}: {exc!r}", flush=True)
            continue
        _ = path.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
        print(f"[ok] {name}: {row['tests']} tests, {row['seconds']}", flush=True)
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SURVEY_DIR.glob("*.json"))]
    _ = (real.REAL_DIR / "survey.md").write_text(render(rows), encoding="utf-8")
    return 0
