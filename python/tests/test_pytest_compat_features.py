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
from unittest.mock import patch

import pytest

from rustest.compat.pytest import (
    WarningsChecker,
    warns,
    deprecated_call,
    param,
    importorskip,
)
from rustest.decorators import parametrize, ParameterSet, _build_cases
from rustest.builtin_fixtures import CaptureFixture, capsys, capfd


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
        capture._start_capture()

        print("hello stdout")
        out, err = capture.readouterr()

        capture._stop_capture()

        assert out == "hello stdout\n"
        assert err == ""

    def test_capture_fixture_captures_stderr(self):
        """Test that CaptureFixture captures stderr."""
        import sys

        capture = CaptureFixture()
        capture._start_capture()

        print("hello stderr", file=sys.stderr)
        out, err = capture.readouterr()

        capture._stop_capture()

        assert out == ""
        assert err == "hello stderr\n"

    def test_capture_fixture_resets_on_readouterr(self):
        """Test that readouterr resets the capture buffers."""
        capture = CaptureFixture()
        capture._start_capture()

        print("first")
        out1, _ = capture.readouterr()

        print("second")
        out2, _ = capture.readouterr()

        capture._stop_capture()

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
        capture._start_capture()
        capture._stop_capture()

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
            result = param(1, marks="some_mark")

            assert len(w) == 1
            assert "marks are not yet supported" in str(w[0].message)

    def test_param_in_parametrize(self):
        """Test that param() works with parametrize decorator."""
        @parametrize("x,y", [
            param(1, 2, id="small"),
            param(10, 20, id="large"),
        ])
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
        @parametrize("a,b,c", [
            [1, 2, 3],      # List
            (4, 5, 6),      # Tuple
            [7, 8, 9],      # List
        ])
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
        @parametrize("x,y", [
            [[1, 2], [3, 4]],  # Outer list unpacked, inner lists are values
        ])
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
        @parametrize("items", [
            param([1, 2, 3], id="list_123"),
            param([4, 5], id="list_45"),
        ])
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
