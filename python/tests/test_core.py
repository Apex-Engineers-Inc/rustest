from __future__ import annotations

from types import SimpleNamespace

from .helpers import stub_rust_module
from rustest import RunReport
from rustest.core import run as core_run


class TestCoreRun:
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
            collection_errors=[],
        )

        captured_args: dict[str, object] = {}

        def fake_run(
            paths,
            pattern,
            mark_expr,
            workers,
            capture_output,
            enable_codeblocks,
            last_failed_mode,
            fail_fast,
            pytest_compat,
            verbose,
            ascii,
            no_color,
            event_callback,
        ):  # type: ignore[no-untyped-def]
            captured_args["paths"] = paths
            captured_args["pattern"] = pattern
            captured_args["mark_expr"] = mark_expr
            captured_args["workers"] = workers
            captured_args["capture_output"] = capture_output
            captured_args["enable_codeblocks"] = enable_codeblocks
            captured_args["last_failed_mode"] = last_failed_mode
            captured_args["fail_fast"] = fail_fast
            captured_args["pytest_compat"] = pytest_compat
            captured_args["verbose"] = verbose
            captured_args["ascii"] = ascii
            captured_args["no_color"] = no_color
            captured_args["event_callback"] = event_callback
            return dummy_report

        with stub_rust_module(run=fake_run):
            report = core_run(
                paths=["tests"],
                pattern="sample",
                workers=4,
                capture_output=False,
            )

        assert isinstance(report, RunReport)
        assert captured_args["paths"] == ["tests"]
        assert captured_args["pattern"] == "sample"
        assert captured_args["mark_expr"] is None
        assert captured_args["workers"] == 4
        assert captured_args["capture_output"] is False
        assert captured_args["enable_codeblocks"] is True
        assert captured_args["last_failed_mode"] == "none"
        assert captured_args["fail_fast"] is False
        assert captured_args["pytest_compat"] is False
        assert captured_args["verbose"] is False
        assert captured_args["ascii"] is False
        assert captured_args["no_color"] is False
        assert captured_args["event_callback"] is not None  # EventRouter.emit callback
        assert callable(captured_args["event_callback"])
        assert report.total == 1
        assert report.passed == 1


class TestPoolSize:
    """The default worker count, which Phase 4b Task 2 changed from ``os.cpu_count()``.

    Guarded as behaviour rather than left to the constant, because the number is the output
    of a measurement (fifteen ``-n`` curves; see :data:`rustest.core._DEFAULT_POOL`) and the
    thing that would silently undo it is somebody restoring ``os.cpu_count()`` because it
    reads like the obvious default.
    """

    def test_the_default_is_the_measured_cap_not_one_per_cpu(self, monkeypatch: object) -> None:
        from rustest import core

        # A machine with far more CPUs than the cap: the cap must win, or the old
        # anti-scaling is back (measured at +153% over the optimum at cpu_count=16).
        monkeypatch.setattr(core.os, "cpu_count", lambda: 64)  # pyright: ignore[reportAttributeAccessIssue]
        assert core._pool_size(None) == core._DEFAULT_POOL

    def test_a_small_machine_is_still_capped_by_its_cpu_count(self, monkeypatch: object) -> None:
        """A 2-core container must not start four interpreters for its own sake."""
        from rustest import core

        monkeypatch.setattr(core.os, "cpu_count", lambda: 2)  # pyright: ignore[reportAttributeAccessIssue]
        assert core._pool_size(None) == 2

    def test_an_unknowable_cpu_count_falls_back_to_one(self, monkeypatch: object) -> None:
        from rustest import core

        monkeypatch.setattr(core.os, "cpu_count", lambda: None)  # pyright: ignore[reportAttributeAccessIssue]
        assert core._pool_size(None) == 1

    def test_dash_n_overrides_the_default_in_both_directions(self, monkeypatch: object) -> None:
        """``-n`` is the escape hatch the default's honesty depends on, so it must be total.

        Both directions matter: a user asking for *more* than the cap on a big machine is
        the case the cap is knowingly wrong for, and a user asking for fewer is the ``-n 1``
        pin two corpus targets rely on for correctness.
        """
        from rustest import core

        monkeypatch.setattr(core.os, "cpu_count", lambda: 16)  # pyright: ignore[reportAttributeAccessIssue]
        assert core._pool_size(32) == 32
        assert core._pool_size(1) == 1
        # Non-positive is "not specified", which is what the CLI's absent flag becomes.
        assert core._pool_size(0) == core._DEFAULT_POOL
