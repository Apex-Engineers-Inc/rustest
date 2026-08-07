"""Assertion rewriting: the transform, the message format, and the bytecode cache.

The centrepiece is :func:`test_message_is_byte_identical_to_pytest`, a **differential**
against the real pytest installed in this environment rather than against transcribed
expectations. Transcribed strings rot silently — pytest changes a diff header, the copy here
keeps asserting the old one, and the suite stays green while the messages diverge. Driving
pytest's own rewriter over the same source and comparing ``str(AssertionError)`` cannot rot:
if pytest changes the format, this fails on the next upgrade and says exactly which shape.

**What "identical" means here.** The claim is about the *message* — ``str(exc)`` — and not
about the rendered ``E`` lines a terminal shows. Those differ by construction and always
will: pytest renders through ``ExceptionInfo``/``FormattedExcinfo``, rustest's worker
formats with ``traceback.format_exception`` (``core.py::_print_failure_sections`` documents
that as structural rather than byte parity). The message is the part the rewriter owns, and
it is the part that must match.

Memory addresses are normalised. ``<function f at 0x...>`` differs between two *runs of
pytest itself*, so comparing them would assert nothing about the format.
"""

from __future__ import annotations

import ast
import marshal
import os
import re
import struct
import subprocess
import sys
import textwrap
from importlib.util import MAGIC_NUMBER
from pathlib import Path
from typing import Any

import pytest

from rustest import _assertion, _assertion_rewrite

# ---------------------------------------------------------------------------
# the shape corpus
# ---------------------------------------------------------------------------

#: ``name -> function body``. One row per *message shape* the rewriter can produce, not one
#: per operator: ``<`` and ``>`` take the same path, while ``==`` on a str, a list, a dict, a
#: set, a dataclass and a namedtuple take six different ones.
SHAPES: dict[str, str] = {
    # -- the summary line, no specialised explanation ------------------------
    "eq_int": "assert 1 == 2",
    "ne_int": "assert 1 != 1",
    "lt": "assert 5 < 3",
    "chained": "x = 20\nassert 1 < x < 10",
    "chain_of_three": "assert 1 < 2 < 1 < 5",
    "in_list": "assert 4 in [1, 2, 3]",
    "not_in_list": "assert 2 not in [1, 2, 3]",
    "is_none": "x = 5\nassert x is None",
    "is_not_none": "x = None\nassert x is not None",
    "bare_name": "x = []\nassert x",
    "false_literal": "assert False",
    "none_literal": "assert None",
    "binop": "assert 1 + 1 == 3",
    "unary_minus": "x = 1\nassert -x > 0",
    # -- boolean operators, short-circuit included ---------------------------
    "bool_and": "a, b = True, False\nassert a and b",
    "bool_or": "a, b = False, False\nassert a or b",
    "bool_three": "a, b, c = True, True, False\nassert a and b and c",
    "not_x": "x = True\nassert not x",
    "boolop_in_compare": "a = 1\nb = 2\nassert (a and b) == 3",
    # -- `where` clauses: calls and attributes -------------------------------
    "call": "def f(x=1):\n    return x\nassert f(0)",
    "call_kw": "def f(x=1):\n    return x\nassert f(x=0)",
    "call_starred": "def f(*a):\n    return 0\nxs = [1, 2]\nassert f(*xs)",
    "two_calls": "def f(x=1):\n    return x\nassert f(2) == f(3)",
    "len_call": "xs = [1, 2]\nassert len(xs) == 3",
    "attr": "class B:\n    n = 0\n    def __repr__(self): return 'B()'\nassert B().n",
    "nested_attr": "class B:\n    class C:\n        n = 0\n    c = C()\nassert B().c.n",
    "obj_eq": (
        "class B:\n"
        "    def __init__(s, n): s.n = n\n"
        "    def __repr__(s): return f'B({s.n})'\n"
        "assert B(1) == B(2)"
    ),
    # -- specialised `==` explanations ---------------------------------------
    "eq_str": "assert 'foo' == 'bar'",
    "eq_str_long": "assert 'hello world alpha' == 'hello world beta'",
    "eq_str_multiline": r"assert 'a\nb\nc' == 'a\nX\nc'",
    "eq_str_whitespace": "assert '  ' == ' '",
    "eq_str_percent": "assert '50%' == '60%'",
    "eq_fstring": "name = 'bob'\nassert f'hi {name}' == 'hi alice'",
    "eq_list": "assert [1, 2, 3] == [1, 3, 3]",
    "eq_list_len": "assert [1, 2] == [1]",
    "eq_list_long": "assert list(range(30)) == list(range(29))",
    "eq_tuple": "assert (1, 2) == (1, 3)",
    "eq_bytes": "assert b'ab' == b'ac'",
    "eq_dict": "assert {'a': 1, 'b': 2} == {'a': 1, 'b': 3}",
    "eq_dict_extra": "assert {'a': 1} == {'a': 1, 'b': 2}",
    "eq_set": "assert {1, 2, 3} == {1, 2, 4}",
    "gt_set": "assert {1} > {1, 2}",
    "eq_dataclass": (
        "import dataclasses\n"
        "@dataclasses.dataclass\n"
        "class P:\n"
        "    x: int\n"
        "    y: int\n"
        "assert P(1, 2) == P(1, 3)"
    ),
    "eq_namedtuple": (
        "import collections\nP = collections.namedtuple('P', 'x y')\nassert P(1, 2) == P(1, 3)"
    ),
    "in_str": "assert 'zz' in 'abc'",
    "not_in_str": "assert 'b' not in 'abc'",
    # -- the user's own message ----------------------------------------------
    "with_message": "assert 1 == 2, 'custom explanation'",
    "with_message_obj": "assert 1 == 2, ['a', 'b']",
    "with_message_multiline": r"assert 1 == 2, 'line one\nline two'",
}


