"""The **three-way collection differential** — Tier S vs Tier D vs real pytest.

``src/v2/static_collect.rs`` (Tier S) answers "what tests are in this file?" by parsing it
with ruff's Python parser instead of importing it. Tier D — the Python worker — imports the
file and runs the same code pytest would, so it is definitionally the oracle. Tier S is
therefore only ever allowed to be *faster*, never different, and the way to know is to ask
the same tree three times:

1. ``manifest(hybrid)``  — the default: Tier S where it can answer, Tier D otherwise;
2. ``manifest(D-only)``  — ``RUSTEST_V2_COLLECT_TIER=d`` / ``collect_tier="d"``, the control;
3. ``pytest --collect-only -q`` — the external oracle both tiers exist to reproduce.

The first two are compared as **whole manifests** (ids, qualnames, class names, param ids,
marks and fixtures), not merely as id lists: a mark or a fixture name that Tier S gets wrong
does not change a single nodeid, and would sail through an ids-only diff into the execute
path, where a dropped ``xfail`` turns a red run green. The ``tier`` field is the one
permitted difference, and it is what the attribution assertions read.

The third leg is compared on ids, because that is what pytest exposes.

**A differential alone is not enough**, and that is why every test here also asserts tier
attribution: a Tier S that refused every file would make legs 1 and 2 identical and prove
nothing at all. So each suite below names which files must have been answered statically and
which must have reached a worker.

The trees are copied out of the repository before either runner sees them, with a bare
``pytest.ini`` at the root. Without it both runners walk *up* out of ``tmp_path``, find this
repo's ``pyproject.toml`` (which carries ``[tool.pytest.ini_options]``), and report
repo-relative ids — the same isolation protocol ``conformance/harness/runners.py::
_isolate_case`` uses, and for the same reason.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import pytest

# The compiled extension is built and installed by `python/tests/__init__.py`.
from rustest import rust

#: The corpus is the conformance gate's own material, so running the differential over it
#: asks the question about exactly the shapes Phase 0 chose as representative.
_CORPUS = Path(__file__).resolve().parents[2] / "conformance" / "corpus"

#: `conformance/harness/runners.py::_NODEID_RE`, kept in step deliberately: this module must
#: read pytest's `-q` output the same way the gate does, or the two would disagree about what
#: pytest said rather than about what rustest said.
_NODEID_RE = re.compile(r"^[^\s:][^:\n]*(::[^\s:][^:\n]*)+(\[[^\n]*\])?$")


def _isolate(source: Path, dest_parent: Path) -> Path:
    """Copy *source* under *dest_parent* with a bare ``pytest.ini`` pinning rootdir.

    ``__pycache__`` is excluded because stale bytecode beside freshly copied source is
    exactly the shape of pytest's ``import file mismatch``, which would make the tree test
    something other than what it says.
    """
    dest = dest_parent / source.name
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", ".*_cache"))
    if not (dest / "pytest.ini").exists():
        (dest / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return dest


def _case_args(case_dir: Path) -> list[str]:
    """A corpus case's own ``case.toml`` arguments, minus the option-shaped ones.

    ``-k``/``-m`` are *selection*, applied identically to both tiers after collection, so
    they add nothing here and would only shrink the sample. The path arguments are kept,
    because ``collection/dupe-args`` is a case about what naming a file twice collects.
    """
    config = case_dir / "case.toml"
    if not config.is_file():
        return []
    import tomllib

    args = tomllib.loads(config.read_text(encoding="utf-8")).get("case", {}).get("args", [])
    kept: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in ("-k", "-m"):
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        kept.append(arg)
    return kept


def _manifest(work: Path, args: list[str], tier: str) -> dict[str, Any]:
    """One collection, at the requested tier, straight off the Rust boundary."""
    payload = rust.v2_collect(
        str(work),
        args,
        sys.executable,
        4,
        None,
        None,
        True,
        tier,
    )
    return json.loads(payload)


def _strip_tier(manifest: dict[str, Any]) -> dict[str, Any]:
    """The manifest with every ``tier`` removed — the one field the two legs may differ in."""
    stripped = dict(manifest)
    stripped["tests"] = [
        {key: value for key, value in test.items() if key != "tier"} for test in manifest["tests"]
    ]
    return stripped


def _pytest_ids(work: Path, args: list[str]) -> list[str]:
    """Real pytest's collected ids, in pytest's order."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q", *args],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode >= 3 and proc.returncode != 5:
        raise RuntimeError(f"pytest collect failed (exit {proc.returncode}): {proc.stderr[-500:]}")
    return [line for line in proc.stdout.splitlines() if _NODEID_RE.match(line)]


