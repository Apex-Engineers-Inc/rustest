"""Class-level and module-level marks must be visible on each test method."""

from __future__ import annotations

from pathlib import Path

from .helpers import ensure_rust_stub
from rustest import run

ensure_rust_stub()


def _run(tmp_path: Path, source: str, mark_expr: str | None = None):  # type: ignore[no-untyped-def]
    (tmp_path / "test_propagation.py").write_text(source)
    return run(paths=[str(tmp_path)], mark_expr=mark_expr)


def test_class_mark_is_visible_to_method_via_get_closest_marker(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        """
from rustest import fixture, mark


@fixture
def probe(request):
    return request.node.get_closest_marker("django_db")


@mark.django_db(transaction=True)
class TestFoo:
    def test_sees_mark(self, probe):
        assert probe is not None
        assert probe.kwargs == {"transaction": True}
""",
    )

    assert report.passed == 1, [r.message for r in report.results]
    assert report.failed == 0


_SELECTION_SOURCE = """
from rustest import mark


@mark.django_db
class TestMarked:
    def test_in_class(self):
        pass


def test_plain():
    pass
"""


def test_mark_expression_selects_methods_of_marked_class(tmp_path: Path) -> None:
    report = _run(tmp_path, _SELECTION_SOURCE, mark_expr="django_db")

    assert [r.name for r in report.results] == ["TestMarked::test_in_class"]


def test_negated_mark_expression_excludes_methods_of_marked_class(tmp_path: Path) -> None:
    report = _run(tmp_path, _SELECTION_SOURCE, mark_expr="not django_db")

    assert [r.name for r in report.results] == ["test_plain"]


def test_module_pytestmark_list_is_visible_to_functions_and_methods(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        """
from rustest import fixture, mark

pytestmark = [mark.django_db(transaction=True), mark.slow]


@fixture
def probe(request):
    return request.node.get_closest_marker("django_db")


@fixture
def slow_probe(request):
    return request.node.get_closest_marker("slow")


def test_function(probe, slow_probe):
    assert probe is not None and probe.kwargs == {"transaction": True}
    assert slow_probe is not None


class TestInModule:
    def test_method(self, probe, slow_probe):
        assert probe is not None and probe.kwargs == {"transaction": True}
        assert slow_probe is not None
""",
    )

    assert report.passed == 2, [r.message for r in report.results]


def test_module_pytestmark_single_mark_drives_mark_expression(tmp_path: Path) -> None:
    source = """
from rustest import mark

pytestmark = mark.django_db


def test_function():
    pass


class TestInModule:
    def test_method(self):
        pass
"""
    selected = _run(tmp_path, source, mark_expr="django_db")
    excluded = _run(tmp_path, source, mark_expr="not django_db")

    assert sorted(r.name for r in selected.results) == [
        "TestInModule::test_method",
        "test_function",
    ]
    assert excluded.results == ()


def test_closest_mark_wins_method_then_class_then_module(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        """
from rustest import fixture, mark

pytestmark = mark.level(where="module")


@fixture
def where(request):
    return request.node.get_closest_marker("level").kwargs["where"]


def test_function_uses_module(where):
    assert where == "module"


@mark.level(where="class")
class TestLevels:
    def test_method_uses_class(self, where):
        assert where == "class"

    @mark.level(where="method")
    def test_method_uses_own(self, where):
        assert where == "method"
""",
    )

    assert report.passed == 3, [r.message for r in report.results]


def test_class_marks_do_not_disturb_asyncio_and_parametrize(tmp_path: Path) -> None:
    report = _run(
        tmp_path,
        """
from rustest import mark, parametrize


@mark.asyncio
@mark.slow
class TestAsyncAndParams:
    async def test_async_method(self):
        assert True

    def test_sync_method(self):
        assert True

    @parametrize("value", [1, 2])
    def test_param_method(self, value):
        assert value in (1, 2)
""",
    )

    assert report.passed == 4, [r.message for r in report.results]
    assert report.failed == 0