def _module_source(body: str) -> str:
    """Wrap a body in ``def _t():`` so the asserts run inside a function, as in a real test."""
    return "def _t():\n" + textwrap.indent(body, "    ") + "\n"


#: The two names pytest's ``running_on_ci()`` sniffs (`_pytest/assertion/util.py`), ported
#: verbatim into :func:`rustest._assertion.running_on_ci`.
_CI_MARKERS = ("CI", "BUILD_NUMBER")


@pytest.fixture(autouse=True)
def _off_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every comparison in this module to the **default** explanation.

    ``running_on_ci()`` switches the comparison explanation from ``Use -v to get more diff``
    to a full diff, and GitHub Actions sets ``CI=true`` on every job. Both sides of the
    differential read the live ``os.environ`` in-process, so without this the corpus compares
    one rendering locally and a different one in CI — which is exactly how the full-diff
    divergence below went unnoticed until this branch was first pushed.

    Autouse rather than opt-in: a new shape added to the corpus should inherit the pinned
    environment without its author having to know any of this.
    """
    for marker in _CI_MARKERS:
        monkeypatch.delenv(marker, raising=False)


def _normalise(text: str) -> str:
    """Erase memory addresses — they differ between two runs of the same rewriter."""
    return re.sub(r"0x[0-9a-fA-F]+", "0xADDR", text)


def _run(code: object) -> str:
    namespace: dict[str, Any] = {}
    exec(code, namespace)  # noqa: S102 - the code under test is compiled from this file
    try:
        namespace["_t"]()
    except AssertionError as exc:
        return str(exc)
    raise AssertionError("the shape did not fail; a shape that passes tests nothing")


def _rustest_message(name: str, body: str) -> str:
    tree = _assertion_rewrite.rewrite_source(_module_source(body).encode(), f"<{name}>")
    return _run(compile(tree, f"<{name}>", "exec", dont_inherit=True))


def _pytest_message(name: str, body: str) -> str:
    """Drive **pytest's own** rewriter and its own message machinery over the same source.

    ``_reprcompare`` is normally installed per-test by pytest's plugin from a live ``Config``;
    the stand-in below supplies the four things it reads (assertion verbosity, the truncation
    limits, a no-op highlighter, and the assert mode) at their defaults, which is the
    configuration rustest pins. Calling pytest's ``callbinrepr`` steps rather than
    reimplementing them is the point: this side of the differential must be pytest's code.
    """
    from _pytest.assertion import truncate as pytest_truncate
    from _pytest.assertion import util as pytest_util
    from _pytest.assertion.rewrite import AssertionRewriter

    source = _module_source(body).encode()
    tree = ast.parse(source, filename=f"<{name}>")
    AssertionRewriter(f"<{name}>", None, source).run(tree)
    code = compile(tree, f"<{name}>", "exec", dont_inherit=True)

    class _Writer:
        def _highlight(self, source: str, lexer: str = "python") -> str:
            return source

    class _Config:
        VERBOSITY_ASSERTIONS = "assertions"

        def get_verbosity(self, kind: object = None) -> int:
            return 0

        def getini(self, name: str) -> object:
            return {"truncation_limit_lines": 8, "truncation_limit_chars": 640}.get(name, False)

        def get_terminal_writer(self) -> _Writer:
            return _Writer()

        def getvalue(self, name: str) -> str:
            return "rewrite"

    config = _Config()

    def callbinrepr(op: str, left: object, right: object) -> str | None:
        lines = pytest_util.assertrepr_compare(config, op, left, right)
        if not lines:
            return None
        if not pytest_util.running_on_ci():
            lines = pytest_truncate._truncate_explanation(lines, 8, 640)
        return "\n~".join(line.replace("\n", "\\n") for line in lines).replace("%", "%%")

    saved = pytest_util._reprcompare, pytest_util._config
    pytest_util._reprcompare = callbinrepr
    pytest_util._config = config  # pyright: ignore[reportAttributeAccessIssue]
    try:
        return _run(code)
    finally:
        pytest_util._reprcompare, pytest_util._config = saved


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_message_is_byte_identical_to_pytest(name: str) -> None:
    """rustest's ``AssertionError`` message equals pytest's, for every shape in the corpus.

    Compared as **line lists**, not as one blob. The messages run to a dozen lines, and a
    string comparison of two of them makes pytest repr the whole thing inline — every newline
    an escaped ``\\n``, wrapped across the terminal — which is unreadable at exactly the
    moment you need to read it. Line lists get a line-oriented diff instead.
    """
    body = SHAPES[name]
    ours = _normalise(_rustest_message(name, body)).splitlines()
    theirs = _normalise(_pytest_message(name, body)).splitlines()
    assert ours == theirs


def test_the_full_diff_branch_diverges_from_pytest() -> None:
    """The known divergence, pinned rather than hidden by :func:`_off_ci`.

    When ``CI`` or ``BUILD_NUMBER`` is set, ``running_on_ci()`` replaces
    ``Use -v to get more diff`` with a full diff — and on **that** branch the two runners do
    not agree. `_assertion.py` says so in as many words (l. 487-490: "pytest uses its own
    vendored ``PrettyPrinter`` here; ``pprint.pformat`` is the stdlib one pytest's is derived
    from ... this branch is unreachable at the pinned verbosity except on CI"). It stopped
    being unreachable the moment this project got a CI run, because GitHub Actions sets
    ``CI=true``.

    The difference is expansion: stdlib ``pprint.pformat([1, 3, 3])`` is ``'[1, 3, 3]'``,
    while pytest's vendored printer always breaks a collection one element per line, so its
    diff is element-wise and ours is whole-list. Closing it means porting
    ``_pytest/_io/pprint.py`` (673 lines) the way ``approx`` was ported.

    **This test asserts the divergence, so it fails the day someone fixes it** — at which
    point delete it, drop :func:`_off_ci`, and let the corpus above cover both branches.
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("CI", "true")
        body = SHAPES["eq_list"]
        ours = _normalise(_rustest_message("eq_list", body))
        theirs = _normalise(_pytest_message("eq_list", body))
    finally:
        monkeypatch.undo()

    # Both take the full-diff branch: neither says "Use -v".
    assert "Use -v to get more diff" not in ours
    assert "Use -v to get more diff" not in theirs
    assert "Full diff:" in ours and "Full diff:" in theirs

    # And there they part company, in the one way documented above.
    assert ours != theirs, (
        "the full-diff branch now matches pytest -- port complete? "
        "delete this test and the _off_ci fixture"
    )
    assert "[1, 3, 3]" in ours, "ours should keep the list on one line (stdlib pprint)"
    assert "    3,\n" in theirs, "pytest's should be expanded one element per line"


def test_the_corpus_covers_every_shape_the_plan_names() -> None:
    """The plan names ``==``, ``in``, ``is``, comparisons and boolean ops; all are present.

    A guard on the corpus rather than on the code: the differential above is only as good as
    the shapes fed to it, and a corpus that quietly lost its ``is`` rows would still pass
    every row it kept.
    """
    joined = "\n".join(SHAPES.values())
    for fragment in ("==", " in ", " not in ", " is ", " is not ", " and ", " or ", "not ", " < "):
        assert fragment in joined, f"the corpus no longer exercises {fragment!r}"
    assert len(SHAPES) >= 45


# ---------------------------------------------------------------------------
# the transform's own guarantees
# ---------------------------------------------------------------------------


def test_operands_are_evaluated_exactly_once() -> None:
    """The property the whole "bind to a temporary" design exists for.

    A rewrite that re-evaluated operands to build the message would consume two items from an
    iterator here and report a value the assertion never compared. Both halves are asserted:
    the call count, and the message naming the value that was actually tested.
    """
    body = "assert next(it) == 99"
    tree = _assertion_rewrite.rewrite_source(_module_source(body).encode(), "<once>")
    namespace: dict[str, Any] = {"it": iter([1, 2, 3])}
    exec(compile(tree, "<once>", "exec", dont_inherit=True), namespace)  # noqa: S102
    with pytest.raises(AssertionError) as excinfo:
        namespace["_t"]()
    assert "assert 1 == 99" in str(excinfo.value)
    assert list(namespace["it"]) == [2, 3], "exactly one item was consumed"


def test_boolean_operators_still_short_circuit() -> None:
    """``assert a and b()`` must not call ``b`` when ``a`` is falsy."""
    body = "assert flag and boom()"
    tree = _assertion_rewrite.rewrite_source(_module_source(body).encode(), "<short>")
    calls: list[int] = []

    def boom() -> bool:
        calls.append(1)
        return True

    namespace: dict[str, Any] = {"flag": False, "boom": boom}
    exec(compile(tree, "<short>", "exec", dont_inherit=True), namespace)  # noqa: S102
    with pytest.raises(AssertionError):
        namespace["_t"]()
    assert calls == [], "the right operand was evaluated despite the left being falsy"


def test_a_passing_assertion_is_unchanged() -> None:
    """Rewriting must not change *behaviour*, only the message on failure."""
    tree = _assertion_rewrite.rewrite_source(_module_source("assert 1 == 1\nreturn 7").encode())
    namespace: dict[str, Any] = {}
    exec(compile(tree, "<pass>", "exec", dont_inherit=True), namespace)  # noqa: S102
    assert namespace["_t"]() == 7


def test_pytest_dont_rewrite_in_the_docstring_disables_the_transform() -> None:
    """pytest's own opt-out marker, honoured — a bare ``AssertionError`` comes back."""
    source = '"""PYTEST_DONT_REWRITE."""\n\n\ndef _t():\n    assert 1 == 2\n'
    tree = _assertion_rewrite.rewrite_source(source.encode(), "<optout>")
    namespace: dict[str, Any] = {}
    exec(compile(tree, "<optout>", "exec", dont_inherit=True), namespace)  # noqa: S102
    with pytest.raises(AssertionError) as excinfo:
        namespace["_t"]()
    assert str(excinfo.value) == ""


def test_a_walrus_inside_an_assert_refuses_the_whole_file() -> None:
    """The documented refusal: no rewriting rather than a possibly-stale repr.

    pytest tracks walrus rebinding through ``variables_overwrite``; that bookkeeping is not
    ported, so a file using ``:=`` inside an ``assert`` keeps plain asserts. Asserted at the
    *file* level, because the refusal is per file — the second assert here is ordinary and
    is still left alone.
    """
    source = "def _t():\n    assert (n := 2) == 3\n\n\ndef _u():\n    assert 1 == 2\n"
    tree = _assertion_rewrite.rewrite_source(source.encode(), "<walrus>")
    namespace: dict[str, Any] = {}
    exec(compile(tree, "<walrus>", "exec", dont_inherit=True), namespace)  # noqa: S102
    for name in ("_t", "_u"):
        with pytest.raises(AssertionError) as excinfo:
            namespace[name]()
        assert str(excinfo.value) == "", f"{name} was rewritten despite the file's walrus"


def test_the_future_import_stays_first() -> None:
    """``from __future__ import ...`` must remain the first statement, or the module dies.

    The rewriter inserts its helper imports at the top; getting the insertion point wrong is
    a ``SyntaxError`` at compile time for every module that uses a future import, which is
    most of this repository's own test files.
    """
    source = '"""Doc."""\n\nfrom __future__ import annotations\n\n\ndef _t():\n    assert 1 == 2\n'
    tree = _assertion_rewrite.rewrite_source(source.encode(), "<future>")
    code = compile(tree, "<future>", "exec", dont_inherit=True)
    namespace: dict[str, Any] = {}
    exec(code, namespace)  # noqa: S102
    with pytest.raises(AssertionError) as excinfo:
        namespace["_t"]()
    assert "assert 1 == 2" in str(excinfo.value)


def test_the_docstring_survives_the_inserted_imports() -> None:
    """An import placed above the docstring would silently delete ``__doc__``."""
    source = '"""Kept."""\n\n\ndef _t():\n    assert 1 == 2\n'
    tree = _assertion_rewrite.rewrite_source(source.encode(), "<doc>")
    namespace: dict[str, Any] = {}
    exec(compile(tree, "<doc>", "exec", dont_inherit=True), namespace)  # noqa: S102
    assert namespace["__doc__"] == "Kept."


def test_every_helper_the_rewriter_emits_exists() -> None:
    """The generated code calls helpers **by name**; a rename is otherwise silent.

    Nothing imports ``@rustest_ar._saferepr`` at type-check time, so a helper renamed in
    ``_assertion.py`` compiles cleanly and only fails when an assertion fails — the worst
    possible moment to discover it. This walks the rewritten AST of the whole corpus and
    resolves every attribute reached through the helper alias.
    """
    emitted: set[str] = set()
    for name, body in SHAPES.items():
        tree = _assertion_rewrite.rewrite_source(_module_source(body).encode(), f"<{name}>")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "@rustest_ar"
            ):
                emitted.add(node.attr)
    assert emitted, "the corpus produced no helper calls at all — the walk is broken"
    for helper in sorted(emitted):
        assert hasattr(_assertion, helper), f"the rewriter emits a call to a missing {helper!r}"


