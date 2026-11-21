"""Stub for ``_pytest.monkeypatch`` -- **aliases**, like :mod:`rustest._pytest_stub.outcomes`.

``from _pytest.monkeypatch import MonkeyPatch`` must be the class the ``monkeypatch`` fixture
hands out, and ``notset`` the sentinel its undo lists hold: flask's suite appends
``(os.environ, key, notset)`` to ``monkeypatch._setitem`` directly.
"""

from rustest.builtin_fixtures import MonkeyPatch, Notset, notset

__all__ = ["MonkeyPatch", "Notset", "notset"]