def _tiers(manifest: dict[str, Any]) -> dict[str, set[str]]:
    """Which files were answered by which tier, as ``{"s": {...}, "d": {...}}``."""
    result: dict[str, set[str]] = {"s": set(), "d": set()}
    for test in manifest["tests"]:
        result[test.get("tier", "d")].add(test["path"])
    return result


def _waived_cases() -> set[str]:
    """The cases ``conformance/waivers-v2-collect.toml`` already excuses from pytest parity.

    Read rather than hard-coded, so this module cannot drift from the gate's ledger — and
    read *only* for the pytest leg. The Tier S question is legs 1 vs 2, and a pre-existing
    Tier-D divergence says nothing about it, so a waived case is still fully compared between
    the two tiers. (Today the single entry is ``fixtures/module-param-reorder``: v2 has no
    ``reorder_items`` pass, which changes the *order* of the ids both tiers produce.)
    """
    import tomllib

    ledger = _CORPUS.parent / "waivers-v2-collect.toml"
    return set(tomllib.loads(ledger.read_text(encoding="utf-8")).get("cases", {}))


_WAIVED = _waived_cases()


def _three_way(
    tmp_path: Path, source: Path, args: list[str], *, compare_pytest: bool = True
) -> dict[str, Any]:
    """Run the legs and assert they agree.  Returns the hybrid manifest."""
    work = _isolate(source, tmp_path)

    hybrid = _manifest(work, args, "auto")
    oracle = _manifest(work, args, "d")
    assert _strip_tier(hybrid) == _strip_tier(oracle), (
        f"the static tier changed the manifest for {source.name}"
    )

    if compare_pytest:
        expected = _pytest_ids(work, args)
        assert [test["id"] for test in hybrid["tests"]] == expected, (
            f"hybrid ids diverge from pytest for {source.name}"
        )
    return hybrid


# ---------------------------------------------------------------------------
# leg 1+2+3 over the conformance corpus
# ---------------------------------------------------------------------------

_CASES = sorted(
    (
        case
        for area in _CORPUS.iterdir()
        if area.is_dir()
        for case in area.iterdir()
        if case.is_dir()
    ),
    key=lambda case: f"{case.parent.name}/{case.name}",
)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: f"{case.parent.name}/{case.name}")
def test_corpus_case_is_tier_invariant(tmp_path: Path, case: Path) -> None:
    """Every corpus case collects identically hybrid, Tier-D-only, and under pytest.

    A case the v2-collect ledger already waives keeps the two-tier comparison and drops only
    the pytest leg — see :func:`_waived_cases`.
    """
    name = f"{case.parent.name}/{case.name}"
    _three_way(tmp_path, case, _case_args(case), compare_pytest=name not in _WAIVED)


def test_the_corpus_exercises_both_tiers(tmp_path: Path) -> None:
    """The corpus is not answered entirely by one tier.

    Without this the parametrized differential above could be green because Tier S never
    fires — which is the failure mode a differential cannot see by construction.
    """
    seen = {"s": 0, "d": 0}
    for case in _CASES:
        work = _isolate(case, tmp_path / case.name)
        manifest = _manifest(work, _case_args(case), "auto")
        for tier, paths in _tiers(manifest).items():
            seen[tier] += len(paths)
    assert seen["s"] > 0, "no corpus file was answered statically"
    assert seen["d"] > 0, "no corpus file reached a worker"


# ---------------------------------------------------------------------------
# the generated mixed suite
# ---------------------------------------------------------------------------

