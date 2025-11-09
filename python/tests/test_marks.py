"""Comprehensive tests for marks support."""

import sys
from rustest import mark


def test_basic_mark_decorator():
    """Test basic mark decorator application."""

    @mark.slow
    def test_function():
        pass

    assert hasattr(test_function, "__rustest_marks__")
    marks = test_function.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "slow"
    assert marks[0]["args"] == ()
    assert marks[0]["kwargs"] == {}


def test_mark_with_arguments():
    """Test mark decorator with arguments."""

    @mark.timeout(seconds=30)
    def test_function():
        pass

    marks = test_function.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "timeout"
    assert marks[0]["kwargs"] == {"seconds": 30}


def test_multiple_marks():
    """Test applying multiple marks to a single test."""

    @mark.slow
    @mark.integration
    @mark.requires_db
    def test_function():
        pass

    marks = test_function.__rustest_marks__
    assert len(marks) == 3
    mark_names = [m["name"] for m in marks]
    assert "slow" in mark_names
    assert "integration" in mark_names
    assert "requires_db" in mark_names


def test_skipif_mark():
    """Test skipif mark with condition and reason."""

    @mark.skipif(sys.platform == "win32", reason="Not supported on Windows")
    def test_unix_only():
        pass

    marks = test_unix_only.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "skipif"
    assert len(marks[0]["args"]) == 1
    assert marks[0]["kwargs"]["reason"] == "Not supported on Windows"


def test_skipif_mark_false_condition():
    """Test skipif mark with false condition."""

    @mark.skipif(False, reason="Never skip")
    def test_always_run():
        pass

    marks = test_always_run.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "skipif"
    assert marks[0]["args"][0] is False


def test_xfail_mark_no_condition():
    """Test xfail mark without condition."""

    @mark.xfail(reason="Known bug")
    def test_expected_failure():
        assert False

    marks = test_expected_failure.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "xfail"
    assert marks[0]["args"] == ()
    assert marks[0]["kwargs"]["reason"] == "Known bug"
    assert marks[0]["kwargs"]["strict"] is False
    assert marks[0]["kwargs"]["run"] is True


def test_xfail_mark_with_condition():
    """Test xfail mark with condition."""

    @mark.xfail(sys.version_info < (3, 10), reason="Requires Python 3.10+")
    def test_new_feature():
        pass

    marks = test_new_feature.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "xfail"
    assert len(marks[0]["args"]) == 1
    assert marks[0]["kwargs"]["reason"] == "Requires Python 3.10+"


def test_xfail_mark_strict_mode():
    """Test xfail mark with strict mode."""

    @mark.xfail(reason="Must fail", strict=True)
    def test_strict_xfail():
        pass

    marks = test_strict_xfail.__rustest_marks__
    assert marks[0]["kwargs"]["strict"] is True


def test_xfail_mark_with_raises():
    """Test xfail mark with expected exception."""

    @mark.xfail(reason="Raises ValueError", raises=ValueError)
    def test_expected_exception():
        raise ValueError("Expected")

    marks = test_expected_exception.__rustest_marks__
    assert marks[0]["kwargs"]["raises"] is ValueError


def test_usefixtures_mark():
    """Test usefixtures mark."""

    @mark.usefixtures("db_session", "temp_dir")
    def test_with_fixtures():
        pass

    marks = test_with_fixtures.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "usefixtures"
    assert marks[0]["args"] == ("db_session", "temp_dir")


def test_usefixtures_single_fixture():
    """Test usefixtures mark with single fixture."""

    @mark.usefixtures("cleanup")
    def test_with_cleanup():
        pass

    marks = test_with_cleanup.__rustest_marks__
    assert marks[0]["args"] == ("cleanup",)


def test_combining_standard_and_custom_marks():
    """Test combining standard marks with custom marks."""

    @mark.slow
    @mark.skipif(sys.platform == "darwin", reason="macOS only")
    @mark.integration
    @mark.xfail(reason="Known issue")
    def test_complex():
        pass

    marks = test_complex.__rustest_marks__
    assert len(marks) == 4
    mark_names = [m["name"] for m in marks]
    assert "slow" in mark_names
    assert "skipif" in mark_names
    assert "integration" in mark_names
    assert "xfail" in mark_names


