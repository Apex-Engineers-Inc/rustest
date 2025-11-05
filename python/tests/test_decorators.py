from __future__ import annotations

import pytest

from .helpers import ensure_rust_stub
from rustest import fixture, mark, parametrize, skip

ensure_rust_stub()


class TestFixtureDecorator:
    def test_fixture_marks_callable(self) -> None:
        @fixture
        def sample() -> int:
            return 42

        assert getattr(sample, "__rustest_fixture__")
        assert sample() == 42


class TestSkipDecorator:
    def test_skip_attaches_reason(self) -> None:
        @skip("because we can")
        def test_func() -> None:
            raise AssertionError("should not run")

        assert getattr(test_func, "__rustest_skip__") == "because we can"

    def test_skip_uses_default_reason(self) -> None:
        @skip()
        def test_func() -> None:
            raise AssertionError("should not run")

        assert getattr(test_func, "__rustest_skip__") == "skipped via rustest.skip"


class TestParametrizeDecorator:
    def test_parametrize_with_string_names(self) -> None:
        @parametrize("value", [(1,), (2,)], ids=["one", "two"])
        def test_func(value: int) -> int:
            return value

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases == (
            {"id": "one", "values": {"value": 1}},
            {"id": "two", "values": {"value": 2}},
        )

    def test_parametrize_with_sequence_names(self) -> None:
        @parametrize(("x", "y"), [(1, 2), (3, 4)])
        def test_func(x: int, y: int) -> tuple[int, int]:
            return x, y

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases == (
            {"id": "case_0", "values": {"x": 1, "y": 2}},
            {"id": "case_1", "values": {"x": 3, "y": 4}},
        )

    def test_parametrize_rejects_empty_names(self) -> None:
        with pytest.raises(ValueError):
            parametrize("", [(1,)])

    def test_parametrize_rejects_mismatched_values(self) -> None:
        with pytest.raises(ValueError):

            @parametrize(("x", "y"), [(1,)])
            def _(_: int, __: int) -> None:
                raise AssertionError("should not run")

    def test_parametrize_rejects_mismatched_ids(self) -> None:
        with pytest.raises(ValueError):

            @parametrize("value", [(1,), (2,)], ids=["only-one"])
            def _(_: int) -> None:
                raise AssertionError("should not run")


class TestMarkDecorator:
    def test_mark_attaches_single_mark(self) -> None:
        @mark.slow
        def test_func() -> None:
            pass

        marks = getattr(test_func, "__rustest_marks__")
        assert len(marks) == 1
        assert marks[0]["name"] == "slow"
        assert marks[0]["args"] == ()
        assert marks[0]["kwargs"] == {}

    def test_mark_with_args(self) -> None:
        @mark.timeout(30)
        def test_func() -> None:
            pass

        marks = getattr(test_func, "__rustest_marks__")
        assert len(marks) == 1
        assert marks[0]["name"] == "timeout"
        assert marks[0]["args"] == (30,)
        assert marks[0]["kwargs"] == {}

    def test_mark_with_kwargs(self) -> None:
        @mark.custom(key="value", priority=1)
        def test_func() -> None:
            pass

        marks = getattr(test_func, "__rustest_marks__")
        assert len(marks) == 1
        assert marks[0]["name"] == "custom"
        assert marks[0]["args"] == ()
        assert marks[0]["kwargs"] == {"key": "value", "priority": 1}

    def test_multiple_marks(self) -> None:
        @mark.slow
        @mark.integration
        @mark.smoke
        def test_func() -> None:
            pass

        marks = getattr(test_func, "__rustest_marks__")
        assert len(marks) == 3
        # Marks are applied bottom-to-top (decorator order)
        assert marks[0]["name"] == "smoke"
        assert marks[1]["name"] == "integration"
        assert marks[2]["name"] == "slow"

    def test_mark_preserves_function(self) -> None:
        @mark.unit
        def test_func() -> int:
            return 42

        assert test_func() == 42
        assert hasattr(test_func, "__rustest_marks__")