#: The deliberately-dynamic file shapes, one per dynamism rule, keyed by the rule they trip.
#: Each one is a file the detector **must** refuse; the attribution assertion below names
#: them individually, so a rule that quietly stopped firing fails here rather than showing up
#: as a slow drift in the tier split.
_DYNAMIC_SHAPES: dict[str, str] = {
    "star_import": "from pytest import *\n\n\ndef test_d():\n    pass\n",
    "module_getattr": "def __getattr__(name):\n    raise AttributeError(name)\n\n\ndef test_d():\n    pass\n",
    "exec_at_import": 'exec("X = 1")\n\n\ndef test_d():\n    pass\n',
    "conditional_def": "import sys\n\nif sys.version_info >= (3, 12):\n\n    def test_d():\n        pass\n",
    "class_bases": "class Base:\n    def test_inherited(self):\n        pass\n\n\nclass TestDerived(Base):\n    def test_d(self):\n        pass\n",
    "unittest_case": "import unittest\n\n\nclass TestLegacy(unittest.TestCase):\n    def test_d(self):\n        self.assertTrue(True)\n",
    "unknown_decorator": "def wrap(func):\n    return func\n\n\n@wrap\ndef test_d():\n    pass\n",
    "module_side_effect": "VALUES = []\nVALUES.append(1)\n\n\ndef test_d():\n    pass\n",
    "non_literal_parametrize": 'import pytest\n\nCASES = [1, 2]\n\n\n@pytest.mark.parametrize("v", CASES)\ndef test_d(v):\n    pass\n',
    "non_literal_mark": 'import pytest\n\n\n@pytest.mark.skipif(1 + 1 == 2, reason="always")\ndef test_d():\n    pass\n',
    "parametrized_fixture": "import pytest\n\n\n@pytest.fixture(params=[1, 2])\ndef n(request):\n    return request.param\n\n\ndef test_d(n):\n    pass\n",
    "test_attribute": "__test__ = True\n\n\ndef test_d():\n    pass\n",
    # A `**` splat on a fixture: the keys are a runtime value, so `params=` may be among
    # them. Read as the literal name `"**"` this sailed through as a plain fixture.
    "fixture_kwargs_splat": (
        "import pytest\n\n\n"
        '@pytest.fixture(**{"params": [1, 2]})\n'
        "def n(request):\n    return request.param\n\n\n"
        "def test_d(n):\n    pass\n"
    ),
    # A PEP 263 cookie naming a non-UTF-8 encoding. The body is deliberately pure ASCII, so
    # both readings agree and the *ids* are identical — what is being pinned is that the file
    # goes to Tier D at all. The reading that actually diverges is pinned separately, in
    # `test_a_non_utf8_encoding_cookie_routes_the_file_to_d`, where it can be compared
    # without a console-encoding artifact in the way.
    "encoding_cookie": "# -*- coding: latin-1 -*-\n\n\ndef test_d():\n    pass\n",
    # An integer with no exact `serde_json` form; the old `as i64` cast wrapped it into a
    # positive number of a different magnitude, silently, with no nodeid moving.
    "unrepresentable_int": (
        "import pytest\n\n\n@pytest.mark.limit(-9223372036854775809)\ndef test_d():\n    pass\n"
    ),
}

