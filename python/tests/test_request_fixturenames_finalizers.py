"""End-to-end tests for ``request.fixturenames`` and ``request.addfinalizer``.

Each test writes a small test module to a temporary directory, runs it through
the public ``rustest.run`` API and checks the resulting report.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from rustest import run


def _read_log(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


def _run_module(tmp_path: Path, source: str):
    module = tmp_path / "test_generated.py"
    module.write_text(textwrap.dedent(source))
    return run(paths=[str(module)], capture_output=False)


class TestFixtureNames:
    def test_fixturenames_contains_fixture_requested_by_test(self, tmp_path: Path) -> None:
        report = _run_module(
            tmp_path,
            """
            from rustest import fixture

            @fixture
            def other():
                return 1

            @fixture
            def probe(request):
                return list(request.fixturenames)

            def test_sees_other(probe, other):
                assert "other" in probe
                assert "probe" in probe
            """,
        )

        assert report.passed == 1, [r.message for r in report.results]
        assert report.failed == 0


class TestAddFinalizer:
    def test_finalizer_runs_after_the_test(self, tmp_path: Path) -> None:
        log = tmp_path / "log.txt"
        report = _run_module(
            tmp_path,
            f"""
            from rustest import fixture

            LOG = {str(log)!r}

            def note(text):
                with open(LOG, "a") as handle:
                    handle.write(text + "\\n")

            @fixture
            def resource(request):
                note("setup")
                request.addfinalizer(lambda: note("finalized"))
                return 1

            def test_uses_resource(resource):
                note("test")
            """,
        )

        assert report.passed == 1, [r.message for r in report.results]
        assert _read_log(log) == ["setup", "test", "finalized"]

    def test_finalizers_run_in_reverse_registration_order(self, tmp_path: Path) -> None:
        log = tmp_path / "log.txt"
        report = _run_module(
            tmp_path,
            f"""
            from rustest import fixture

            LOG = {str(log)!r}

            def note(text):
                with open(LOG, "a") as handle:
                    handle.write(text + "\\n")

            @fixture
            def resource(request):
                request.addfinalizer(lambda: note("first"))
                request.addfinalizer(lambda: note("second"))
                request.addfinalizer(lambda: note("third"))
                return 1

            def test_uses_resource(resource):
                note("test")
            """,
        )

        assert report.passed == 1, [r.message for r in report.results]
        assert _read_log(log) == ["test", "third", "second", "first"]

    def test_module_scoped_finalizer_runs_once_at_scope_end(self, tmp_path: Path) -> None:
        log = tmp_path / "log.txt"
        report = _run_module(
            tmp_path,
            f"""
            from rustest import fixture

            LOG = {str(log)!r}

            def note(text):
                with open(LOG, "a") as handle:
                    handle.write(text + "\\n")

            @fixture(scope="module")
            def shared(request):
                request.addfinalizer(lambda: note("finalized"))
                return 1

            def test_one(shared):
                note("one")

            def test_two(shared):
                note("two")
            """,
        )

        assert report.passed == 2, [r.message for r in report.results]
        assert _read_log(log) == ["one", "two", "finalized"]

    def test_failing_finalizer_is_reported_and_others_still_run(self, tmp_path: Path) -> None:
        log = tmp_path / "log.txt"
        report = _run_module(
            tmp_path,
            f"""
            from rustest import fixture

            LOG = {str(log)!r}

            def note(text):
                with open(LOG, "a") as handle:
                    handle.write(text + "\\n")

            def explode():
                raise ValueError("finalizer blew up")

            @fixture
            def resource(request):
                request.addfinalizer(lambda: note("first"))
                request.addfinalizer(explode)
                request.addfinalizer(lambda: note("third"))
                return 1

            def test_uses_resource(resource):
                note("test")
            """,
        )

        assert report.failed == 1
        assert "finalizer blew up" in report.results[0].message
        assert _read_log(log) == ["test", "third", "first"]
