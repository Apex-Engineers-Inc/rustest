"""The one place the installed version is read from.

There were two callers before this module existed and there is now a third, which is the
point: ``--llm``'s ``meta`` line and ``--version`` must never be able to disagree. A second
hardcoded literal is the usual way that happens, so there isn't one -- the value comes from
the installed distribution's metadata, which `maturin` writes from `pyproject.toml`.
"""

from __future__ import annotations

#: What a source checkout with no `dist-info` reports. Not a real release, and deliberately
#: shaped so it sorts below every real one rather than looking like a version.
UNINSTALLED = "0.0.0"


def package_version() -> str:
    """The installed ``rustest`` version, or :data:`UNINSTALLED` from a bare source tree.

    ``importlib.metadata`` is imported inside the function rather than at module scope for
    the reason everything else in this package defers its imports: the metadata scan walks
    ``sys.path`` looking for a ``dist-info``, and no run that does not ask for the version
    should pay for it.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("rustest")
    except PackageNotFoundError:  # pragma: no cover - a source checkout with no install
        return UNINSTALLED