#: The shapes Tier S must answer, keyed by the feature they exercise.  The values are chosen
#: so the *ids* differ between shapes — a suite of identical files would not notice a
#: collector that emitted one file's tests for another.
_STATIC_SHAPES: dict[str, str] = {
    "plain": "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n",
    "async_def": "async def test_async():\n    pass\n",
    "klass": "class TestBox:\n    def test_method(self):\n        pass\n\n    @staticmethod\n    def test_static(tmp_path):\n        pass\n",
    "nested_klass": "class TestOuter:\n    class TestInner:\n        def test_deep(self):\n            pass\n",
    "parametrize": 'import pytest\n\n\n@pytest.mark.parametrize("v", [1, 2, "a", None, True])\ndef test_p(v):\n    pass\n',
    "parametrize_ids": 'import pytest\n\n\n@pytest.mark.parametrize("v", [1, 2], ids=["one", "two"])\ndef test_named(v):\n    pass\n',
    "parametrize_stacked": 'import pytest\n\n\n@pytest.mark.parametrize("a", [1, 2])\n@pytest.mark.parametrize("b", ["x", "y"])\ndef test_grid(a, b):\n    pass\n',
    "parametrize_pathological": 'import pytest\n\n\n@pytest.mark.parametrize("s", ["", "a"])\ndef test_empty_id(s):\n    pass\n\n\n@pytest.mark.parametrize("s", [1, 2, 3, 4], ids=["p]q", "trail[", "a::b", "-"])\ndef test_shapes(s):\n    pass\n',
    "parametrize_dupes": 'import pytest\n\n\n@pytest.mark.parametrize("v", [1, 1, "a", "a"])\ndef test_dupes(v):\n    pass\n',
    "marks": 'import pytest\n\npytestmark = pytest.mark.module_wide\n\n\n@pytest.mark.slow\n@pytest.mark.smoke("tag", level=3)\ndef test_marked():\n    pass\n\n\n@pytest.mark.xfail(reason="known")\ndef test_x():\n    pass\n\n\n@pytest.mark.skipif(True, reason="never")\ndef test_s():\n    pass\n',
    "fixtures": 'import pytest\n\n\n@pytest.fixture\ndef value():\n    return 1\n\n\n@pytest.fixture(scope="module")\ndef shared():\n    return 2\n\n\ndef test_uses(value, shared, tmp_path):\n    pass\n',
    "stdlib_imports": "import json\nimport os\nfrom pathlib import Path\nfrom typing import Any\n\n\ndef test_stdlib():\n    pass\n",
    # The positive control for the integer-payload fix: values either side of the `i64`
    # boundary that ARE exact, so the manifest comparison pins payload fidelity rather than
    # only the refusal. `_json_safe` passes an int through untouched, so Tier D's bytes here
    # are the oracle for Tier S's.
    "mark_payloads": (
        "import pytest\n\n\n"
        "@pytest.mark.limit(9223372036854775807, -9223372036854775808)\n"
        '@pytest.mark.tags("a", "b", nested=[1, -2, None, True], mapping={"k": -9})\n'
        "def test_payload():\n    pass\n"
    ),
    # `_hasinit` is `bool(getattr(cls, "__init__")) and it != object.__init__`, so a TRUTHY
    # binding refuses the class even without a `def`. The falsy half is a rustest/pytest
    # divergence in both tiers and lives in its own test — see `_FALSY_CONSTRUCTOR`.
    "class_constructor_binding": (
        "class TestTruthyInit:\n    __init__ = 5\n\n"
        "    def test_never_collected(self):\n        pass\n\n\n"
        "class TestPlain:\n    def test_collected(self):\n        pass\n"
    ),
    "no_tests": "def helper():\n    pass\n",
}

#: A class-level ``@parametrize`` crossed with a method-level one, kept **out** of the suite
#: above because its ids are a pre-existing rustest/pytest divergence and it would poison the
#: pytest leg with a disagreement neither tier is responsible for.
#:
#: pytest 8.4.2 emits ``test_m[10-1]``/``test_m[10-2]`` (method component first); rustest v1's
#: ``decorators.py::_cross_product_cases`` emits ``test_m[1-10]``/``test_m[2-10]`` and
#: ``_v2_worker.py::_cross_product_cases`` consumes those ids verbatim, documenting the
#: divergence in its own docstring. Tier S reproduces **rustest's**, which is the only correct
#: answer available to it: matching pytest here would make Tier S disagree with Tier D.
#: A class binding `__init__` to a **falsy** value, kept out of the suite above for the same
#: reason as `_CLASS_PARAMETRIZE`: it is a rustest/pytest divergence in *both* tiers.
#:
#: `_hasinit` is `bool(init) and init != object.__init__`, so a falsy `__init__` does not
#: refuse the class — pytest goes on to **instantiate** it (`_pytest/python.py::newinstance`,
#: `return self.obj()`) and gets `TypeError: 'NoneType' object is not callable`, i.e. a
#: collection error and exit 2. `_v2_worker.py::_collect_class` deliberately never
#: instantiates ("this parses them off the class ... which avoids instantiating a class during
#: collection"), so rustest collects the method in both tiers.
_FALSY_CONSTRUCTOR = (
    "class TestFalsyInit:\n    __init__ = None\n\n"
    "    def test_still_collected(self):\n        pass\n"
)