# ---------------------------------------------------------------------------
# the bytecode cache
# ---------------------------------------------------------------------------

_KEY = "0" * 64
_FILE = "/repo/tests/test_cached.py"


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Point the module's cache at a temporary directory and restore it afterwards."""
    previous = _assertion_rewrite._cache_dir
    _assertion_rewrite._cache_dir = str(tmp_path / "assert")
    try:
        yield tmp_path / "assert"
    finally:
        _assertion_rewrite._cache_dir = previous


def _cache_name(fn: str, key: str) -> str:
    """The file name `_cache_path` builds, so the tests name it once."""
    return f"{_assertion_rewrite._path_tag(fn)}-{key}.pyc"


def _some_code() -> Any:
    return compile("x = 1", "<cache>", "exec", dont_inherit=True)


def test_a_written_entry_reads_back(cache_dir: Path) -> None:
    assert _assertion_rewrite._write_cached(_FILE, _KEY, _some_code())
    code = _assertion_rewrite._read_cached(_FILE, _KEY)
    assert code is not None
    namespace: dict[str, Any] = {}
    exec(code, namespace)  # noqa: S102
    assert namespace["x"] == 1


def test_a_missing_entry_is_a_miss(cache_dir: Path) -> None:
    assert _assertion_rewrite._read_cached(_FILE, "1" * 64) is None


