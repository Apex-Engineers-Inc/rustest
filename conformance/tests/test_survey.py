"""The survey's arithmetic: what counts as a difference, and that the tables render."""

from __future__ import annotations

from conformance.harness import survey


def test_diff_counts_missing_extra_and_changed_ids() -> None:
    left = {"a": "passed", "b": "failed", "c": "passed"}
    right = {"a": "passed", "b": "passed", "d": "passed"}
    assert survey._diff(left, right) == ["b", "c", "d"]  # pyright: ignore[reportPrivateUsage]


def test_render_reports_agreement_and_ratios() -> None:
    seconds = {label: [10.0, 12.0, 11.0] for label, _, _ in survey.LEGS}
    seconds["rustest -n 1"] = [5.5, 5.5, 5.5]
    row = {
        "suite": "demo",
        "tag": "1.0",
        "cpus": survey.CPUS,
        "tests": 100,
        "flaky_under_pytest": ["x"],
        "seconds": seconds,
        "agreement": {
            label: {"differing": ["y"], "differing_on_flaky_tests": ["x"], "handed_to_pytest": []}
            for label, runner, _ in survey.LEGS
            if runner == "rustest"
        },
    }
    text = survey.render([row])
    assert "| demo | 1.0 | 100 | 98 | 1 | 1 | native |" in text
    assert "| demo | 11.0 | 5.5 | 2.00x |" in text