_CLASS_PARAMETRIZE = (
    'import pytest\n\n\n@pytest.mark.parametrize("x", [1, 2])\n'
    'class TestGrid:\n    @pytest.mark.parametrize("y", [10])\n'
    "    def test_m(self, x, y):\n        pass\n"
)


def _mixed_suite(root: Path, files: int = 200) -> tuple[set[str], set[str]]:
    """Write a *files*-file suite alternating static and deliberately-dynamic shapes.

    Returns ``(expected_static_paths, expected_dynamic_paths)`` as rootdir-relative posix
    paths, which is what the manifest's ``path`` field carries.

    Files are spread over subdirectories, because directory depth is what the conftest-chain
    rule keys on and a flat suite would never exercise it. Every file gets a **unique stem**:
    two same-stem files in different non-package directories are pytest's ``import file
    mismatch``, which Tier S deliberately refuses — a suite that tripped that rule by accident
    would drift towards Tier D for a reason unrelated to what it means to test.
    """
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    static_names = sorted(_STATIC_SHAPES)
    dynamic_names = sorted(_DYNAMIC_SHAPES)
    expected_static: set[str] = set()
    expected_dynamic: set[str] = set()

    for index in range(files):
        directory = root / f"pkg{index % 7}"
        directory.mkdir(exist_ok=True)
        if index % 2 == 0:
            name = static_names[(index // 2) % len(static_names)]
            body = _STATIC_SHAPES[name]
            target = expected_static
        else:
            name = dynamic_names[(index // 2) % len(dynamic_names)]
            body = _DYNAMIC_SHAPES[name]
            target = expected_dynamic
        path = directory / f"test_{name}_{index:03d}.py"
        path.write_text(body, encoding="utf-8")
        target.add(f"pkg{index % 7}/{path.name}")
    return expected_static, expected_dynamic


def test_the_mixed_synthetic_suite_is_tier_invariant(tmp_path: Path) -> None:
    """200 files, half of them deliberately dynamic: all three legs must agree.

    This is the differential's volume leg. The corpus is 34 files chosen for *semantics*;
    this one is chosen for *coverage of the detector*, with every dynamism rule represented
    and every static feature repeated across directories.
    """
    root = tmp_path / "suite"
    root.mkdir()
    _mixed_suite(root)

    hybrid = _manifest(root, [], "auto")
    oracle = _manifest(root, [], "d")
    assert _strip_tier(hybrid) == _strip_tier(oracle)

    assert [test["id"] for test in hybrid["tests"]] == _pytest_ids(root, [])
    assert not hybrid.get("errors"), hybrid.get("errors")


def test_tier_attribution_on_the_mixed_synthetic_suite(tmp_path: Path) -> None:
    """Every file lands in the tier its shape was written for — no file, no rule excepted.

    Files with no tests contribute no manifest entries and so have no attribution to assert;
    they are excluded from both expectations rather than assumed.
    """
    root = tmp_path / "suite"
    root.mkdir()
    expected_static, expected_dynamic = _mixed_suite(root)

    hybrid = _manifest(root, [], "auto")
    tiers = _tiers(hybrid)

    # `no_tests` files collect nothing, so they appear in neither bucket.
    testless = {path for path in expected_static if "_no_tests_" in path}
    assert tiers["s"] == expected_static - testless, sorted(
        (expected_static - testless) ^ tiers["s"]
    )
    assert tiers["d"] == expected_dynamic, sorted(expected_dynamic ^ tiers["d"])


def test_the_stdlib_allowlist_is_importable_and_actually_stdlib() -> None:
    """Every name on Tier S's stdlib allowlist really is importable standard library, *here*.

    The allowlist is the load-bearing half of the import rule: Tier S answers statically for a
    file that says ``import json`` precisely because that import cannot raise. A name that is
    not stdlib on this interpreter — removed in a later Python, or an optional C extension this
    build lacks — would make that assumption false and turn a collection error into a silently
    complete manifest, which is the Critical class.

    So the list is *verified*, not asserted, and verified on whatever interpreter is running:
    CI runs 3.12, 3.13 and 3.14, so a name that stops being universal fails on the version
    where it does. Two checks, because each catches something the other cannot:

    * the module imports at all — rules out removals and missing extensions;
    * its file (or, for a builtin, its absence of one) is inside ``sysconfig``'s stdlib
      directory — rules out a name that only resolves because a *site-package* provides it.
    """
    import importlib
    import sysconfig

    allowlist = rust.v2_static_stdlib_allowlist()
    assert allowlist, "the allowlist is empty; Tier S would refuse every real file"
    assert allowlist == sorted(allowlist), "keep the allowlist sorted so diffs are readable"

    stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    for name in allowlist:
        module = importlib.import_module(name)
        origin = getattr(module, "__file__", None)
        if origin is None:
            # A built-in module (``sys``, ``time``, ...) is compiled into the interpreter and
            # has no file at all, which is a stronger guarantee than living under `stdlib`.
            assert name in sys.builtin_module_names, name
            continue
        assert Path(origin).resolve().is_relative_to(stdlib), (
            f"{name} resolves to {origin}, which is outside the standard library"
        )


def test_a_missing_dependency_is_still_a_collection_error(tmp_path: Path) -> None:
    """The Critical class, stated directly.

    A test file importing something that is not installed is a **collection error** under
    pytest (``_pytest/python.py::importtestmodule``) and under Tier D
    (``_v2_worker.py::collect_file`` turns any import-time exception into an ``errors`` entry).
    A Tier S that answered for it would report its tests as collected and exit 0 — a shorter,
    greener, wrong run. This is the shape the import allowlist exists to catch.
    """
    root = tmp_path / "suite"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "test_missing.py").write_text(
        "import definitely_not_installed_anywhere\n\n\ndef test_x():\n    pass\n",
        encoding="utf-8",
    )
    (root / "test_fine.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")

    hybrid = _manifest(root, [], "auto")
    oracle = _manifest(root, [], "d")

    assert _strip_tier(hybrid) == _strip_tier(oracle)
    assert [test["id"] for test in hybrid["tests"]] == ["test_fine.py::test_ok"]
    assert [error["path"] for error in hybrid["errors"]] == ["test_missing.py"]
    assert "ModuleNotFoundError" in hybrid["errors"][0]["message"]
    # The good file is still answered statically: one bad file does not disable the tier.
    assert hybrid["tests"][0]["tier"] == "s"


def test_a_local_module_shadowing_the_stdlib_routes_the_file_to_d(tmp_path: Path) -> None:
    """A ``queue.py`` beside a test file makes ``import queue`` user code.

    ``_v2_worker.py::sys_path_root_for`` puts the test file's own directory on ``sys.path``, so
    the local module wins — and it can raise, which no static analysis of the *test* file would
    ever reveal. The allowlist is therefore conditioned on the shadow set
    (``static_collect.rs::shadowing_names``), and this is the end-to-end proof that the
    condition reaches the answer.
    """
    root = tmp_path / "suite"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "test_uses_queue.py").write_text(
        "import queue\n\n\ndef test_q():\n    assert queue.LOCAL\n", encoding="utf-8"
    )

    # Without the shadowing module the file is static...
    assert _manifest(root, [], "auto")["tests"][0]["tier"] == "s"

    # ...and the moment a local `queue.py` exists, it is not.
    (root / "queue.py").write_text("LOCAL = True\n", encoding="utf-8")
    hybrid = _manifest(root, [], "auto")
    assert hybrid["tests"][0].get("tier", "d") == "d", hybrid["tests"][0]
    assert _strip_tier(hybrid) == _strip_tier(_manifest(root, [], "d"))
    assert [test["id"] for test in hybrid["tests"]] == _pytest_ids(root, [])


def test_a_non_utf8_encoding_cookie_routes_the_file_to_d(tmp_path: Path) -> None:
    """The defect in full: a cookie'd file whose bytes are *valid UTF-8*.

    A genuinely latin-1 file usually holds bytes that are not valid UTF-8, so `read_to_string`
    already refuses it. The dangerous file is the one that decodes cleanly **both ways, into
    different text** — here the two bytes ``C3 A9``, which are ``é`` to a UTF-8 reader and
    ``Ã©`` to the latin-1 reader the cookie asks for. Probed against pytest 8.4.2: pytest
    honours the cookie, so the nodeid is ``test_x[Ã©]``; Tier S read it as UTF-8 and produced
    ``test_x[é]`` — one character wrong, exit 0, nothing reporting a problem.

    Compared between the two tiers only. pytest prints non-ASCII ids through the console
    encoding (``backslashreplace`` on a redirected Windows stdout — see
    ``core.py::_escaping_unencodable_output``), so an ids-vs-stdout comparison would be
    measuring the terminal, not the collector. Tier D *is* the oracle for Tier S, which is the
    question this test is actually about.
    """
    root = tmp_path / "suite"
    root.mkdir()
    (root / "pytest.ini").write_bytes(b"[pytest]\n")
    (root / "test_enc.py").write_bytes(
        b"# -*- coding: latin-1 -*-\nimport pytest\n\n\n"
        b'@pytest.mark.parametrize("s", ["\xc3\xa9"])\ndef test_x(s):\n    pass\n'
    )

    hybrid = _manifest(root, [], "auto")
    oracle = _manifest(root, [], "d")

    assert _strip_tier(hybrid) == _strip_tier(oracle)
    assert hybrid["tests"][0].get("tier", "d") == "d", hybrid["tests"][0]
    # Tier D decodes the cookie the way the interpreter does, so the id carries the two
    # latin-1 characters rather than the single UTF-8 one Tier S used to emit -- and since
    # Phase 4 Task 1 ported `_pytest/compat.py::ascii_escaped`, it carries them **escaped**,
    # which is what pytest's own id is (`_idval_from_value` -> `_ascii_escaped_by_config`).
    assert hybrid["tests"][0]["id"] == "test_enc.py::test_x[" + "\\xc3\\xa9]"


def test_a_falsy_constructor_binding_matches_tier_d_not_pytest(tmp_path: Path) -> None:
    """The second shape where "match Tier D" and "match pytest" are different instructions.

    Found by this review round's own differential, which is the argument for running it on
    generated material rather than only on the corpus: nothing in the corpus binds `__init__`
    to a falsy value. Tier S matches Tier D — its actual contract — and both differ from
    pytest, which errors while instantiating. Recorded here rather than papered over, and
    asserted on **both** sides so that closing the gap fails this test instead of silently
    turning it into a tautology.
    """
    root = tmp_path / "suite"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "test_falsy.py").write_text(_FALSY_CONSTRUCTOR, encoding="utf-8")

    hybrid = _manifest(root, [], "auto")

    assert _strip_tier(hybrid) == _strip_tier(_manifest(root, [], "d"))
    assert [test["id"] for test in hybrid["tests"]] == [
        "test_falsy.py::TestFalsyInit::test_still_collected"
    ]
    assert all(test.get("tier", "d") == "s" for test in hybrid["tests"])

    # pytest instead reports a collection error and collects nothing from the class.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2, proc.stdout
    assert "'NoneType' object is not callable" in proc.stdout, proc.stdout


def test_a_fixture_kwargs_splat_routes_the_directory_to_d(tmp_path: Path) -> None:
    """The splat defect where it actually costs ids: a **conftest** fixture.

    ``@fixture(**{"params": [1, 2]})`` doubles every id in the directory, and the test file
    below it says nothing at all — so a Tier S that read the splat as a plain fixture reported
    one id where the run has two.
    """
    root = tmp_path / "suite"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "conftest.py").write_text(
        'import pytest\n\n\n@pytest.fixture(**{"params": [1, 2], "autouse": True})\n'
        "def flavour(request):\n    return request.param\n",
        encoding="utf-8",
    )
    (root / "test_quiet.py").write_text("def test_q():\n    pass\n", encoding="utf-8")

    hybrid = _manifest(root, [], "auto")

    assert _strip_tier(hybrid) == _strip_tier(_manifest(root, [], "d"))
    assert [test["id"] for test in hybrid["tests"]] == _pytest_ids(root, [])
    assert [test["id"] for test in hybrid["tests"]] == [
        "test_quiet.py::test_q[1]",
        "test_quiet.py::test_q[2]",
    ], "the splat's parameters reached a file that never mentions the fixture"
    assert all(test.get("tier", "d") == "d" for test in hybrid["tests"])


