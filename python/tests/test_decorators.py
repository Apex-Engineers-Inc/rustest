from __future__ import annotations

import pytest

from .helpers import ensure_rust_stub
from rustest import fixture, mark, parametrize, skip_decorator

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
        @skip_decorator("because we can")
        def test_func() -> None:
            raise AssertionError("should not run")

        assert getattr(test_func, "__rustest_skip__") == "because we can"

    def test_skip_uses_default_reason(self) -> None:
        @skip_decorator()
        def test_func() -> None:
            raise AssertionError("should not run")

        assert getattr(test_func, "__rustest_skip__") == "skipped via rustest.skip"


class TestParametrizeDecorator:
    def test_parametrize_with_string_names(self) -> None:
        @parametrize("value", [1, 2], ids=["one", "two"])
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
            {"id": "1-2", "values": {"x": 1, "y": 2}},
            {"id": "3-4", "values": {"x": 3, "y": 4}},
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

    def test_mark_parametrize_matches_top_level_decorator(self) -> None:
        @mark.parametrize(
            "start,increment,expected",
            [(10, 1, 11), (5, 4, 9)],
            ids=["ten-plus-one", "five-plus-four"],
        )
        def test_func(start: int, increment: int, expected: int) -> None:
            pass

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases == (
            {
                "id": "ten-plus-one",
                "values": {"start": 10, "increment": 1, "expected": 11},
            },
            {
                "id": "five-plus-four",
                "values": {"start": 5, "increment": 4, "expected": 9},
            },
        )
        assert not hasattr(test_func, "__rustest_marks__")

    def test_mark_parametrize_allows_fixture_arguments(self) -> None:
        @fixture
        def base_number() -> int:
            return 10

        @mark.parametrize(
            "addend,expected_total",
            [
                (3, 13),
                (7, 17),
            ],
            ids=["plus-three", "plus-seven"],
        )
        def test_func(base_number: int, addend: int, expected_total: int) -> tuple[int, int]:
            return base_number + addend, expected_total

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases == (
            {
                "id": "plus-three",
                "values": {"addend": 3, "expected_total": 13},
            },
            {
                "id": "plus-seven",
                "values": {"addend": 7, "expected_total": 17},
            },
        )
        assert not hasattr(test_func, "__rustest_marks__")

        first_case = cases[0]["values"]
        calculated, expected = test_func(10, first_case["addend"], first_case["expected_total"])
        assert calculated == expected


class TestParametrizeForceTuple:
    """`ParameterSet._for_parametrize`'s unpacking rule, pinned shape by shape.

    Port target: `_pytest/mark/structures.py` l. 137-227. The rule is decided by
    `_parse_parametrize_args` (l. 165-177) -- `force_tuple = len(argnames) == 1` **and only
    when `argnames` is a `str`** -- and applied by `extract_from` (l. 137-161).

    MECHANISM M2 of the Phase 4 Task 1b sweep. Before the port, rustest decided unpacking by
    comparing lengths, so a length-1 sequence under a single argname was unpacked and the
    test silently received the wrong value. Each assertion below is an oracle answer probed
    on pytest 8.4.2, not an inference.
    """

    def test_a_single_string_name_makes_each_argvalue_one_value(self) -> None:
        @parametrize("value", [[42], [7, 8], (1,), "ab", {"k": 1}])
        def test_func(value: object) -> object:
            return value

        cases = getattr(test_func, "__rustest_parametrization__")
        assert [case["values"]["value"] for case in cases] == [
            [42],
            [7, 8],
            (1,),
            "ab",
            {"k": 1},
        ]

    def test_a_sequence_name_never_forces_a_tuple(self) -> None:
        """`("solo",)` is a Sequence, so `(9,)` IS the value set, not one value."""

        @parametrize(("solo",), [(9,), (10,)])
        def test_func(solo: int) -> int:
            return solo

        cases = getattr(test_func, "__rustest_parametrization__")
        assert [case["values"]["solo"] for case in cases] == [9, 10]

    def test_a_sequence_name_with_an_over_long_value_set_is_an_error(self) -> None:
        """`@parametrize(["x"], [[1, 2]])` -- one name, two values. pytest fails it."""
        with pytest.raises(ValueError) as excinfo:

            @parametrize(["x"], [[1, 2]])
            def _test(_x: object) -> None:
                pass

        assert "must be equal to the number of values" in str(excinfo.value)

    def test_two_names_still_unpack_lists_and_tuples_alike(self) -> None:
        @parametrize("a,b", [(1, 2), [3, 4]])
        def test_func(a: int, b: int) -> int:
            return a + b

        cases = getattr(test_func, "__rustest_parametrization__")
        assert [case["values"] for case in cases] == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

    def test_ids_are_generated_from_the_bound_value_not_the_wrapper(self) -> None:
        """The defect corrupted node ids too -- attrs' `test_setattr` diverged on id alone."""

        @parametrize("rng", [(0,), (0, 1)])
        def test_func(rng: tuple[int, ...]) -> tuple[int, ...]:
            return rng

        cases = getattr(test_func, "__rustest_parametrization__")
        # Both are containers, so pytest falls back to `<argname><index>` for each.
        assert [case["id"] for case in cases] == ["rng0", "rng1"]


class TestParametrizePytestKeywordSpelling:
    """`parametrize(argnames=..., argvalues=...)` -- pytest's parameter names as keywords.

    MECHANISM M8. pytest's signature is `Metafunc.parametrize(argnames, argvalues, ...)`
    (`_pytest/python.py` l. 1163-1167); the `argvalues` alias already existed and the
    `argnames` one did not, which cost the whole of FastAPI (3 289 tests) in the Task 1b
    sweep because three of its modules spell it as a keyword and a collection error aborts
    the session.
    """

    def test_both_names_as_keywords(self) -> None:
        @parametrize(argnames="a,b", argvalues=[(1, 2)])
        def test_func(a: int, b: int) -> int:
            return a + b

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases == ({"id": "1-2", "values": {"a": 1, "b": 2}},)

    def test_keyword_argnames_still_decides_force_tuple(self) -> None:
        @parametrize(argnames="value", argvalues=[[1], [2, 3]])
        def test_func(value: list[int]) -> list[int]:
            return value

        cases = getattr(test_func, "__rustest_parametrization__")
        assert [case["values"]["value"] for case in cases] == [[1], [2, 3]]

    def test_neither_spelling_given_is_a_type_error(self) -> None:
        with pytest.raises(TypeError):
            parametrize(argvalues=[1, 2])  # pyright: ignore[reportCallIssue]
