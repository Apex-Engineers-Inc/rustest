"""Tests for runtime configuration and rustestconfig fixture."""

from pathlib import Path

from rustest.core import run


def test_rustestconfig_reflects_verbose_flag(tmp_path: Path) -> None:
    """Test that rustestconfig.getoption('verbose') reflects actual CLI flag."""
    test_file = tmp_path / "test_verbose.py"
    test_file.write_text(
        """
def test_verbose_check(rustestconfig):
    verbose = rustestconfig.getoption("verbose", default=0)
    # When running with verbose=True in core.run(), expect verbose=1
    assert verbose == 1, f"Expected verbose=1, got {verbose}"
"""
    )

    report = run(paths=[str(tmp_path)], verbose=True)
    assert report.passed == 1, f"Test should pass with verbose flag. Report: {report}"


def test_rustestconfig_reflects_capture_disabled(tmp_path: Path) -> None:
    """Test that rustestconfig.getoption('capture') reflects --no-capture."""
    test_file = tmp_path / "test_capture.py"
    test_file.write_text(
        """
def test_capture_check(rustestconfig):
    capture = rustestconfig.getoption("capture", default="fd")
    # When running with capture_output=False, expect capture="no"
    assert capture == "no", f"Expected capture='no', got {capture}"
"""
    )

    report = run(paths=[str(tmp_path)], capture_output=False)
    assert report.passed == 1, "Test should pass with capture_output=False"


def test_rustestconfig_reflects_default_verbose(tmp_path: Path) -> None:
    """Test that rustestconfig.getoption('verbose') defaults to 0."""
    test_file = tmp_path / "test_verbose_default.py"
    test_file.write_text(
        """
def test_verbose_default(rustestconfig):
    verbose = rustestconfig.getoption("verbose", default=0)
    # When running without verbose flag, expect verbose=0
    assert verbose == 0, f"Expected verbose=0, got {verbose}"
"""
    )

    report = run(paths=[str(tmp_path)], verbose=False)
    assert report.passed == 1


def test_rustestconfig_option_namespace(tmp_path: Path) -> None:
    """Test accessing options via rustestconfig.option namespace."""
    test_file = tmp_path / "test_option_namespace.py"
    test_file.write_text(
        """
def test_option_access(rustestconfig):
    # Access via option namespace
    verbose = rustestconfig.option.verbose
    assert verbose == 1, f"Expected option.verbose=1, got {verbose}"

    # Access via getoption
    capture = rustestconfig.getoption("capture")
    assert capture in ["fd", "no"], f"Unexpected capture value: {capture}"
"""
    )

    report = run(paths=[str(tmp_path)], verbose=True)
    assert report.passed == 1


def test_rustestconfig_assertmode(tmp_path: Path) -> None:
    """Test that assertmode is always 'rewrite' in rustest."""
    test_file = tmp_path / "test_assertmode.py"
    test_file.write_text(
        """
def test_assertmode_rewrite(rustestconfig):
    mode = rustestconfig.getoption("assertmode", default="rewrite")
    assert mode == "rewrite", f"Expected assertmode='rewrite', got {mode}"
"""
    )

    report = run(paths=[str(tmp_path)])
    assert report.passed == 1


def test_rustestconfig_workers_option(tmp_path: Path) -> None:
    """Test that workers option is accessible."""
    test_file = tmp_path / "test_workers.py"
    test_file.write_text(
        """
def test_workers_option(rustestconfig):
    workers = rustestconfig.getoption("workers", default=None)
    # When workers=4 is set, it should be accessible
    assert workers == 4, f"Expected workers=4, got {workers}"
"""
    )

    report = run(paths=[str(tmp_path)], workers=4)
    assert report.passed == 1


def test_pytestconfig_in_compat_mode(tmp_path: Path) -> None:
    """Test that pytestconfig works in pytest-compat mode."""
    test_file = tmp_path / "test_pytestconfig_compat.py"
    test_file.write_text(
        """
def test_pytestconfig_available(pytestconfig):
    verbose = pytestconfig.getoption("verbose", default=0)
    assert verbose == 1

    # Should have same data as rustestconfig
    capture = pytestconfig.getoption("capture")
    assert capture in ["fd", "no"]
"""
    )

    report = run(paths=[str(tmp_path)], pytest_compat=True, verbose=True)
    assert report.passed == 1


def test_pytestconfig_error_without_compat_mode(tmp_path: Path) -> None:
    """Test that pytestconfig raises error without --pytest-compat."""
    test_file = tmp_path / "test_pytestconfig_error.py"
    test_file.write_text(
        """
def test_pytestconfig_should_fail(pytestconfig):
    # This should raise RuntimeError
    pass
"""
    )

    report = run(paths=[str(tmp_path)], pytest_compat=False)
    # Should fail with RuntimeError
    assert report.failed == 1
    assert report.passed == 0