def test_bytecode_from_another_interpreter_is_a_miss(cache_dir: Path) -> None:
    """The ``MAGIC_NUMBER`` guard: a ``.pyc`` from another CPython is unloadable, not stale.

    This is the failure the manifest cache key *cannot* catch — the tree, the config and the
    rustest build are all identical, and the artefact is still garbage.
    """
    assert _assertion_rewrite._write_cached(_FILE, _KEY, _some_code())
    path = cache_dir / _cache_name(_FILE, _KEY)
    blob = path.read_bytes()
    forged = b"\x00\x00\x00\x00" + blob[len(MAGIC_NUMBER) :]
    _ = path.write_bytes(forged)
    assert _assertion_rewrite._read_cached(_FILE, _KEY) is None


def test_bytecode_from_another_rewriter_epoch_is_a_miss(cache_dir: Path) -> None:
    """The ``REWRITE_EPOCH`` guard, and the reason it exists separately from the key.

    A developer changing the transform rebuilds this file many times without moving
    ``CARGO_PKG_VERSION``, so the manifest key is identical across every one of those builds.
    Without the epoch, the *previous* transform's bytecode would be executed by the *current*
    runtime helpers — the one way this cache produces a wrong message rather than a slow run.
    """
    assert _assertion_rewrite._write_cached(_FILE, _KEY, _some_code())
    path = cache_dir / _cache_name(_FILE, _KEY)
    blob = path.read_bytes()
    prefix = MAGIC_NUMBER + _assertion_rewrite._CACHE_MAGIC
    forged = (
        prefix + struct.pack("<I", _assertion_rewrite.REWRITE_EPOCH + 1) + blob[len(prefix) + 4 :]
    )
    _ = path.write_bytes(forged)
    assert _assertion_rewrite._read_cached(_FILE, _KEY) is None


