""":class:`MonkeyPatch`, the class behind the ``monkeypatch`` fixture.

The fixture itself, and every other builtin fixture, is registered by
:mod:`rustest._builtins`; this module holds only the class, which is public API
(``rustest.MonkeyPatch``, ``pytest.MonkeyPatch``).
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
import warnings
from collections.abc import Generator, MutableMapping
from contextlib import contextmanager
from typing import Any


class Notset:
    """Sentinel value for tracking missing attributes/items."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<notset>"


notset = Notset()


def _annotated_getattr(obj: object, name: str, ann: str) -> object:
    """Port of `_pytest/monkeypatch.py::annotated_getattr` (pytest 8.4.2, l. 88-95)."""
    try:
        obj = getattr(obj, name)
    except AttributeError as exc:
        raise AttributeError(
            f"{type(obj).__name__!r} object at {ann} has no attribute {name!r}"
        ) from exc
    return obj


def _resolve(name: str) -> object:
    """Port of `_pytest/monkeypatch.py::resolve` (pytest 8.4.2, l. 60-85).

    The point of the walk — and the reason a single ``rsplit(".", 1)`` +
    ``import_module`` is wrong — is that *the longest importable prefix* is imported and
    everything after it is reached with ``getattr``.  ``click.shell_completion.BashComplete``
    is a class inside a module, so the naive split asks for a module
    ``click.shell_completion.BashComplete`` that does not exist and dies with
    ``ModuleNotFoundError``; this walks ``click`` -> ``shell_completion`` -> ``BashComplete``
    instead.

    ``getattr`` is tried *first* at each step, which is what makes ``os.path.join``
    resolve without importing ``os.path.join`` as a module, and the ``expected == used``
    test is what keeps a genuinely missing top-level module reporting its own
    ``ModuleNotFoundError`` while a missing intermediate segment is re-raised wrapped.
    """
    parts = name.split(".")

    used = parts.pop(0)
    found: object = __import__(used)
    for part in parts:
        used += "." + part
        try:
            found = getattr(found, part)
        except AttributeError:
            pass
        else:
            continue
        # Un-nested exactly as pytest un-nests it, to avoid a chained exception.
        try:
            __import__(used)
        except ImportError as exc:
            expected = str(exc).split()[-1]
            if expected == used:
                raise
            raise ImportError(f"import error in {used}: {exc}") from exc
        found = _annotated_getattr(found, part, used)
    return found


def _derive_importpath(import_path: object, raising: bool) -> tuple[str, object]:
    """Port of `_pytest/monkeypatch.py::derive_importpath` (pytest 8.4.2, l. 98-105)."""
    if not isinstance(import_path, str) or "." not in import_path:
        raise TypeError(f"must be absolute import path string, not {import_path!r}")
    module, attr = import_path.rsplit(".", 1)
    target = _resolve(module)
    if raising:
        _ = _annotated_getattr(target, attr, ann=module)
    return attr, target


