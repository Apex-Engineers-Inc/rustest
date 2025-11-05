"""Tests for error handling scenarios."""

from __future__ import annotations

import unittest

from .helpers import ensure_rust_stub
from rustest import parametrize, fixture

ensure_rust_stub()


class ErrorHandlingTests(unittest.TestCase):
    """Tests for various error scenarios."""

    def test_parametrize_empty_argnames_raises_error(self) -> None:
        """Test that empty argnames raises ValueError."""
        with self.assertRaises(ValueError) as ctx:

            @parametrize("", [(1,)])
            def _test(_: int) -> None:
                pass

        self.assertIn("at least one argument", str(ctx.exception).lower())

    def test_parametrize_mismatched_values_raises_error(self) -> None:
        """Test that mismatched values raises ValueError."""
        with self.assertRaises(ValueError) as ctx:

            @parametrize(("x", "y"), [(1,)])  # Missing one value
            def _test(_x: int, _y: int) -> None:
                pass

        self.assertIn("does not match", str(ctx.exception).lower())

    def test_parametrize_mismatched_ids_raises_error(self) -> None:
        """Test that mismatched IDs raises ValueError."""
        with self.assertRaises(ValueError) as ctx:

            @parametrize("value", [(1,), (2,)], ids=["only_one"])
            def _test(_: int) -> None:
                pass

        self.assertIn("must match", str(ctx.exception).lower())

    def test_parametrize_with_empty_values_list(self) -> None:
        """Test that empty values list works correctly."""

        @parametrize("x", [])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        self.assertEqual(len(cases), 0)

    def test_parametrize_with_whitespace_only_argname(self) -> None:
        """Test that whitespace-only argnames raise ValueError."""
        with self.assertRaises(ValueError):

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
        self.assertIn("x", cases[0]["values"])
        self.assertIn("y", cases[0]["values"])

    def test_fixture_with_exception_in_body(self) -> None:
        """Test that fixtures can raise exceptions."""

        @fixture
        def broken_fixture() -> None:
            raise RuntimeError("Fixture is broken")

        with self.assertRaises(RuntimeError):
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
        self.assertEqual(len(cases), 3)

    def test_parametrize_with_very_long_id(self) -> None:
        """Test handling of very long custom IDs."""
        long_id = "a" * 1000

        @parametrize("x", [(1,)], ids=[long_id])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        self.assertEqual(cases[0]["id"], long_id)

    def test_parametrize_with_duplicate_ids(self) -> None:
        """Test handling of duplicate IDs (should be allowed)."""

        @parametrize("x", [(1,), (2,)], ids=["same", "same"])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        self.assertEqual(cases[0]["id"], "same")
        self.assertEqual(cases[1]["id"], "same")


class EdgeCaseTests(unittest.TestCase):
    """Tests for edge cases and boundary conditions."""

    def test_parametrize_with_single_comma_separated_arg(self) -> None:
        """Test single parameter with comma-separated string format."""

        @parametrize("x", [(1,), (2,)])
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        self.assertEqual(len(cases), 2)

    def test_parametrize_with_nested_tuples(self) -> None:
        """Test parametrization with nested tuple values."""

        @parametrize("data", [((1, 2),), ((3, 4),)])
        def test_func(data: tuple) -> tuple:
            return data

        cases = getattr(test_func, "__rustest_parametrization__")
        self.assertEqual(cases[0]["values"]["data"], (1, 2))

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
        self.assertEqual(len(cases), 5)
        self.assertEqual(cases[0]["values"]["value"], 1)
        self.assertEqual(cases[1]["values"]["value"], "string")
        self.assertIsNone(cases[2]["values"]["value"])
        self.assertTrue(cases[3]["values"]["value"])
        self.assertEqual(cases[4]["values"]["value"], [1, 2, 3])

    def test_fixture_with_class_method(self) -> None:
        """Test that fixture decorator works on class methods."""

        class TestClass:
            @staticmethod
            @fixture
            def static_fixture() -> int:
                return 42

        self.assertTrue(hasattr(TestClass.static_fixture, "__rustest_fixture__"))

    def test_parametrize_with_large_number_of_cases(self) -> None:
        """Test parametrization with many cases."""
        cases_data = [(i,) for i in range(100)]

        @parametrize("x", cases_data)
        def test_func(x: int) -> int:
            return x

        cases = getattr(test_func, "__rustest_parametrization__")
        self.assertEqual(len(cases), 100)
        self.assertEqual(cases[0]["values"]["x"], 0)
        self.assertEqual(cases[99]["values"]["x"], 99)

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
        self.assertEqual(len(cases), 5)
        self.assertEqual(cases[0]["values"]["text"], "")
        self.assertEqual(cases[2]["values"]["text"], "\n")

    def test_fixture_returns_lambda(self) -> None:
        """Test that fixtures can return callable objects."""

        @fixture
        def lambda_fixture():  # type: ignore
            return lambda x: x * 2

        result = lambda_fixture()
        self.assertTrue(callable(result))
        self.assertEqual(result(5), 10)

    def test_parametrize_preserves_callable(self) -> None:
        """Test that parametrized functions remain callable."""

        @parametrize("x", [(1,), (2,)])
        def test_func(x: int) -> int:
            return x * 2

        # Should still be callable
        self.assertTrue(callable(test_func))
        # When called directly, should execute normally
        self.assertEqual(test_func(3), 6)

    def test_multiple_parametrize_decorators(self) -> None:
        """Test applying parametrize multiple times."""

        @parametrize("y", [(10,), (20,)])
        @parametrize("x", [(1,), (2,)])
        def test_func(x: int, y: int) -> int:
            return x + y

        # Both should be stored
        self.assertTrue(hasattr(test_func, "__rustest_parametrization__"))


class RobustnessTests(unittest.TestCase):
    """Tests for robustness and unusual inputs."""

    def test_fixture_with_args_and_kwargs(self) -> None:
        """Test that fixtures work with *args and **kwargs."""

        @fixture
        def flexible_fixture(*args, **kwargs):  # type: ignore
            return (args, kwargs)

        self.assertTrue(getattr(flexible_fixture, "__rustest_fixture__"))

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
        self.assertEqual(len(cases), 2)
        self.assertIsInstance(cases[0]["values"]["obj"], DummyClass)

    def test_fixture_with_default_arguments(self) -> None:
        """Test fixtures with default argument values."""

        @fixture
        def fixture_with_default(x: int = 10) -> int:
            return x

        self.assertEqual(fixture_with_default(), 10)
        self.assertEqual(fixture_with_default(20), 20)


if __name__ == "__main__":
    unittest.main()