@pytest.mark.parametrize(
    ("label", "blob"),
    [
        ("empty", b""),
        ("header only", MAGIC_NUMBER + b"RSTA" + struct.pack("<I", 1)),
        ("foreign pyc", MAGIC_NUMBER + b"\x00" * 12 + b"garbage"),
        ("unmarshallable body", MAGIC_NUMBER + b"RSTA" + struct.pack("<I", 1) + b"\xff\xfe\xfd"),
        ("not bytecode", MAGIC_NUMBER + b"RSTA" + struct.pack("<I", 1) + marshal.dumps([1, 2, 3])),
    ],
)
def test_a_damaged_entry_is_a_miss_not_an_error(cache_dir: Path, label: str, blob: bytes) -> None:
    """Every damaged shape misses. A cache that can *fail a run* is worse than no cache.

    ``not bytecode`` is the subtle one: the body unmarshals perfectly and is simply not a
    code object, so a reader that trusted ``marshal`` would hand a list to ``exec``.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    _ = (cache_dir / _cache_name(_FILE, _KEY)).write_bytes(blob)
    assert _assertion_rewrite._read_cached(_FILE, _KEY) is None, label

    # ...and one bad write is not permanent: the next write replaces it.
    assert _assertion_rewrite._write_cached(_FILE, _KEY, _some_code())
    assert _assertion_rewrite._read_cached(_FILE, _KEY) is not None


def test_writing_a_new_key_prunes_that_files_superseded_artefacts(cache_dir: Path) -> None:
    """One artefact per file, not one per edit.

    Keyed by content alone, every edit to a test file would leave its previous ``.pyc``
    behind forever — an unbounded directory nobody looks at. The path tag in the file name is
    what makes the store bounded by the tree's *contents* rather than by its history, exactly
    as the manifest cache is bounded by a directory's contents (`src/engine/manifest_cache.rs`).
    """
    for key in ("a" * 64, "b" * 64, "c" * 64):
        assert _assertion_rewrite._write_cached(_FILE, key, _some_code())

    names = sorted(p.name for p in cache_dir.iterdir())
    assert names == [_cache_name(_FILE, "c" * 64)], names
    # ...and the surviving entry is the one just written, not an arbitrary survivor.
    assert _assertion_rewrite._read_cached(_FILE, "c" * 64) is not None
    assert _assertion_rewrite._read_cached(_FILE, "a" * 64) is None


def test_pruning_never_touches_another_files_artefacts(cache_dir: Path) -> None:
    """The prune is scoped by path tag; a shared directory holds every file's entry."""
    other = "/repo/tests/test_other.py"
    assert _assertion_rewrite._write_cached(other, "a" * 64, _some_code())
    assert _assertion_rewrite._write_cached(_FILE, "b" * 64, _some_code())
    assert _assertion_rewrite._write_cached(_FILE, "c" * 64, _some_code())

    names = sorted(p.name for p in cache_dir.iterdir())
    assert names == sorted([_cache_name(other, "a" * 64), _cache_name(_FILE, "c" * 64)]), names


