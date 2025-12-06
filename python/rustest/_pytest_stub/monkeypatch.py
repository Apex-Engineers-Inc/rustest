"""
Stub for _pytest.monkeypatch

MIGRATION GUIDE:
Instead of:
    from _pytest.monkeypatch import MonkeyPatch

Use the monkeypatch fixture:
    def test_example(monkeypatch):
        monkeypatch.setattr(...)

Or with rustest:
    from rustest import fixture

    @fixture
    def my_fixture(monkeypatch):
        monkeypatch.setattr(...)
"""

import warnings


class MonkeyPatch:
    """
    Stub MonkeyPatch class - primarily for type hints.

    DO NOT instantiate directly. Use the 'monkeypatch' fixture instead:

        def test_example(monkeypatch):
            monkeypatch.setattr(obj, 'attr', value)

    rustest provides the monkeypatch fixture just like pytest.
    """

    def __init__(self):
        warnings.warn(
            "Direct MonkeyPatch() instantiation is not supported in rustest.\n"
            "Use the 'monkeypatch' fixture instead:\n"
            "  def test_example(monkeypatch):\n"
            "      monkeypatch.setattr(...)\n"
            "\n"
            "For migration help, see: https://github.com/anthropics/rustest",
            DeprecationWarning,
            stacklevel=2,
        )
        raise NotImplementedError(
            "MonkeyPatch() cannot be instantiated directly. "
            "Use the 'monkeypatch' fixture provided by rustest."
        )

    # Provide method stubs for type checking/IDE autocomplete
    def setattr(self, target, name, value=..., raising=True): ...
    def delattr(self, target, name=..., raising=True): ...
    def setitem(self, dic, name, value): ...
    def delitem(self, dic, name, raising=True): ...
    def setenv(self, name, value, prepend=None): ...
    def delenv(self, name, raising=True): ...
    def syspath_prepend(self, path): ...
    def chdir(self, path): ...
    def context(self): ...
    def undo(self): ...
