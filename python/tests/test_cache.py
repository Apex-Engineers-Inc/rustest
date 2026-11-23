"""``cache`` behaviour not pinned elsewhere, against the class the fixture really returns.

The store's layout and its failure modes are in ``test_builtins.py``; the fixture end to end
is in ``test_builtin_fixtures.py``.
"""

from __future__ import annotations

from pathlib import Path

from rustest import Cache


def test_a_value_survives_into_a_new_cache_object(tmp_path: Path) -> None:
    """What makes it a cache *between runs*: a fresh object over the same directory reads it."""
    Cache(tmp_path).set("run/count", 3)
    assert Cache(tmp_path).get("run/count", None) == 3


def test_mkdir_is_idempotent(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    assert cache.mkdir("plugin") == cache.mkdir("plugin")
