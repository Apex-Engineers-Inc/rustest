"""unittest.SkipTest raised from tests or fixtures is reported as skipped."""

from __future__ import annotations

from pathlib import Path

from rustest import run

from .helpers import ensure_rust_stub

ensure_rust_stub()


def _run_source(tmp_path: Path, source: str):
    (tmp_path / "test_sample.py").write_text(source)
    report = run(paths=[str(tmp_path)])
    assert report.total == 1
    return next(iter(report.results))


def test_skiptest_in_test_body_is_skipped_with_reason(tmp_path: Path) -> None:
    result = _run_source(
        tmp_path,
        """
import unittest

def test_skips():
    raise unittest.SkipTest("not applicable here")
""",
    )
    assert result.status == "skipped"
    assert result.message == "not applicable here"


def test_skiptest_subclass_is_skipped_with_reason(tmp_path: Path) -> None:
    result = _run_source(
        tmp_path,
        """
import unittest

class FeatureMissing(unittest.SkipTest):
    pass

def test_skips():
    raise FeatureMissing("feature missing")
""",
    )
    assert result.status == "skipped"
    assert result.message == "feature missing"


def test_skiptest_raised_from_fixture_is_skipped(tmp_path: Path) -> None:
    result = _run_source(
        tmp_path,
        """
import unittest
from rustest import fixture

@fixture
def needs_feature():
    raise unittest.SkipTest("fixture says no")

def test_uses_fixture(needs_feature):
    assert False, "body must not run"
""",
    )
    assert result.status == "skipped"
    assert result.message == "fixture says no"


def test_failure_mentioning_skiptest_is_still_a_failure(tmp_path: Path) -> None:
    result = _run_source(
        tmp_path,
        """
def test_fails():
    raise RuntimeError("unittest.case.SkipTest: this is not a skip")
""",
    )
    assert result.status == "failed"
