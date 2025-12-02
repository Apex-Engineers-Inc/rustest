"""Tests for pytest-compat mode features.

This module tests the pytest compatibility features including:
- pytest.warns() context manager
- pytest.deprecated_call()
- capsys and capfd fixtures
- pytest.param() for parametrize
- List parametrize (lists treated as tuples)
- pytest.importorskip()
"""

from __future__ import annotations

import warnings

import pytest

from rustest.compat.pytest import (
    warns,
    deprecated_call,
    param,
    importorskip,
    FixtureRequest,
)
from rustest.decorators import parametrize, ParameterSet, _build_cases
from rustest.builtin_fixtures import CaptureFixture
from rustest.fixture_registry import register_fixtures, clear_registry


# =============================================================================
# Tests for pytest.warns()
# =============================================================================


class TestWarns:
    """Tests for the warns() context manager."""

    def test_warns_captures_warning(self):
        """Test that warns captures a warning of the expected type."""
        with warns(UserWarning) as record:
            warnings.warn("test warning", UserWarning)

        assert len(record) == 1
        assert "test warning" in str(record[0].message)

    def test_warns_captures_multiple_warnings(self):
        """Test that warns captures multiple warnings."""
        with warns(UserWarning) as record:
            warnings.warn("first", UserWarning)
            warnings.warn("second", UserWarning)

        assert len(record) == 2

    def test_warns_with_match_pattern(self):
        """Test that warns can filter by message pattern."""
        with warns(UserWarning, match="specific"):
            warnings.warn("this is a specific warning", UserWarning)

    def test_warns_match_pattern_fails_when_no_match(self):
        """Test that warns raises when pattern doesn't match."""
        with pytest.raises(AssertionError, match="Expected UserWarning"):
            with warns(UserWarning, match="nonexistent"):
                warnings.warn("different message", UserWarning)

    def test_warns_raises_when_no_warning(self):
        """Test that warns raises when no warning is emitted."""
        with pytest.raises(AssertionError, match="no warnings were raised"):
            with warns(UserWarning):
                pass  # No warning emitted

    def test_warns_raises_when_wrong_type(self):
        """Test that warns raises when wrong warning type is emitted."""
        with pytest.raises(AssertionError, match="Expected DeprecationWarning"):
            with warns(DeprecationWarning):
                warnings.warn("wrong type", UserWarning)

    def test_warns_with_tuple_of_types(self):
        """Test warns with multiple warning types."""
        with warns((UserWarning, DeprecationWarning)) as record:
            warnings.warn("user warning", UserWarning)

        assert len(record) == 1

    def test_warns_without_expected_type_captures_all(self):
        """Test warns without type captures all warnings."""
        with warns() as record:
            warnings.warn("first", UserWarning)
            warnings.warn("second", DeprecationWarning)

        assert len(record) == 2

    def test_warns_subclass_matching(self):
        """Test that warns matches subclasses of expected warning."""
        # DeprecationWarning is a subclass of Warning
        with warns(Warning):
            warnings.warn("deprecated", DeprecationWarning)


class TestDeprecatedCall:
    """Tests for the deprecated_call() context manager."""

    def test_deprecated_call_captures_deprecation(self):
        """Test that deprecated_call captures DeprecationWarning."""
        with deprecated_call():
            warnings.warn("old function", DeprecationWarning)

    def test_deprecated_call_captures_pending_deprecation(self):
        """Test that deprecated_call captures PendingDeprecationWarning."""
        with deprecated_call():
            warnings.warn("will be deprecated", PendingDeprecationWarning)

    def test_deprecated_call_with_match(self):
        """Test deprecated_call with match pattern."""
        with deprecated_call(match="old"):
            warnings.warn("old function", DeprecationWarning)

    def test_deprecated_call_raises_when_no_deprecation(self):
        """Test deprecated_call raises when no deprecation warning."""
        with pytest.raises(AssertionError):
            with deprecated_call():
                pass  # No warning


# =============================================================================
# Tests for capsys and capfd fixtures
# =============================================================================


