"""Fingerprints, records and the inverse queries of the dependency recorder (see ``_deps``).

Everything here is a pure function of the filesystem, the environment and the import system:
hashing, listing fingerprints, import *resolution*, the session prefix, :class:`Record`, and
:func:`stale` / :func:`affected_by_paths`.  Nothing here records anything.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.metadata
import json
import os
import sys
import sysconfig
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

Dep = tuple[str, str, str]

CODE: Final = "code"
FILE: Final = "file"
LISTING: Final = "listing"
ENV: Final = "env"
IMPORT: Final = "import"

ABSENT: Final = "absent"
UNSET: Final = "unset"

#: Names tools create beside the code (the import system, rustest, pytest, linters): not part of
#: what a listing means, and present or absent depending on whether anything ran before.
_TOOL_NAMES: Final = frozenset(
    {"__pycache__", ".rustest_cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)

#: Variables C code reads without Python's help; part of the prefix, never recorded per test.
_C_ENV: Final = (
    "TZ",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHONHASHSEED",
    "PYTHONPATH",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "PYTHONWARNINGS",
)
#: Variables that change on every run by design and must not make the whole-environment
#: dependency stale.
_VOLATILE_ENV: Final = ("PYTEST_CURRENT_TEST",)


# --------------------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """Everything one test depends on, as far as it can be seen."""

    deps: frozenset[Dep]
    cacheable: bool = True
    reasons: tuple[str, ...] = ()
    #: ``(id, entries)``: the search paths the ``import`` dependencies were resolved against
    #: (``sys.path`` as it was, or a package's ``__path__``); an import key names one by its id.
    search_paths: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def dump(self) -> dict[str, Any]:
        return {
            "deps": sorted(self.deps),
            "cacheable": self.cacheable,
            "reasons": list(self.reasons),
            "search_paths": [[pid, list(entries)] for pid, entries in self.search_paths],
        }

    @staticmethod
    def load(data: Mapping[str, Any]) -> Record:
        deps = frozenset((str(k), str(a), str(f)) for k, a, f in data["deps"])
        paths = tuple((str(p), tuple(map(str, e))) for p, e in data.get("search_paths", []))
        return Record(deps, bool(data["cacheable"]), tuple(data["reasons"]), paths)


def merge(*records: Record) -> Record:
    """The record of something that depends on all of ``records`` (a test and the wide-scope
    fixtures it uses)."""
    reasons = sorted({r for record in records for r in record.reasons})
    return Record(
        frozenset(d for record in records for d in record.deps),
        all(record.cacheable for record in records),
        tuple(reasons),
        tuple(sorted({p for record in records for p in record.search_paths})),
    )


@dataclass(frozen=True)
class Prefix:
    """What every test depends on.  ``digest`` changes when any part does."""

    parts: Mapping[str, str]

    @property
    def digest(self) -> str:
        return _blake(json.dumps(sorted(self.parts.items())).encode())

    def changed(self, other: Prefix) -> list[str]:
        keys = sorted(set(self.parts) | set(other.parts))
        return [k for k in keys if self.parts.get(k) != other.parts.get(k)]


def _blake(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _norm(path: str | os.PathLike[str] | bytes) -> str:
    return os.path.normcase(os.path.abspath(os.fsdecode(path)))


def _untracked_prefixes() -> tuple[str, ...]:
    """Directories whose content is not the project's: the standard library, site-packages
    (versions are in the prefix) and rustest itself."""
    found = {
        sysconfig.get_paths().get(name, "")
        for name in ("stdlib", "platstdlib", "purelib", "platlib")
    }
    found.add(os.path.join(sys.base_prefix, "DLLs"))
    found.add(str(Path(__file__).resolve().parent))
    try:
        import site

        found.add(site.getusersitepackages())
    except Exception:  # noqa: BLE001 - a hostile or missing site module is not our problem
        pass
    return tuple(_norm(p) + os.sep for p in found if p)


def digest_file(path: str) -> str:
    """The fingerprint of a file: blake2b of its bytes, or ``absent`` (a directory is ``dir``)."""
    try:
        hasher = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as handle:
            while chunk := handle.read(1 << 20):
                hasher.update(chunk)
        return hasher.hexdigest()
    except (FileNotFoundError, NotADirectoryError):
        return ABSENT
    except IsADirectoryError:
        return "dir"
    except PermissionError:
        return "unreadable"


def digest_listing(path: str) -> str:
    try:
        with os.scandir(path) as entries:
            names = sorted(
                f"{e.name}:{'l' if e.is_symlink() else 'd' if e.is_dir(follow_symlinks=False) else 'f'}"
                for e in entries
                if e.name not in _TOOL_NAMES
            )
        return _blake("\0".join(names).encode("utf-8", "surrogateescape"))
    except (FileNotFoundError, NotADirectoryError):
        return ABSENT
    except PermissionError:
        return "unreadable"


def _env_value(name: str) -> str:
    value = os.environ.get(name)
    return UNSET if value is None else _blake(value.encode("utf-8", "surrogateescape"))


def _env_all() -> str:
    items = sorted((k, v) for k, v in os.environ.items() if k not in _VOLATILE_ENV)
    return _blake(json.dumps(items).encode("utf-8", "surrogateescape"))


#: Modules whose resolution is not recorded: the standard library (a *new local file named like a
#: stdlib module* is a documented hole) and built-ins.
STDLIB: Final = frozenset(sys.stdlib_module_names) | frozenset(sys.builtin_module_names)


def path_id(search: Sequence[str]) -> str:
    """A short name for a search path, so many import keys can share one stored copy."""
    return _blake("\n".join(search).encode("utf-8", "surrogateescape"))[:8]


def resolve(name: str, search: Sequence[str]) -> tuple[str, str | None]:
    """``(fingerprint, origin)``: what the standard path finder makes of *name* on *search* now.

    Nothing is imported.  The fingerprint is of the *resolution* -- which file, or which namespace
    directories, or that nothing was found -- so a new file earlier on the path, or a package beside
    a module of the same name, changes it.  The content of a compiled extension is added; a source
    file's content is the ``code`` dependency's business.
    """
    try:
        spec = importlib.machinery.PathFinder.find_spec(name, list(search))
    except Exception as exc:  # noqa: BLE001 - a hostile path hook: never "unchanged"
        return f"error:{type(exc).__name__}", None
    if spec is None:
        return ABSENT, None
    origin = spec.origin if spec.has_location else None
    if origin is None:
        places = "|".join(_norm(p) for p in spec.submodule_search_locations or [])
        return _blake(f"namespace:{places}".encode()), None
    description = _norm(origin)
    if not origin.endswith((".py", ".pyc")) and not _norm(origin).startswith(_untracked_prefixes()):
        description += ":" + digest_file(origin)
    return _blake(description.encode()), origin


def opaque_fingerprint(origin: str, finder_file: str) -> str:
    """A module a non-standard finder resolved: its file and the finder's own file."""
    finder = digest_file(finder_file) if finder_file else "no-finder-file"
    return _blake(f"opaque:{digest_file(origin)}:{finder}".encode())


