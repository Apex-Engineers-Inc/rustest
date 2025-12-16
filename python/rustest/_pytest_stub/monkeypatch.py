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
    def setattr(
        self, target: object, name: str, value: object = None, raising: bool = True
    ) -> None:
        """Stub for setattr method."""
        ...

    def delattr(self, target: object, name: str = "", raising: bool = True) -> None:
        """Stub for delattr method."""
        ...

    def setitem(self, dic: dict, name: str, value: object) -> None:
        """Stub for setitem method."""
        ...

    def delitem(self, dic: dict, name: str, raising: bool = True) -> None:
        """Stub for delitem method."""
        ...

    def setenv(self, name: str, value: str, prepend: str | None = None) -> None:
        """Stub for setenv method."""
        ...

    def delenv(self, name: str, raising: bool = True) -> None:
        """Stub for delenv method."""
        ...

    def syspath_prepend(self, path: str) -> None:
        """Stub for syspath_prepend method."""
        ...

    def chdir(self, path: str) -> None:
        """Stub for chdir method."""
        ...

    def context(self) -> object:
        """Stub for context method."""
        ...

    def undo(self) -> None:
        """Stub for undo method."""
        ...