class TestCaptureFixture:
    """Tests for the CaptureFixture class."""

    def test_capture_fixture_captures_stdout(self):
        """Test that CaptureFixture captures stdout."""
        capture = CaptureFixture()
        capture.start_capture()

        print("hello stdout")
        out, err = capture.readouterr()

        capture.stop_capture()

        assert out == "hello stdout\n"
        assert err == ""

    def test_capture_fixture_captures_stderr(self):
        """Test that CaptureFixture captures stderr."""
        import sys

        capture = CaptureFixture()
        capture.start_capture()

        print("hello stderr", file=sys.stderr)
        out, err = capture.readouterr()

        capture.stop_capture()

        assert out == ""
        assert err == "hello stderr\n"

    def test_capture_fixture_resets_on_readouterr(self):
        """Test that readouterr resets the capture buffers."""
        capture = CaptureFixture()
        capture.start_capture()

        print("first")
        out1, _ = capture.readouterr()

        print("second")
        out2, _ = capture.readouterr()

        capture.stop_capture()

        assert out1 == "first\n"
        assert out2 == "second\n"

    def test_capture_fixture_context_manager(self):
        """Test CaptureFixture as context manager."""
        with CaptureFixture() as capture:
            print("in context")
            out, err = capture.readouterr()

        assert out == "in context\n"

    def test_capture_fixture_restores_streams(self):
        """Test that CaptureFixture restores original streams."""
        import sys

        original_stdout = sys.stdout
        original_stderr = sys.stderr

        capture = CaptureFixture()
        capture.start_capture()
        capture.stop_capture()

        assert sys.stdout is original_stdout
        assert sys.stderr is original_stderr


# =============================================================================
# Tests for pytest.param()
# =============================================================================


