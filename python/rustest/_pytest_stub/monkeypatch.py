"""
Stub for _pytest.monkeypatch

Provides the real MonkeyPatch class from rustest.builtin_fixtures
so that ``from _pytest.monkeypatch import MonkeyPatch`` works in
--pytest-compat mode.
"""

from rustest.builtin_fixtures import MonkeyPatch

__all__ = ["MonkeyPatch"]