class MonkeyPatch:
    """Lightweight re-implementation of :class:`pytest.MonkeyPatch`."""

    def __init__(self) -> None:
        super().__init__()
        self._setattr: list[tuple[object, str, object | Notset]] = []
        self._setitem: list[tuple[MutableMapping[Any, Any], Any, object | Notset]] = []
        self._savesyspath: list[str] | None = None
        self._cwd_original: str | None = None

    @classmethod
    @contextmanager
    def context(cls) -> Generator[MonkeyPatch, None, None]:
        patch = cls()
        try:
            yield patch
        finally:
            patch.undo()

    def setattr(
        self,
        target: object | str,
        name: object | str = notset,
        value: object = notset,
        raising: bool = True,
    ) -> None:
        """Port of `_pytest/monkeypatch.py::MonkeyPatch.setattr` (pytest 8.4.2, l. 181-251).

        ``raising`` is positional-or-keyword because pytest's is: ``setattr(obj, "x", 1,
        False)`` is a shape real suites use, and a keyword-only parameter turned it into
        ``TypeError: takes from 2 to 4 positional arguments but 5 were given``.
        """
        if value is notset:
            if not isinstance(target, str):
                raise TypeError(
                    "use setattr(target, name, value) or "
                    + "setattr(target, value) with target being a dotted import string"
                )
            value = name
            name, target = _derive_importpath(target, raising)
        elif not isinstance(name, str):
            raise TypeError(
                "use setattr(target, name, value) with name being a string or "
                + "setattr(target, value) with target being a dotted import string"
            )

        original = getattr(target, name, notset)
        if raising and original is notset:
            raise AttributeError(f"{target!r} has no attribute {name!r}")

        # Avoid class descriptors like staticmethod/classmethod: `getattr` on a class
        # returns the *bound* object, and restoring that would silently turn a
        # `staticmethod` into a plain function (pytest l. 247-249).
        if inspect.isclass(target):
            original = target.__dict__.get(name, notset)
        self._setattr.append((target, name, original))
        setattr(target, name, value)

    def delattr(
        self,
        target: object | str,
        name: str | Notset = notset,
        raising: bool = True,
    ) -> None:
        """Port of `_pytest/monkeypatch.py::MonkeyPatch.delattr` (pytest 8.4.2, l. 253-289)."""
        if isinstance(name, Notset):
            if not isinstance(target, str):
                raise TypeError(
                    "use delattr(target, name) or "
                    + "delattr(target) with target being a dotted import string"
                )
            name, target = _derive_importpath(target, raising)

        if not hasattr(target, name):
            if raising:
                raise AttributeError(name)
            return

        original = getattr(target, name, notset)
        if inspect.isclass(target):
            original = target.__dict__.get(name, notset)
        self._setattr.append((target, name, original))
        delattr(target, name)

    def setitem(self, dic: MutableMapping[Any, Any], name: Any, value: Any) -> None:
        self._setitem.append((dic, name, dic.get(name, notset)))
        dic[name] = value

    def delitem(self, dic: MutableMapping[Any, Any], name: Any, raising: bool = True) -> None:
        if name not in dic:
            if raising:
                raise KeyError(name)
        else:
            self._setitem.append((dic, name, dic.get(name, notset)))
            del dic[name]

    def setenv(self, name: str, value: str, prepend: str | None = None) -> None:
        """Port of `MonkeyPatch.setenv`: an environment change is a ``setitem`` on
        ``os.environ``, so it is undone from the same list, in the same order, as pytest's."""
        if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            from rustest.compat.pytest import PytestWarning

            warnings.warn(
                PytestWarning(
                    f"Value of environment variable {name} type should be str, but got "
                    + f"{value!r} (type: {type(value).__name__}); converted to str implicitly"
                ),
                stacklevel=2,
            )
            value = str(value)
        if prepend and name in os.environ:
            value = value + prepend + os.environ[name]
        self.setitem(os.environ, name, value)

    def delenv(self, name: str, raising: bool = True) -> None:
        self.delitem(os.environ, name, raising=raising)

    def syspath_prepend(self, path: os.PathLike[str] | str) -> None:
        """Port of `MonkeyPatch.syspath_prepend`. The undo restores the **whole** of
        ``sys.path`` from this snapshot, so entries the code under test inserted go too:
        flask's suite relies on that to forget an app directory between tests."""
        if self._savesyspath is None:
            self._savesyspath = sys.path[:]
        sys.path.insert(0, os.fspath(path))
        importlib.invalidate_caches()

    def chdir(self, path: os.PathLike[str] | str) -> None:
        if self._cwd_original is None:
            self._cwd_original = os.getcwd()
        os.chdir(os.fspath(path))

    def undo(self) -> None:
        """Reverse every recorded change, newest first.

        Port of `_pytest/monkeypatch.py::MonkeyPatch.undo` (l. 322-350).

        **One deliberate softening, and it is the `delattr` branch.** pytest calls
        ``delattr(obj, name)`` bare: an attribute that was absent when it was patched and has
        since been *removed by the test itself* makes pytest's undo raise ``AttributeError``.
        This swallows that, because the alternative is a teardown error attributed to the
        fixture rather than to the test that deleted the attribute, and the state pytest and
        rustest end in is identical either way -- the attribute is gone, which is what undo
        was asked to arrange. Noted rather than silently different: a suite that *relies* on
        the raise (nothing plausibly does) would see a green teardown here.
        """
        for obj, attr_name, original in reversed(self._setattr):
            if original is notset:
                try:
                    delattr(obj, attr_name)
                except AttributeError:  # the test already removed it; see the docstring
                    pass
            else:
                setattr(obj, attr_name, original)
        self._setattr.clear()

        for mapping, key, original in reversed(self._setitem):
            if original is notset:
                mapping.pop(key, None)
            else:
                mapping[key] = original
        self._setitem.clear()

        if self._savesyspath is not None:
            sys.path[:] = self._savesyspath
            self._savesyspath = None

        if self._cwd_original is not None:
            os.chdir(self._cwd_original)
            self._cwd_original = None


__all__ = ["MonkeyPatch"]