def import_key(name: str, pid: str, opaque: tuple[str, str, str] | None = None) -> str:
    """The ``import`` dependency key: the name, the search path's id and, for an opaque finder,
    ``(finder qualified name, finder file, origin)``."""
    if opaque is None:
        return f"{name}\0{pid}"
    qualname, finder_file, origin = opaque
    return f"{name}\0{pid}\0opaque\0{qualname}\0{finder_file}\0{origin}"


def _import_fingerprint(
    key: str, paths: Mapping[str, tuple[str, ...]], memo: dict[str, tuple[str, str | None]]
) -> tuple[str, str | None]:
    """``(fingerprint, origin)`` of an ``import`` key as it resolves now."""
    parts = key.split("\0")
    if len(parts) >= 6 and parts[2] == "opaque":
        return opaque_fingerprint(parts[5], parts[4]), parts[5]
    search = paths.get(parts[1])
    if search is None:
        return "unresolvable", None
    if key not in memo:
        memo[key] = resolve(parts[0], search)
    return memo[key]


def _current(
    kind: str,
    key: str,
    hashes: dict[str, str],
    paths: Mapping[str, tuple[str, ...]],
    resolved: dict[str, tuple[str, str | None]],
) -> str:
    """The fingerprint ``(kind, key)`` has right now."""
    if kind in (CODE, FILE):
        if key not in hashes:
            hashes[key] = digest_file(key)
        return hashes[key]
    if kind == LISTING:
        return digest_listing(key)
    if kind == ENV:
        return _env_all() if key == "*" else _env_value(key)
    if kind == IMPORT:
        return _import_fingerprint(key, paths, resolved)[0]
    return "unknown-kind"