def test_mark_on_test_class():
    """Test applying marks to test classes."""

    @mark.integration
    @mark.slow
    class TestSuite:
        def test_one(self):
            pass

        def test_two(self):
            pass

    # Marks should be on the class
    assert hasattr(TestSuite, "__rustest_marks__")
    marks = TestSuite.__rustest_marks__
    assert len(marks) == 2
    mark_names = [m["name"] for m in marks]
    assert "integration" in mark_names
    assert "slow" in mark_names


def test_mark_inheritance_on_methods():
    """Test that marks on individual methods work correctly."""

    class TestMethods:
        @mark.slow
        def test_slow_method(self):
            pass

        @mark.fast
        def test_fast_method(self):
            pass

    # Each method should have its own marks
    slow_marks = TestMethods.test_slow_method.__rustest_marks__
    fast_marks = TestMethods.test_fast_method.__rustest_marks__

    assert len(slow_marks) == 1
    assert slow_marks[0]["name"] == "slow"

    assert len(fast_marks) == 1
    assert fast_marks[0]["name"] == "fast"


def test_mark_with_positional_and_keyword_args():
    """Test marks with both positional and keyword arguments."""

    @mark.parametrize("x", [1, 2, 3])
    @mark.custom("arg1", "arg2", key1="value1", key2="value2")
    def test_mixed_args():
        pass

    marks = test_mixed_args.__rustest_marks__
    custom_mark = next(m for m in marks if m["name"] == "custom")
    assert custom_mark["args"] == ("arg1", "arg2")
    assert custom_mark["kwargs"] == {"key1": "value1", "key2": "value2"}


def test_xfail_default_values():
    """Test that xfail mark has correct default values."""

    @mark.xfail()
    def test_defaults():
        pass

    marks = test_defaults.__rustest_marks__
    assert marks[0]["kwargs"]["reason"] is None
    assert marks[0]["kwargs"]["raises"] is None
    assert marks[0]["kwargs"]["run"] is True
    assert marks[0]["kwargs"]["strict"] is False


def test_skipif_with_string_condition():
    """Test skipif with string condition (for evaluation)."""

    @mark.skipif("sys.platform == 'win32'", reason="Windows not supported")
    def test_string_condition():
        pass

    marks = test_string_condition.__rustest_marks__
    assert marks[0]["args"][0] == "sys.platform == 'win32'"


# Integration tests with parametrize
def test_marks_with_parametrize():
    """Test that marks work together with parametrize."""
    from rustest import parametrize

    @mark.slow
    @parametrize("x", [1, 2, 3])
    def test_parametrized_marked(x):
        assert x > 0

    # Should have both marks and parametrization
    assert hasattr(test_parametrized_marked, "__rustest_marks__")
    assert hasattr(test_parametrized_marked, "__rustest_parametrization__")

    marks = test_parametrized_marked.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "slow"


# Integration tests with fixtures
def test_marks_with_fixtures():
    """Test that marks work with fixtures."""
    from rustest import fixture

    @fixture
    def sample_fixture():
        return 42

    @mark.integration
    def test_with_fixture(sample_fixture):
        assert sample_fixture == 42

    marks = test_with_fixture.__rustest_marks__
    assert len(marks) == 1
    assert marks[0]["name"] == "integration"


# Edge cases
def test_empty_usefixtures():
    """Test usefixtures with no arguments raises or handles gracefully."""

    # In pytest, this would be allowed but pointless
    # Our implementation should handle it gracefully
    @mark.usefixtures()
    def test_no_fixtures():
        pass

    marks = test_no_fixtures.__rustest_marks__
    assert marks[0]["args"] == ()


def test_mark_repr():
    """Test string representation of MarkDecorator."""
    from rustest.decorators import MarkDecorator

    decorator = MarkDecorator("test_mark", ("arg1",), {"key": "value"})
    repr_str = repr(decorator)
    assert "test_mark" in repr_str
    assert "arg1" in repr_str