def test_class_level_parametrize_matches_tier_d_and_pytest(tmp_path: Path) -> None:
    """Tier S, Tier D and pytest now agree on class-parametrize id order.

    This test used to assert the opposite -- Tier S matching Tier D while **both** diverged
    from pytest, whose ``[10-1]`` rustest spelled ``[1-10]`` -- and it carried the
    instruction "if the gap is ever closed, this test fails and says so, rather than quietly
    turning into a tautology". Phase 4 Task 1's review closed it: pytest appends each
    ``parametrize`` call's id component to ``CallSpec2._idlist`` in call order, a method's
    own decorator is applied before the enclosing class's mark is unpacked, so the method
    dimension leads the id *and* varies slowest. Both halves are still asserted.
    """
    root = tmp_path / "suite"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "test_grid.py").write_text(_CLASS_PARAMETRIZE, encoding="utf-8")

    hybrid = _manifest(root, [], "auto")
    oracle = _manifest(root, [], "d")

    assert _strip_tier(hybrid) == _strip_tier(oracle)
    assert all(test["tier"] == "s" for test in hybrid["tests"])
    expected = [
        "test_grid.py::TestGrid::test_m[10-1]",
        "test_grid.py::TestGrid::test_m[10-2]",
    ]
    assert [test["id"] for test in hybrid["tests"]] == expected
    assert _pytest_ids(root, []) == expected