def test_a_warm_write_free_run_does_not_list_the_cache_directory(cache_dir: Path) -> None:
    """Pruning is **lazy**: it happens on a miss, never on a hit.

    A fully warm run must not pay a directory listing per file — the same trade
    `src/engine/manifest_cache.rs` makes when it declines to `stat` every cached file to tidy
    sooner. Asserted by making the listing itself fail: a hit must not reach it.
    """
    assert _assertion_rewrite._write_cached(_FILE, _KEY, _some_code())

    def explode(_path: str) -> list[str]:
        raise AssertionError("a cache hit must not list the cache directory")

    original = os.listdir
    os.listdir = explode  # pyright: ignore[reportAttributeAccessIssue]
    try:
        assert _assertion_rewrite._read_cached(_FILE, _KEY) is not None
    finally:
        os.listdir = original  # pyright: ignore[reportAttributeAccessIssue]


def test_the_cache_can_be_switched_off(tmp_path: Path) -> None:
    """``install_hook(None)`` disables the store — what the unit tests above rely on."""
    previous = _assertion_rewrite._cache_dir
    _assertion_rewrite._cache_dir = None
    try:
        assert not _assertion_rewrite._write_cached(_FILE, _KEY, _some_code())
        assert _assertion_rewrite._read_cached(_FILE, _KEY) is None
    finally:
        _assertion_rewrite._cache_dir = previous


