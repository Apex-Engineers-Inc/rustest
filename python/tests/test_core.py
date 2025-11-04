from __future__ import annotations

import unittest
from types import SimpleNamespace

from .helpers import stub_rust_module
from rustest import RunReport
from rustest.core import run as core_run


class CoreRunTests(unittest.TestCase):
    def test_run_delegates_to_rust_layer(self) -> None:
        dummy_result = SimpleNamespace(
            name="test_sample",
            path="tests/test_sample.py",
            status="passed",
            duration=0.05,
            message=None,
            stdout=None,
            stderr=None,
        )
        dummy_report = SimpleNamespace(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            duration=0.05,
            results=[dummy_result],
        )

        captured_args: dict[str, object] = {}

        def fake_run(paths, pattern, workers, capture_output):  # type: ignore[no-untyped-def]
            captured_args["paths"] = paths
            captured_args["pattern"] = pattern
            captured_args["workers"] = workers
            captured_args["capture_output"] = capture_output
            return dummy_report

        with stub_rust_module(run=fake_run):
            report = core_run(
                paths=["tests"],
                pattern="sample",
                workers=4,
                capture_output=False,
            )

        self.assertIsInstance(report, RunReport)
        self.assertEqual(captured_args["paths"], ["tests"])
        self.assertEqual(captured_args["pattern"], "sample")
        self.assertEqual(captured_args["workers"], 4)
        self.assertFalse(captured_args["capture_output"])
        self.assertEqual(report.total, 1)
        self.assertEqual(report.passed, 1)


if __name__ == "__main__":
    unittest.main()