def test_a_fully_static_suite_spawns_no_worker(tmp_path: Path) -> None:
    """The speed claim, stated as behaviour rather than as a timing.

    A tree Tier S answers in full must never start an interpreter — which is provable without
    a stopwatch by handing the boundary a ``python_executable`` that cannot be spawned. If any
    worker were started this would raise ``RuntimeError: could not spawn``.
    """
    root = tmp_path / "suite"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for index in range(20):
        (root / f"test_static_{index:02d}.py").write_text(
            f"def test_{index:02d}():\n    pass\n", encoding="utf-8"
        )

    payload = rust.v2_collect(
        str(root), [], "definitely-not-an-interpreter", 4, None, None, True, "auto"
    )
    manifest = json.loads(payload)

    assert len(manifest["tests"]) == 20
    assert all(test["tier"] == "s" for test in manifest["tests"])


def test_the_env_var_forces_tier_d_through_the_cli(tmp_path: Path) -> None:
    """``RUSTEST_V2_COLLECT_TIER=d`` reaches the boundary from a real CLI invocation.

    The Rust tests drive ``TierMode`` directly, so without this the environment variable —
    the only way a *subprocess* can select the control leg — would be untested end to end and
    the differential could be running two identical hybrid legs.
    """
    import os

    root = tmp_path / "suite"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "test_a.py").write_text("def test_one():\n    pass\n", encoding="utf-8")

    def collect(env_value: str | None) -> list[str]:
        env = dict(os.environ)
        env.pop("RUSTEST_V2_COLLECT_TIER", None)
        if env_value is not None:
            env["RUSTEST_V2_COLLECT_TIER"] = env_value
        proc = subprocess.run(
            [sys.executable, "-m", "rustest", "--v2-collect-only"],
            cwd=root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.splitlines()

    assert collect(None) == ["test_a.py::test_one"]
    assert collect("d") == ["test_a.py::test_one"]
    # An unrecognised value is the default, not a usage error.
    assert collect("nonsense") == ["test_a.py::test_one"]