def stale(records: Mapping[str, Record]) -> dict[str, list[Dep]]:
    """The records that must run again, with the dependencies that changed (an uncacheable record
    is listed with no dependencies).  Fingerprints are recomputed from scratch: no stat gate, and
    the import system's directory caches are dropped first so a file created a moment ago is seen."""
    importlib.invalidate_caches()
    hashes: dict[str, str] = {}
    resolved: dict[str, tuple[str, str | None]] = {}
    out: dict[str, list[Dep]] = {}
    for nodeid, record in records.items():
        paths = dict(record.search_paths)
        changed = [
            d for d in sorted(record.deps) if _current(d[0], d[1], hashes, paths, resolved) != d[2]
        ]
        if changed or not record.cacheable:
            out[nodeid] = changed
    return out


def affected_by_paths(
    records: Mapping[str, Record], changed: Iterable[str | os.PathLike[str]]
) -> dict[str, list[str]]:
    """The records that depend on any of the ``changed`` paths (for ``--related``, and watch mode's
    affected tests): ``{nodeid: [changed path, ...]}``.  A file or code dependency matches its
    own path; a listing matches a path directly inside it (or itself); an import matches the file
    it resolves to *now*.  A path is matched as written and as its real path (a symlink).
    Uncacheable records are not listed: they always run."""
    importlib.invalidate_caches()
    wanted = {_norm(p) for p in changed} | {_norm(os.path.realpath(p)) for p in changed}
    real: dict[str, str] = {}  # a recorded path as the file system now resolves it (a symlink)

    def reaches(key: str) -> bool:
        if key in wanted:
            return True
        if key not in real:
            real[key] = _norm(os.path.realpath(key))
        return real[key] in wanted

    resolved: dict[str, tuple[str, str | None]] = {}
    out: dict[str, list[str]] = {}
    for nodeid, record in records.items():
        paths = dict(record.search_paths)
        hits: set[str] = set()
        for kind, key, _ in record.deps:
            if kind in (CODE, FILE):
                if reaches(key):
                    hits.add(key)
            elif kind == LISTING:
                reaches(key)  # fills `real`
                hits |= {
                    p for p in wanted if p == key or os.path.dirname(p) in (key, real.get(key))
                }
            elif kind == IMPORT:
                origin = _import_fingerprint(key, paths, resolved)[1]
                if origin is not None and _norm(origin) in wanted:
                    hits.add(_norm(origin))
        if hits:
            out[nodeid] = sorted(hits)
    return out


def session_prefix(
    options: Mapping[str, object] | None = None, *, extra: Mapping[str, str] | None = None
) -> Prefix:
    """What every test depends on (see the module docstring).  ``options`` are the host's
    outcome-relevant settings (ini values, flags, plugin set); ``extra`` more named parts."""
    try:
        dists = sorted(
            f"{d.metadata['Name']}=={d.version}".lower() for d in importlib.metadata.distributions()
        )
    except Exception:  # noqa: BLE001 - a broken distribution must not stop the run
        dists = ["unreadable"]
    package = Path(__file__).resolve().parent
    build: list[str] = []
    for path in sorted(package.rglob("*")):
        if path.suffix in {".py", ".pyd", ".so", ".dylib"} and "__pycache__" not in path.parts:
            stat = path.stat()
            build.append(f"{path.relative_to(package)}:{stat.st_size}:{stat.st_mtime_ns}")
    try:
        version = importlib.metadata.version("rustest")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    flags = sys.flags
    parts = {
        "rustest": version,
        "rustest_build": _blake("\n".join(build).encode()),
        "python": f"{sys.version}|{sys.implementation.cache_tag}|{sys.platform}",
        "flags": (
            f"{flags.optimize}{flags.dev_mode}{flags.utf8_mode}{flags.safe_path}"
            f"{flags.bytes_warning}{sys.warnoptions!r}"
        ),
        "dists": _blake("\n".join(dists).encode()),
        "cwd": _norm(os.getcwd()),
        "c_env": json.dumps([os.environ.get(name) for name in _C_ENV]),
        "options": json.dumps(dict(options or {}), sort_keys=True, default=str),
        **dict(extra or {}),
    }
    return Prefix(parts)
