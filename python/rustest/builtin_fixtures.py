"""Builtin fixtures that mirror a subset of pytest's default fixtures."""

from __future__ import annotations

import importlib
import itertools
import os
import shutil
import sys
import tempfile
from collections.abc import Generator, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Iterator, cast

from .decorators import fixture

py: ModuleType | None
try:  # pragma: no cover - optional dependency at runtime
    import py as _py_module
except Exception:  # pragma: no cover - import error reported at fixture usage time
    py = None
else:
    py = _py_module

if TYPE_CHECKING:
    try:  # pragma: no cover - typing-only import
        from py import path as _py_path
    except ImportError:
        PyPathLocal = Any
    else:
        PyPathLocal = _py_path.local

else:  # pragma: no cover - imported only for typing
    PyPathLocal = Any


class _NotSet:
    """Sentinel value for tracking missing attributes/items."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<NOTSET>"


_NOT_SET = _NotSet()


class MonkeyPatch:
    """Lightweight re-implementation of :class:`pytest.MonkeyPatch`."""

    def __init__(self) -> None:
        super().__init__()
        self._setattrs: list[tuple[object, str, object | _NotSet]] = []
        self._setitems: list[tuple[MutableMapping[Any, Any], Any, object | _NotSet]] = []
        self._environ: list[tuple[str, str | _NotSet]] = []
        self._syspath_prepend: list[str] = []
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
        name: object | str = _NOT_SET,
        value: object = _NOT_SET,
        *,
        raising: bool = True,
    ) -> None:
        if value is _NOT_SET:
            if not isinstance(target, str):
                raise TypeError("use setattr(target, name, value) or setattr('module.attr', value)")
            module_path, attr_name = target.rsplit(".", 1)
            module = importlib.import_module(module_path)
            obj = module
            attr_value = name
            if attr_value is _NOT_SET:
                raise TypeError("value must be provided when using dotted path syntax")
            attr_name = attr_name
        else:
            if not isinstance(name, str):
                raise TypeError("attribute name must be a string")
            obj = target
            attr_name = name
            attr_value = value

        original = getattr(obj, attr_name, _NOT_SET)
        if original is _NOT_SET and raising:
            raise AttributeError(f"{attr_name!r} not found for patching")

        setattr(obj, attr_name, attr_value)
        self._setattrs.append((obj, attr_name, original))

    def delattr(
        self, target: object | str, name: str | _NotSet = _NOT_SET, *, raising: bool = True
    ) -> None:
        if isinstance(target, str) and name is _NOT_SET:
            module_path, attr_name = target.rsplit(".", 1)
            module = importlib.import_module(module_path)
            obj = module
            attr_name = attr_name
        else:
            if not isinstance(name, str):
                raise TypeError("attribute name must be a string")
            obj = target
            attr_name = name

        original = getattr(obj, attr_name, _NOT_SET)
        if original is _NOT_SET:
            if raising:
                raise AttributeError(f"{attr_name!r} not found for deletion")
            return

        delattr(obj, attr_name)
        self._setattrs.append((obj, attr_name, original))

    def setitem(self, mapping: MutableMapping[Any, Any], key: Any, value: Any) -> None:
        original = mapping.get(key, _NOT_SET)
        mapping[key] = value
        self._setitems.append((mapping, key, original))

    def delitem(self, mapping: MutableMapping[Any, Any], key: Any, *, raising: bool = True) -> None:
        if key not in mapping:
            if raising:
                raise KeyError(key)
            self._setitems.append((mapping, key, _NOT_SET))
            return

        original = mapping[key]
        del mapping[key]
        self._setitems.append((mapping, key, original))

    def setenv(self, name: str, value: Any, prepend: str | None = None) -> None:
        str_value = str(value)
        if prepend and name in os.environ:
            str_value = f"{str_value}{prepend}{os.environ[name]}"
        original = os.environ.get(name)
        os.environ[name] = str_value
        stored_original: str | _NotSet = original if original is not None else _NOT_SET
        self._environ.append((name, stored_original))

    def delenv(self, name: str, *, raising: bool = True) -> None:
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            self._environ.append((name, _NOT_SET))
            return

        original = os.environ.pop(name)
        self._environ.append((name, original))

    def syspath_prepend(self, path: os.PathLike[str] | str) -> None:
        str_path = os.fspath(path)
        if str_path in sys.path:
            return
        sys.path.insert(0, str_path)
        self._syspath_prepend.append(str_path)

    def chdir(self, path: os.PathLike[str] | str) -> None:
        if self._cwd_original is None:
            self._cwd_original = os.getcwd()
        os.chdir(os.fspath(path))

    def undo(self) -> None:
        for obj, attr_name, original in reversed(self._setattrs):
            if original is _NOT_SET:
                try:
                    delattr(obj, attr_name)
                except AttributeError:  # pragma: no cover - defensive
                    pass
            else:
                setattr(obj, attr_name, original)
        self._setattrs.clear()

        for mapping, key, original in reversed(self._setitems):
            if original is _NOT_SET:
                mapping.pop(key, None)
            else:
                mapping[key] = original
        self._setitems.clear()

        for name, original in reversed(self._environ):
            if original is _NOT_SET:
                os.environ.pop(name, None)
            else:
                os.environ[name] = cast(str, original)
        self._environ.clear()

        while self._syspath_prepend:
            str_path = self._syspath_prepend.pop()
            try:
                sys.path.remove(str_path)
            except ValueError:  # pragma: no cover - path already removed externally
                pass

        if self._cwd_original is not None:
            os.chdir(self._cwd_original)
            self._cwd_original = None


class TmpPathFactory:
    """Create temporary directories using :class:`pathlib.Path`."""

    def __init__(self, prefix: str = "tmp_path") -> None:
        super().__init__()
        self._base = Path(tempfile.mkdtemp(prefix=f"rustest-{prefix}-"))
        self._counter = itertools.count()
        self._created: list[Path] = []

    def mktemp(self, basename: str, *, numbered: bool = True) -> Path:
        if not basename:
            raise ValueError("basename must be a non-empty string")
        if numbered:
            suffix = next(self._counter)
            name = f"{basename}{suffix}"
        else:
            name = basename
        path = self._base / name
        path.mkdir(parents=True, exist_ok=False)
        self._created.append(path)
        return path

    def getbasetemp(self) -> Path:
        return self._base

    def cleanup(self) -> None:
        for path in reversed(self._created):
            shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(self._base, ignore_errors=True)
        self._created.clear()


class TmpDirFactory:
    """Wrapper that exposes ``py.path.local`` directories."""

    def __init__(self, path_factory: TmpPathFactory) -> None:
        super().__init__()
        self._factory = path_factory

    def mktemp(self, basename: str, *, numbered: bool = True) -> Any:
        if py is None:  # pragma: no cover - exercised only when dependency missing
            raise RuntimeError("py library is required for tmpdir fixtures")
        path = self._factory.mktemp(basename, numbered=numbered)
        return py.path.local(path)

    def getbasetemp(self) -> Any:
        if py is None:  # pragma: no cover - exercised only when dependency missing
            raise RuntimeError("py library is required for tmpdir fixtures")
        return py.path.local(self._factory.getbasetemp())

    def cleanup(self) -> None:
        self._factory.cleanup()


@fixture(scope="session")
def tmp_path_factory() -> Iterator[TmpPathFactory]:
    factory = TmpPathFactory("tmp_path")
    try:
        yield factory
    finally:
        factory.cleanup()


@fixture(scope="function")
def tmp_path(tmp_path_factory: TmpPathFactory) -> Iterator[Path]:
    path = tmp_path_factory.mktemp("tmp_path")
    yield path


@fixture(scope="session")
def tmpdir_factory() -> Iterator[TmpDirFactory]:
    factory = TmpDirFactory(TmpPathFactory("tmpdir"))
    try:
        yield factory
    finally:
        factory.cleanup()


@fixture(scope="function")
def tmpdir(tmpdir_factory: TmpDirFactory) -> Iterator[Any]:
    yield tmpdir_factory.mktemp("tmpdir")


@fixture(scope="function")
def monkeypatch() -> Iterator[MonkeyPatch]:
    patch = MonkeyPatch()
    try:
        yield patch
    finally:
        patch.undo()


__all__ = [
    "MonkeyPatch",
    "TmpDirFactory",
    "TmpPathFactory",
    "monkeypatch",
    "tmpdir",
    "tmpdir_factory",
    "tmp_path",
    "tmp_path_factory",
]
