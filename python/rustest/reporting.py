"""Utilities for converting raw results from the Rust layer."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from . import rust


@dataclass(slots=True)
class TestResult:
    """Structured view of a single test outcome."""

    __test__ = False  # Tell pytest this is not a test class

    name: str
    path: str
    status: str
    duration: float
    message: str | None
    stdout: str | None
    stderr: str | None

    @classmethod
    def from_py(cls, result: rust.PyTestResult) -> "TestResult":
        return cls(
            name=result.name,
            path=result.path,
            status=result.status,
            duration=result.duration,
            message=result.message,
            stdout=result.stdout,
            stderr=result.stderr,
        )


@dataclass(slots=True)
class CollectionError:
    """Error that occurred during test collection (e.g., syntax error, import error)."""

    path: str
    message: str

    @classmethod
    def from_py(cls, error: rust.CollectionError) -> "CollectionError":
        return cls(
            path=error.path,
            message=error.message,
        )


@dataclass(slots=True)
class RunReport:
    """Aggregate statistics for an entire test session."""

    total: int
    passed: int
    failed: int
    skipped: int
    duration: float
    results: tuple[TestResult, ...]
    collection_errors: tuple[CollectionError, ...]

    @classmethod
    def from_py(cls, report: rust.PyRunReport) -> "RunReport":
        return cls(
            total=report.total,
            passed=report.passed,
            failed=report.failed,
            skipped=report.skipped,
            duration=report.duration,
            results=tuple(TestResult.from_py(result) for result in report.results),
            collection_errors=tuple(
                CollectionError.from_py(error) for error in report.collection_errors
            ),
        )

    def iter_status(self, status: str) -> Iterable[TestResult]:
        """Yield results with the requested status."""

        return (result for result in self.results if result.status == status)
