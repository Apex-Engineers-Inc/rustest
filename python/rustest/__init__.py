"""Public Python API for rustest."""

from __future__ import annotations

from . import _decorators
from ._approx import approx
from ._cli import main
from ._reporting import RunReport, TestResult
from .core import run

fixture = _decorators.fixture
mark = _decorators.mark
parametrize = _decorators.parametrize
raises = _decorators.raises
skip = _decorators.skip

__all__ = [
    "RunReport",
    "TestResult",
    "approx",
    "fixture",
    "main",
    "mark",
    "parametrize",
    "raises",
    "run",
    "skip",
]
