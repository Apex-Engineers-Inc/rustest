"""Tests for error handling scenarios."""

from __future__ import annotations

import pytest

from .helpers import ensure_rust_stub
from rustest import parametrize, fixture, raises

ensure_rust_stub()


class TestErrorHandling:
    """Tests for various error scenarios."""

    def test_parametrize_empty_argnames_raises_error(self) -> None:
        """Test that empty argnames raises ValueError."""
        with pytest.raises(ValueError) as ctx:

            @parametrize("", [(1,)])
            def _test(_: int) -> None:
                pass

        assert "at least one argument" in str(ctx.value).lower()

    def test_parametrize_mismatched_values_raises_error(self) -> None:
        """Test that mismatched values raises ValueError."""
        with pytest.raises(ValueError) as ctx:

            @parametrize(("x", "y"), [(1,)])  # Missing one value
            def _test(_x: int, _y: int) -> None:
                pass

        assert "does not match" in str(ctx.value).lower()

    def test_parametrize_mismatched_ids_raises_error(self) -> None:
        """Test that mismatched IDs raises ValueError."""
        with pytest.raises(ValueError) as ctx:

            @parametrize("value", [(1,), (2,)], ids=["only_one"])
            def _test(_: int) -> None:
                pass

        assert "must match" in str(ctx.value).lower()

    def test_parametrize_with_empty_values_list(self) -> None:
        """Test that empty values list works correctly."""

        @parametrize("x", [])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 0

    def test_parametrize_with_whitespace_only_argname(self) -> None:
        """Test that whitespace-only argnames raise ValueError."""
        with pytest.raises(ValueError):

            @parametrize("   ", [(1,)])
            def _test(_: int) -> None:
                pass

    def test_parametrize_with_invalid_argname_format(self) -> None:
        """Test handling of invalid argname formats."""

        # Comma-separated string with spaces should work
        @parametrize("x, y", [(1, 2)])
        def test_func(x: int, y: int) -> None:
            pass

        cases = getattr(test_func, "__rustest_parametrization__")
        assert "x" in cases[0]["values"]
        assert "y" in cases[0]["values"]

    def test_fixture_with_exception_in_body(self) -> None:
        """Test that fixtures can raise exceptions."""

        @fixture
        def broken_fixture() -> None:
            raise RuntimeError("Fixture is broken")

        with pytest.raises(RuntimeError):
            broken_fixture()

    def test_parametrize_with_generator_values(self) -> None:
        """Test that generator values are properly consumed."""

        def value_generator():
            yield (1,)
            yield (2,)
            yield (3,)

        @parametrize("x", list(value_generator()))
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 3

    def test_parametrize_with_very_long_id(self) -> None:
        """Test handling of very long custom IDs."""
        long_id = "a" * 1000

        @parametrize("x", [(1,)], ids=[long_id])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases[0]["id"] == long_id

    def test_parametrize_with_duplicate_ids(self) -> None:
        """Test handling of duplicate IDs (should be allowed)."""

        @parametrize("x", [(1,), (2,)], ids=["same", "same"])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases[0]["id"] == "same"
        assert cases[1]["id"] == "same"


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_parametrize_with_single_comma_separated_arg(self) -> None:
        """Test single parameter with comma-separated string format."""

        @parametrize("x", [(1,), (2,)])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 2

    def test_parametrize_with_nested_tuples(self) -> None:
        """Test parametrization with nested tuple values."""

        @parametrize("data", [((1, 2),), ((3, 4),)])
        def test_func(data: tuple) -> tuple:
            return data

        cases = getattr(test_func, "__rustest_parametrization__")
        assert cases[0]["values"]["data"] == (1, 2)

    def test_parametrize_with_mixed_types(self) -> None:
        """Test parametrization with mixed value types."""

        @parametrize(
            "value",
            [
                (1,),
                ("string",),
                (None,),
                (True,),
                ([1, 2, 3],),
            ],
        )
        def test_func(value) -> None:  # type: ignore
            pass

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 5
        assert cases[0]["values"]["value"] == 1
        assert cases[1]["values"]["value"] == "string"
        assert cases[2]["values"]["value"] is None
        assert cases[3]["values"]["value"] is True
        assert cases[4]["values"]["value"] == [1, 2, 3]

    def test_fixture_with_class_method(self) -> None:
        """Test that fixture decorator works on class methods."""

        class TestClass:
            @staticmethod
            @fixture
            def static_fixture() -> int:
                return 42

        assert hasattr(TestClass.static_fixture, "__rustest_fixture__")

    def test_parametrize_with_large_number_of_cases(self) -> None:
        """Test parametrization with many cases."""
        cases_data = [(i,) for i in range(100)]

        @parametrize("x", cases_data)
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 100
        assert cases[0]["values"]["x"] == 0
        assert cases[99]["values"]["x"] == 99

    def test_parametrize_with_special_string_values(self) -> None:
        """Test parametrization with special string values."""

        @parametrize(
            "text",
            [
                ("",),  # Empty string
                ("\\n",),  # Escaped newline
                ("\n",),  # Actual newline
                ("\t",),  # Tab
                ("'\"",),  # Quotes
            ],
        )
        def test_func(text: str) -> str:
            return text

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 5
        assert cases[0]["values"]["text"] == ""
        assert cases[2]["values"]["text"] == "\n"

    def test_fixture_returns_lambda(self) -> None:
        """Test that fixtures can return callable objects."""

        @fixture
        def lambda_fixture():  # type: ignore
            return lambda x: x * 2

        result = lambda_fixture()
        assert callable(result)
        assert result(5) == 10

    def test_parametrize_preserves_callable(self) -> None:
        """Test that parametrized functions remain callable."""

        @parametrize("x", [(1,), (2,)])
        def test_func(x: int) -> int:
            return x * 2

        # Should still be callable
        assert callable(test_func)
        # When called directly, should execute normally
        assert test_func(3) == 6

    def test_multiple_parametrize_decorators(self) -> None:
        """Test applying parametrize multiple times."""

        @parametrize("y", [(10,), (20,)])
        @parametrize("x", [(1,), (2,)])
        def test_func(x: int, y: int) -> int:
            return x + y

        # Both should be stored
        assert hasattr(test_func, "__rustest_parametrization__")


