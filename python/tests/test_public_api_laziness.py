"""The public API is bound lazily; this pins that laziness is invisible and safe.

``python -m rustest`` executes ``rustest/__init__.py`` before ``__main__``, so whatever that
module imports is on the critical path of every invocation — a collect-only run, a
``--report-json`` run, and each of the N worker subprocesses a run spawns. Phase 2 Task 3
made the exports lazy (PEP 562 ``__getattr__``) and measured the package import falling from
~390 ms above a bare interpreter to ~30 ms.

Laziness is only worth having if it is *invisible*, so the tests here are in two halves:

* the API still resolves, in every spelling a user writes it;
* the modules that made it slow are genuinely not imported any more — a regression that
  reintroduced a top-level ``import rich`` would leave every test above green.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import rustest


def _in_fresh_interpreter(code: str) -> str:
    """Run *code* in a new interpreter and return its stdout.

    Import-graph questions cannot be answered in-process: this test session has already
    imported half of rustest, so ``sys.modules`` here says nothing about what a fresh
    ``python -m rustest`` pulls in.
    """
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# the API still resolves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(rustest.__all__))
def test_every_exported_name_resolves(name: str) -> None:
    """``__all__`` is a promise; a lazy module can break it without anything else noticing."""
    assert getattr(rustest, name) is not None


@pytest.mark.parametrize("name", sorted(rustest.__all__))
def test_every_exported_name_resolves_by_from_import(name: str) -> None:
    """``from rustest import x`` takes a different path through the import system than
    ``rustest.x``, and it is the spelling every test file in the wild uses."""
    out = _in_fresh_interpreter(f"from rustest import {name}; print(type({name}).__name__)")
    assert out


def test_a_missing_name_still_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'not_a_real_export'"):
        _ = rustest.not_a_real_export  # pyright: ignore[reportAttributeAccessIssue]


def test_dir_lists_the_whole_api_before_anything_is_touched() -> None:
    """Tab completion in a REPL is the one user-visible way a lazy module can differ."""
    out = _in_fresh_interpreter("import rustest; print(' '.join(sorted(dir(rustest))))")
    listed = set(out.split())
    assert set(rustest.__all__) <= listed


def test_a_resolved_name_is_cached_in_the_module_dict() -> None:
    """After first use, lookup must be an ordinary dict hit rather than a ``__getattr__`` call.

    Otherwise every ``@fixture`` in a large conftest would pay an ``import_module`` round
    trip. Asserted through ``vars()``, which sees the module ``__dict__`` directly.
    """
    out = _in_fresh_interpreter(
        "import rustest\n"
        "before = 'fixture' in vars(rustest)\n"
        "_ = rustest.fixture\n"
        "print(before, 'fixture' in vars(rustest))"
    )
    assert out == "False True"


# ---------------------------------------------------------------------------
# the collision that laziness makes possible
# ---------------------------------------------------------------------------


def test_no_lazy_export_collides_with_a_submodule() -> None:
    """A lazily exported name must not also be a module file name.

    PEP 562's ``__getattr__`` runs **only when normal lookup fails**, and importing
    ``rustest.<name>`` binds the *module* into the package ``__dict__``. From then on the
    lazy attribute is unreachable and the name silently means the module instead — which is
    exactly the bug ``approx`` hit (``rustest/compat/pytest.py`` does
    ``from rustest.approx import approx``, so the shadowing happened on every worker).

    This is the general form, so the next such export fails here instead of at a user's
    ``TypeError: 'module' object is not callable``.
    """
    package = Path(rustest.__file__).parent
    submodules = {p.stem for p in package.glob("*.py")} | {
        p.name for p in package.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    }
    collisions = sorted(set(rustest._LAZY) & submodules)
    assert not collisions, (
        f"these exports are lazy but share a name with a submodule: {collisions}. "
        "Bind them eagerly in __init__.py instead (see the `approx` comment there)."
    )


def test_approx_is_the_class_even_after_its_module_is_imported() -> None:
    """The regression itself, in the order that triggered it.

    Importing the submodule first is what a worker does (via the pytest compat shim), so the
    assertion order here is not incidental.
    """
    out = _in_fresh_interpreter(
        "import rustest.approx\n"
        "import rustest\n"
        "print(callable(rustest.approx), rustest.approx([0.3, 0.3]) == [0.3, 0.3])"
    )
    assert out == "True True"


# ---------------------------------------------------------------------------
# the laziness is real
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "forbidden"),
    [
        # `rich` is ~230 ms and renders nothing on any v2 path.
        ("import rustest", "rich"),
        ("import rustest.cli", "rich"),
        # The worker pays its import graph N times per run; `asyncio` is ~240 ms of it and is
        # needed only by a suite that actually contains an async test.
        ("import rustest._v2_worker", "rich"),
        ("import rustest._v2_worker", "asyncio"),
    ],
)
def test_the_hot_paths_do_not_import_the_expensive_modules(entry: str, forbidden: str) -> None:
    out = _in_fresh_interpreter(
        f"{entry}\n"
        "import sys\n"
        f"print(any(m == {forbidden!r} or m.startswith({forbidden!r} + '.') for m in sys.modules))"
    )
    assert out == "False", f"{entry!r} pulled in {forbidden!r}"


def test_importing_the_package_does_not_pull_in_the_cli_or_the_engine() -> None:
    """``import rustest`` in a user's ``conftest.py`` must not load the CLI or the extension.

    ``rustest.core`` imports the compiled extension, which is the single largest remaining
    import on any path; a library user asking only for ``@fixture`` should not pay it.
    """
    out = _in_fresh_interpreter(
        "import rustest\n"
        "import sys\n"
        "print(sorted(m for m in ('rustest.cli', 'rustest.core', 'rustest.rust')"
        " if m in sys.modules))"
    )
    assert out == "[]"