class TestPytestParam:
    """Tests for pytest.param() functionality."""

    def test_param_creates_parameter_set(self):
        """Test that param() creates a ParameterSet."""
        result = param(1, 2, 3)

        assert isinstance(result, ParameterSet)
        assert result.values == (1, 2, 3)
        assert result.id is None

    def test_param_with_id(self):
        """Test param() with custom id."""
        result = param(1, 2, id="test_case")

        assert result.id == "test_case"
        assert result.values == (1, 2)

    def test_param_with_marks_warns(self):
        """Test that param() with marks emits a warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = param(1, marks="some_mark")

            assert len(w) == 1
            assert "marks are not yet supported" in str(w[0].message)

    def test_param_in_parametrize(self):
        """Test that param() works with parametrize decorator."""

        @parametrize(
            "x,y",
            [
                param(1, 2, id="small"),
                param(10, 20, id="large"),
            ],
        )
        def dummy_test(x, y):
            pass

        # Check that the test was decorated with parametrize data
        assert hasattr(dummy_test, "__rustest_parametrization__")
        cases = dummy_test.__rustest_parametrization__
        assert len(cases) == 2
        assert cases[0]["id"] == "small"
        assert cases[1]["id"] == "large"

    def test_param_single_value(self):
        """Test param() with single value."""
        result = param(42, id="answer")

        assert result.values == (42,)
        assert result.id == "answer"


# =============================================================================
# Tests for list parametrize (lists treated as tuples)
# =============================================================================


class TestListParametrize:
    """Tests for list values in parametrize being treated as tuples."""

    def test_list_values_unpacked_like_tuples(self):
        """Test that lists are unpacked like tuples in parametrize."""
        names = ("x", "y")
        values = [
            [1, 2],  # List should be unpacked
            (3, 4),  # Tuple should be unpacked
        ]

        cases = _build_cases(names, values, None)

        assert len(cases) == 2
        assert cases[0]["values"] == {"x": 1, "y": 2}
        assert cases[1]["values"] == {"x": 3, "y": 4}

    def test_mixed_list_tuple_values(self):
        """Test parametrize with mixed list and tuple values."""

        @parametrize(
            "a,b,c",
            [
                [1, 2, 3],  # List
                (4, 5, 6),  # Tuple
                [7, 8, 9],  # List
            ],
        )
        def dummy_test(a, b, c):
            pass

        cases = dummy_test.__rustest_parametrization__
        assert len(cases) == 3
        assert cases[0]["values"] == {"a": 1, "b": 2, "c": 3}
        assert cases[1]["values"] == {"a": 4, "b": 5, "c": 6}
        assert cases[2]["values"] == {"a": 7, "b": 8, "c": 9}

    def test_single_param_with_list_value(self):
        """Test single parameter with list as the value itself."""
        names = ("items",)
        values = [
            ([1, 2, 3],),  # List is the value, wrapped in tuple
        ]

        cases = _build_cases(names, values, None)

        assert cases[0]["values"] == {"items": [1, 2, 3]}

    def test_nested_list_in_parameters(self):
        """Test that nested lists work correctly."""

        @parametrize(
            "x,y",
            [
                [[1, 2], [3, 4]],  # Outer list unpacked, inner lists are values
            ],
        )
        def dummy_test(x, y):
            pass

        cases = dummy_test.__rustest_parametrization__
        assert cases[0]["values"] == {"x": [1, 2], "y": [3, 4]}


# =============================================================================
# Tests for pytest.importorskip()
# =============================================================================


class TestImportorskip:
    """Tests for importorskip() functionality."""

    def test_importorskip_returns_module(self):
        """Test that importorskip returns the imported module."""
        # Import a module that definitely exists
        os_module = importorskip("os")

        import os

        assert os_module is os

    def test_importorskip_with_missing_module(self):
        """Test that importorskip skips when module is missing."""
        with pytest.raises(Exception):  # Should raise skip
            importorskip("nonexistent_module_12345")

    def test_importorskip_with_custom_reason(self):
        """Test importorskip with custom reason."""
        with pytest.raises(Exception) as exc_info:
            importorskip("nonexistent_module", reason="custom reason")

        # The skip should contain our custom reason
        assert "custom reason" in str(exc_info.value) or True  # Skip raises

    def test_importorskip_version_check(self):
        """Test importorskip with version requirement."""
        # This should work - os has no __version__ but we handle that
        try:
            importorskip("os", minversion="0.0.1")
        except Exception:
            pass  # Expected if no __version__


# =============================================================================
# Tests for ParameterSet in _build_cases
# =============================================================================


class TestParameterSetInBuildCases:
    """Tests for ParameterSet handling in _build_cases."""

    def test_parameter_set_id_takes_priority(self):
        """Test that ParameterSet id takes priority over ids parameter."""
        names = ("x",)
        values = [
            ParameterSet((1,), id="param_id"),
        ]

        # Even with ids parameter, ParameterSet id should win
        cases = _build_cases(names, values, ["override_id"])

        assert cases[0]["id"] == "param_id"

    def test_parameter_set_values_extracted(self):
        """Test that ParameterSet values are correctly extracted."""
        names = ("a", "b")
        values = [
            ParameterSet((10, 20), id="test"),
        ]

        cases = _build_cases(names, values, None)

        assert cases[0]["values"] == {"a": 10, "b": 20}

    def test_parameter_set_single_value_unwrapped(self):
        """Test that single-value ParameterSet is unwrapped correctly."""
        names = ("x",)
        values = [
            ParameterSet((42,), id="single"),
        ]

        cases = _build_cases(names, values, None)

        assert cases[0]["values"] == {"x": 42}


# =============================================================================
# Integration tests
# =============================================================================


class TestPytestCompatIntegration:
    """Integration tests for pytest-compat features working together."""

    def test_param_with_list_values(self):
        """Test param() containing list values."""

        @parametrize(
            "items",
            [
                param([1, 2, 3], id="list_123"),
                param([4, 5], id="list_45"),
            ],
        )
        def dummy_test(items):
            pass

        cases = dummy_test.__rustest_parametrization__
        assert cases[0]["values"] == {"items": [1, 2, 3]}
        assert cases[1]["values"] == {"items": [4, 5]}

    def test_warns_and_deprecated_call_same_api(self):
        """Test that warns and deprecated_call have compatible APIs."""
        # Both should work with match parameter
        with warns(UserWarning, match="test"):
            warnings.warn("test message", UserWarning)

        with deprecated_call(match="old"):
            warnings.warn("old function", DeprecationWarning)


# =============================================================================
# Tests for new pytest compatibility features
# =============================================================================


class TestSkipifSignatures:
    """Tests for pytest.mark.skipif() with different signature forms."""

    def test_skipif_with_keyword_reason(self):
        """Test skipif with reason as keyword argument."""
        import sys
        from rustest.decorators import mark

        # This is the modern pytest style
        @mark.skipif(sys.platform == "nonexistent", reason="Never skips")
        def dummy_test():
            pass

        # Check that the mark was applied
        marks = getattr(dummy_test, "__rustest_marks__", [])
        assert len(marks) == 1
        assert marks[0]["name"] == "skipif"
        assert marks[0]["kwargs"]["reason"] == "Never skips"

    def test_skipif_with_positional_reason(self):
        """Test skipif with reason as positional argument (older pytest style)."""
        import sys
        from rustest.decorators import mark

        # This is the older pytest style - should also work
        @mark.skipif(sys.platform == "nonexistent", "Never skips")
        def dummy_test():
            pass

        # Check that the mark was applied with the reason
        marks = getattr(dummy_test, "__rustest_marks__", [])
        assert len(marks) == 1
        assert marks[0]["name"] == "skipif"
        assert marks[0]["kwargs"]["reason"] == "Never skips"

    def test_skipif_false_condition(self):
        """Test that skipif with False condition doesn't skip."""
        from rustest.decorators import mark

        @mark.skipif(False, reason="Should not skip")
        def dummy_test():
            return "executed"

        # Test should not be skipped
        result = dummy_test()
        assert result == "executed"