class TestRobustness:
    """Tests for robustness and unusual inputs."""

    def test_fixture_with_args_and_kwargs(self) -> None:
        """Test that fixtures work with *args and **kwargs."""

        @fixture
        def flexible_fixture(*args, **kwargs):  # type: ignore
            return (args, kwargs)

        assert getattr(flexible_fixture, "__rustest_fixture__")

    def test_parametrize_with_class_instances(self) -> None:
        """Test parametrization with class instances."""

        class DummyClass:
            def __init__(self, value: int):
                self.value = value

        obj1 = DummyClass(1)
        obj2 = DummyClass(2)

        @parametrize("obj", [(obj1,), (obj2,)])
        def test_func(obj: DummyClass) -> int:
            return obj.value

        cases = getattr(test_func, "__rustest_parametrization__")
        assert len(cases) == 2
        assert isinstance(cases[0]["values"]["obj"], DummyClass)

    def test_fixture_with_default_arguments(self) -> None:
        """Test fixtures with default argument values."""

        @fixture
        def fixture_with_default(x: int = 10) -> int:
            return x

        assert fixture_with_default() == 10
        assert fixture_with_default(20) == 20


class TestRaises:
    """Tests for the raises() context manager."""

    def test_raises_basic_exception(self) -> None:
        """Test that raises catches the expected exception."""
        with raises(ValueError):
            raise ValueError("test error")

    def test_raises_with_match_success(self) -> None:
        """Test that raises with match works when pattern matches."""
        with raises(ValueError, match="invalid"):
            raise ValueError("invalid literal")

    def test_raises_with_match_regex(self) -> None:
        """Test that raises match supports regex patterns."""
        with raises(ValueError, match=r"invalid \w+"):
            raise ValueError("invalid literal")

    def test_raises_with_match_failure(self) -> None:
        """Test that raises with match fails when pattern doesn't match."""
        with pytest.raises(AssertionError, match="Pattern.*does not match"):
            with raises(ValueError, match="notfound"):
                raise ValueError("something else")

    def test_raises_no_exception(self) -> None:
        """Test that raises fails when no exception is raised."""
        with pytest.raises(AssertionError, match="DID NOT RAISE"):
            with raises(ValueError):
                pass  # No exception raised

    def test_raises_wrong_exception(self) -> None:
        """Test that raises lets unexpected exceptions propagate."""
        with pytest.raises(TypeError):
            with raises(ValueError):
                raise TypeError("wrong exception")

    def test_raises_tuple_of_exceptions(self) -> None:
        """Test that raises accepts a tuple of exception types."""
        with raises((ValueError, TypeError)):
            raise ValueError("test")

        with raises((ValueError, TypeError)):
            raise TypeError("test")

    def test_raises_access_exception_value(self) -> None:
        """Test that we can access the caught exception."""
        with raises(ValueError) as exc_info:
            raise ValueError("test message")

        assert exc_info.value is not None
        assert str(exc_info.value) == "test message"
        assert exc_info.type is ValueError

    def test_raises_access_exception_before_exit(self) -> None:
        """Test that accessing exception before exit raises AttributeError."""
        ctx = raises(ValueError)
        with pytest.raises(AttributeError, match="No exception was caught"):
            _ = ctx.value

    def test_raises_subclass_exception(self) -> None:
        """Test that raises catches subclass exceptions."""

        class CustomError(ValueError):
            pass

        with raises(ValueError):
            raise CustomError("test")

    def test_raises_with_assertion_error(self) -> None:
        """Test that raises works with AssertionError."""
        with raises(AssertionError):
            assert False, "This should fail"

    def test_raises_with_runtime_error(self) -> None:
        """Test that raises works with RuntimeError."""
        with raises(RuntimeError, match="Something went wrong"):
            raise RuntimeError("Something went wrong")

    def test_raises_with_zero_division(self) -> None:
        """Test that raises works with ZeroDivisionError."""
        with raises(ZeroDivisionError):
            _ = 1 / 0

    def test_raises_excinfo_repr(self) -> None:
        """Test the ExceptionInfo repr."""
        with raises(ValueError) as exc_info:
            raise ValueError("test")

        repr_str = repr(exc_info.excinfo)
        assert "ExceptionInfo" in repr_str
        assert "ValueError" in repr_str

    def test_raises_with_partial_match(self) -> None:
        """Test that match does partial matching, not exact."""
        with raises(ValueError, match="invalid"):
            raise ValueError("this is an invalid literal for conversion")

    def test_raises_format_exc_name_single(self) -> None:
        """Test formatting of single exception name."""
        with pytest.raises(AssertionError, match="DID NOT RAISE ValueError"):
            with raises(ValueError):
                pass

    def test_raises_format_exc_name_tuple(self) -> None:
        """Test formatting of tuple of exception names."""
        with pytest.raises(AssertionError, match="ValueError or TypeError"):
            with raises((ValueError, TypeError)):
                pass

    def test_raises_with_exception_in_message(self) -> None:
        """Test that match failure message includes exception details."""
        with pytest.raises(AssertionError) as exc_info:
            with raises(ValueError, match="expected"):
                raise ValueError("actual message")

        error_msg = str(exc_info.value)
        assert "Pattern 'expected' does not match" in error_msg
        assert "'actual message'" in error_msg
        assert "ValueError" in error_msg