def test_no_temporary_file_survives_a_write(cache_dir: Path) -> None:
    """The write is rename-based; a leftover ``.tmp-<pid>`` would accumulate per run."""
    assert _assertion_rewrite._write_cached(_FILE, _KEY, _some_code())
    assert [p.name for p in cache_dir.iterdir()] == [_cache_name(_FILE, _KEY)]


# ---------------------------------------------------------------------------
# the import hook
# ---------------------------------------------------------------------------


def test_the_hook_rewrites_only_registered_files(tmp_path: Path) -> None:
    """The Tier S / Tier D split, end to end through a real import.

    Two identical modules, one registered and one not. The registered one must produce a
    pytest-shaped message and the other a bare ``AssertionError`` — which is precisely the
    plan's "Tier D files keep plain asserts", asserted rather than described.
    """
    source = "def check():\n    assert 1 == 2\n"
    _ = (tmp_path / "mod_rewritten.py").write_text(source, encoding="utf-8")
    _ = (tmp_path / "mod_plain.py").write_text(source, encoding="utf-8")

    script = f"""
import sys
sys.path.insert(0, {str(tmp_path)!r})
from rustest import _assertion_rewrite
_assertion_rewrite.install_hook(None)
_assertion_rewrite.register({str(tmp_path / "mod_rewritten.py")!r}, {_KEY!r})
import mod_rewritten, mod_plain
for module in (mod_rewritten, mod_plain):
    try:
        module.check()
    except AssertionError as exc:
        print(f"{{module.__name__}}={{str(exc)!r}}")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert "mod_rewritten='assert 1 == 2'" in proc.stdout, proc.stdout
    assert "mod_plain=''" in proc.stdout, proc.stdout


def test_installing_the_hook_twice_adds_one_finder(tmp_path: Path) -> None:
    """Idempotent, so a re-initialised worker does not accumulate meta-path entries."""
    before = list(sys.meta_path)
    try:
        first = _assertion_rewrite.install_hook(None)
        second = _assertion_rewrite.install_hook(None)
        assert first is second
        assert sum(isinstance(f, _assertion_rewrite.RewriteHook) for f in sys.meta_path) == 1
    finally:
        sys.meta_path[:] = before
        _assertion_rewrite.reset()


def test_an_unregistered_import_is_not_claimed(tmp_path: Path) -> None:
    """``find_spec`` returns ``None`` for anything not registered, so the hook is inert.

    It sits at the front of ``sys.meta_path`` for the whole worker; if it ever claimed a
    module it had not been told about, it would be loading arbitrary third-party code through
    a rewriter.
    """
    hook = _assertion_rewrite.RewriteHook()
    assert hook.find_spec("json", None, None) is None
    assert hook.find_spec("rustest.core", None, None) is None