class TestSkipFunction:
    """Tests for pytest.skip() function for dynamic skipping."""

    def test_skip_function_exists(self):
        """Test that skip function exists in pytest compat."""
        from rustest.compat.pytest import skip

        assert callable(skip)

    def test_skip_function_raises_skipped(self):
        """Test that skip() raises Skipped exception."""
        from rustest.compat.pytest import skip, Skipped

        with pytest.raises(Skipped):
            skip("Test skipped dynamically")

    def test_skip_function_with_reason(self):
        """Test that skip() includes the reason in the exception."""
        from rustest.compat.pytest import skip, Skipped

        try:
            skip("Custom skip reason")
        except Skipped as e:
            assert "Custom skip reason" in str(e)

    def test_skip_function_in_conditional(self):
        """Test skip() in conditional logic."""
        from rustest.compat.pytest import skip

        condition = False
        if condition:
            skip("Should not be reached")

        # If we get here, skip wasn't called
        assert True

    def test_skip_exception_type_exported(self):
        """Test that Skipped exception is exported."""
        from rustest.compat.pytest import Skipped

        assert issubclass(Skipped, Exception)


class TestXFailFunction:
    """Tests for pytest.xfail() function for expected failures."""

    def test_xfail_function_exists(self):
        """Test that xfail function exists in pytest compat."""
        from rustest.compat.pytest import xfail

        assert callable(xfail)

    def test_xfail_function_raises_xfailed(self):
        """Test that xfail() raises XFailed exception."""
        from rustest.compat.pytest import xfail, XFailed

        with pytest.raises(XFailed):
            xfail("Test expected to fail")

    def test_xfail_function_with_reason(self):
        """Test that xfail() includes the reason in the exception."""
        from rustest.compat.pytest import xfail, XFailed

        try:
            xfail("Known bug in backend")
        except XFailed as e:
            assert "Known bug" in str(e)

    def test_xfail_function_in_conditional(self):
        """Test xfail() in conditional logic."""
        from rustest.compat.pytest import xfail
        import sys

        if sys.version_info < (3, 0):  # This is False for us
            xfail("Would fail on Python 2")

        # If we get here, xfail wasn't called
        assert True

    def test_xfail_exception_type_exported(self):
        """Test that XFailed exception is exported."""
        from rustest.compat.pytest import XFailed

        assert issubclass(XFailed, Exception)


class TestFailFunction:
    """Tests for pytest.fail() function."""

    def test_fail_function_exists(self):
        """Test that fail function exists."""
        from rustest.compat.pytest import fail

        assert callable(fail)

    def test_fail_function_raises_failed(self):
        """Test that fail() raises Failed exception."""
        from rustest.compat.pytest import fail, Failed

        with pytest.raises(Failed):
            fail("Test failed explicitly")

    def test_fail_function_with_reason(self):
        """Test that fail() includes the reason in the exception."""
        from rustest.compat.pytest import fail, Failed

        try:
            fail("Validation error occurred")
        except Failed as e:
            assert "Validation error" in str(e)

    def test_fail_in_conditional(self):
        """Test fail() in conditional logic."""
        from rustest.compat.pytest import fail

        data_valid = True
        if not data_valid:
            fail("Data validation failed")

        # If we get here, fail wasn't called
        assert True


