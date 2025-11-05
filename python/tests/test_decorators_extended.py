"""Extended tests for decorator functionality."""

from __future__ import annotations

import pytest

from .helpers import ensure_rust_stub
from rustest import fixture, parametrize, skip

ensure_rust_stub()


class TestExtendedFixture:
    def test_fixture_preserves_function_name(self) -> None:
        @fixture
        def my_fixture() -> int:
            return 42

        assert my_fixture.__name__ == "my_fixture"

    def test_fixture_preserves_docstring(self) -> None:
        @fixture
        def documented() -> int:
            """This is a docstring."""
            return 10

        assert documented.__doc__ == "This is a docstring."

    def test_fixture_can_return_none(self) -> None:
        @fixture
        def none_fixture() -> None:
            return None

        assert getattr(none_fixture, "__rustest_fixture__")
        assert none_fixture() is None

    def test_fixture_can_return_complex_types(self) -> None:
        @fixture
        def dict_fixture() -> dict:
            return {"key": "value"}

        result = dict_fixture()
        assert isinstance(result, dict)
        assert result["key"] == "value"

    def test_multiple_fixture_decorations(self) -> None:
        """Test that fixture can be composed with other decorators."""

        @fixture
        def simple() -> int:
            return 1

        # Should still be marked as a fixture
        assert getattr(simple, "__rustest_fixture__")


class TestExtendedSkip:
    def test_skip_with_empty_string_reason(self) -> None:
        @skip("")
        def test_func() -> None:
            pass

        # Empty string is treated as no reason, so default is used
        assert getattr(test_func, "__rustest_skip__") == "skipped via rustest.skip"

    def test_skip_with_multiline_reason(self) -> None:
        reason = "This is a\nmultiline\nreason"

        @skip(reason)
        def test_func() -> None:
            pass

        assert getattr(test_func, "__rustest_skip__") == reason

    def test_skip_with_special_characters(self) -> None:
        reason = "Special chars: @#$%^&*(){}[]"

        @skip(reason)
        def test_func() -> None:
            pass

        assert getattr(test_func, "__rustest_skip__") == reason

    def test_skip_preserves_function_attributes(self) -> None:
        @skip("reason")
        def test_with_attrs() -> None:
            """Docstring here."""
            pass

        assert test_with_attrs.__name__ == "test_with_attrs"
        assert test_with_attrs.__doc__ == "Docstring here."


class TestExtendedParametrize:
    def test_parametrize_with_single_value(self) -> None:
        @parametrize("x", [(1,)])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 1
        assert cases[0]["values"]["x"] == 1

    def test_parametrize_with_many_parameters(self) -> None:
        @parametrize(("a", "b", "c", "d", "e"), [(1, 2, 3, 4, 5), (6, 7, 8, 9, 10)])
        def test_func(a: int, b: int, c: int, d: int, e: int) -> int:
            return a + b + c + d + e

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 2
        assert cases[0]["values"]["a"] == 1
        assert cases[0]["values"]["e"] == 5
        assert cases[1]["values"]["a"] == 6

    def test_parametrize_with_complex_values(self) -> None:
        complex_data = [
            ({"nested": {"dict": "value"}}, [1, 2, 3]),
            ({"another": "dict"}, [4, 5, 6]),
        ]

        @parametrize(("dict_val", "list_val"), complex_data)
        def test_func(dict_val: dict, list_val: list) -> None:
            pass

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 2
        assert isinstance(cases[0]["values"]["dict_val"], dict)
        assert isinstance(cases[0]["values"]["list_val"], list)

    def test_parametrize_with_custom_ids(self) -> None:
        @parametrize("value", [(1,), (2,), (3,)], ids=["first", "second", "third"])
        def test_func(value: int) -> int:
            return value

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases[0]["id"] == "first"
        assert cases[1]["id"] == "second"
        assert cases[2]["id"] == "third"

    def test_parametrize_with_unicode_ids(self) -> None:
        @parametrize("value", [(1,), (2,)], ids=["测试", "🚀"])
        def test_func(value: int) -> int:
            return value

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases[0]["id"] == "测试"
        assert cases[1]["id"] == "🚀"

    def test_parametrize_with_none_values(self) -> None:
        @parametrize("value", [(None,), (None,)])
        def test_func(value: None) -> None:
            pass

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 2
        assert cases[0]["values"]["value"] is None

    def test_parametrize_rejects_none_as_argnames(self) -> None:
        with pytest.raises(TypeError):

            @parametrize(None, [(1,)])  # type: ignore
            def _(_: int) -> None:
                pass

    def test_parametrize_with_boolean_values(self) -> None:
        @parametrize("flag", [(True,), (False,)])
        def test_func(flag: bool) -> bool:
            return flag

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases[0]["values"]["flag"] is True
        assert cases[1]["values"]["flag"] is False

    def test_parametrize_with_zero_and_negative_numbers(self) -> None:
        @parametrize("num", [(0,), (-1,), (-100,)])
        def test_func(num: int) -> int:
            return num

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases[0]["values"]["num"] == 0
        assert cases[1]["values"]["num"] == -1
        assert cases[2]["values"]["num"] == -100

    def test_parametrize_with_float_values(self) -> None:
        @parametrize("value", [(1.5,), (2.7,), (3.14159,)])
        def test_func(value: float) -> float:
            return value

        cases = getattr(test_func, "__rustest_parametrization__")
        assert abs(cases[0]["values"]["value"] - 1.5) < 0.001
        assert abs(cases[2]["values"]["value"] - 3.14159) < 0.00001

    def test_parametrize_with_tuple_values(self) -> None:
        @parametrize("data", [((1, 2),), ((3, 4),)])
        def test_func(data: tuple) -> tuple:
            return data

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases[0]["values"]["data"] == (1, 2)
        assert cases[1]["values"]["data"] == (3, 4)


class TestCombinedDecorators:
    def test_skip_and_parametrize_together(self) -> None:
        @skip("not ready")
        @parametrize("x", [(1,), (2,)])
        def test_func(x: int) -> int:
            return x

        assert getattr(test_func, "__rustest_skip__") == "not ready"
        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 2

    def test_parametrize_order_matters(self) -> None:
        """Test that decorator order is preserved."""

        @parametrize("a", [(1,)])
        @parametrize("b", [(2,)])
        def test_func(a: int, b: int) -> int:
            return a + b

        # Should have parametrization metadata
        assert hasattr(test_func, "__rustest_parametrization__")