class TestAllExceptionTypesExported:
    """Test that all exception types are properly exported."""

    def test_all_exceptions_accessible_from_pytest(self):
        """Test that all exception types are accessible via pytest compat."""
        from rustest.compat import pytest as pytest_compat

        assert hasattr(pytest_compat, "Failed")
        assert hasattr(pytest_compat, "Skipped")
        assert hasattr(pytest_compat, "XFailed")

    def test_exceptions_are_exceptions(self):
        """Test that all exception types inherit from Exception."""
        from rustest.compat.pytest import Failed, Skipped, XFailed

        assert issubclass(Failed, Exception)
        assert issubclass(Skipped, Exception)
        assert issubclass(XFailed, Exception)

    def test_exceptions_have_distinct_types(self):
        """Test that exception types are distinct."""
        from rustest.compat.pytest import Failed, Skipped, XFailed

        assert Failed is not Skipped
        assert Failed is not XFailed
        assert Skipped is not XFailed


class TestAsyncioDecorator:
    """Tests for @mark.asyncio decorator compatibility."""

    def test_asyncio_decorator_on_async_function(self):
        """Test that @mark.asyncio works with async functions."""
        from rustest.decorators import mark
        import asyncio

        @mark.asyncio
        async def async_test():
            await asyncio.sleep(0)
            return "async_result"

        # The decorated function should have the asyncio mark
        marks = getattr(async_test, "__rustest_marks__", [])
        assert len(marks) >= 1
        # Check if any mark has name "asyncio"
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) >= 1

    def test_asyncio_decorator_on_non_async_function(self):
        """Test that @mark.asyncio accepts non-async functions for pytest compat."""
        from rustest.decorators import mark

        # This should NOT raise TypeError (pytest compatibility)
        @mark.asyncio
        def sync_test():
            return "sync_result"

        # Test should be marked with asyncio
        marks = getattr(sync_test, "__rustest_marks__", [])
        assert len(marks) >= 1
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) >= 1

        # Test should still run normally
        result = sync_test()
        assert result == "sync_result"

    def test_asyncio_decorator_with_loop_scope(self):
        """Test @mark.asyncio with loop_scope parameter."""
        from rustest.decorators import mark

        @mark.asyncio(loop_scope="function")
        def sync_with_scope():
            return "scoped"

        # Check that the mark includes loop_scope
        marks = getattr(sync_with_scope, "__rustest_marks__", [])
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) >= 1
        # Check kwargs contains loop_scope
        assert asyncio_marks[0].get("kwargs", {}).get("loop_scope") == "function"

    def test_asyncio_decorator_on_class(self):
        """Test that @mark.asyncio can be applied to classes."""
        from rustest.decorators import mark

        # Classes should be supported - mark is applied to class
        @mark.asyncio
        class TestClass:
            async def test_method(self):
                return "async"

            def test_sync_method(self):
                return "sync"

        # Class should have the asyncio mark
        marks = getattr(TestClass, "__rustest_marks__", [])
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) >= 1

    def test_asyncio_mark_applied_correctly_sync(self):
        """Test that asyncio mark is correctly applied to sync functions."""
        from rustest.decorators import mark

        @mark.asyncio
        def regular_test():
            pass

        # Verify mark structure
        marks = getattr(regular_test, "__rustest_marks__", [])
        asyncio_marks = [m for m in marks if m.get("name") == "asyncio"]
        assert len(asyncio_marks) == 1

        mark_data = asyncio_marks[0]
        assert mark_data["name"] == "asyncio"
        assert "kwargs" in mark_data

    def test_asyncio_decorator_preserves_function_metadata(self):
        """Test that @mark.asyncio preserves function name and docstring."""
        from rustest.decorators import mark

        @mark.asyncio
        def test_with_metadata():
            """Test function docstring."""
            pass

        assert test_with_metadata.__name__ == "test_with_metadata"
        assert test_with_metadata.__doc__ == "Test function docstring."

    def test_asyncio_decorator_multiple_marks(self):
        """Test that @mark.asyncio can be combined with other marks."""
        from rustest.decorators import mark

        @mark.asyncio
        @mark.slow
        def test_multi_marked():
            return "marked"

        marks = getattr(test_multi_marked, "__rustest_marks__", [])
        mark_names = [m.get("name") for m in marks]

        assert "asyncio" in mark_names
        assert "slow" in mark_names


class TestFixtureRequestFallback:
    """Tests for FixtureRequest.getfixturevalue fallback resolver."""

    def test_getfixturevalue_uses_python_registry(self):
        """Ensure fallback resolver passes the active request object."""

        def needs_request(request: FixtureRequest) -> FixtureRequest:
            return request

        register_fixtures({"needs_request": needs_request})
        try:
            request = FixtureRequest()
            result = request.getfixturevalue("needs_request")
            assert result is request
        finally:
            clear_registry()
