r"""AST assertion rewriting: the transform, the import hook, and the bytecode cache.

A plain ``assert a == b`` raises ``AssertionError`` with **no arguments**, so the only thing
a runner can show is the source line. pytest's answer, adopted here, is to rewrite the
module's AST before compiling it, so the generated code evaluates each operand once, keeps
it in a temporary, and — only when the assertion fails — builds the message from those
values. This module is the transform half; :mod:`rustest._assertion` is the runtime half
the generated code calls.

## Provenance

Adapted from **pytest 8.4.2 ``_pytest/assertion/rewrite.py``**. What is taken verbatim in
structure is the ``AssertionRewriter`` visitor — ``visit_Assert``, ``visit_Compare``,
``visit_BoolOp``, ``visit_UnaryOp``, ``visit_BinOp``, ``visit_Call``, ``visit_Attribute``,
``visit_Name``, ``visit_Starred``, ``generic_visit``, and the format-context machinery
(``push_format_context``/``pop_format_context``/``explanation_param``) that assembles a
``%``-formatted explanation. Reproducing the visitor *exactly* is what makes the messages
match; a paraphrase would differ on the nesting of ``where`` clauses, which is precisely
the part that is hard to review by eye.

Three things are **not** taken:

* **the walrus bookkeeping** (``variables_overwrite``, ``visit_NamedExpr``, the
  ``NamedExpr`` special cases inside ``visit_Compare``/``visit_BoolOp``/``visit_Call``).
  pytest carries it so a name reassigned by ``:=`` inside the assertion reprs as its new
  value. A file containing ``:=`` inside an ``assert`` is **refused for rewriting** here
  (:func:`_uses_walrus_in_assert`) and keeps its plain asserts, which is a worse *message*
  and never a wrong one — where a partial port would have produced a confidently wrong
  repr. Recorded in the report's parity table;
* **the ``pytest_assertion_pass`` hook path**, which is opt-in via an ini option rustest
  does not have. Only the ``else:`` branch of ``visit_Assert`` is ported;
* **``PYTEST_DONT_REWRITE``** is honoured (a module docstring containing it disables the
  rewrite) but ``PYTEST_DONT_REWRITE`` in *conftest* registration and the plugin-rewrite
  machinery are not: rustest rewrites test modules and nothing else.

## The mechanism, and why it is a meta-path hook

``_v2_worker.py::import_test_module`` imports test modules by **dotted name** through
``importlib.import_module`` — the ``ImportMode.prepend`` port that makes ``import conftest``
inside a test reach the same module object the worker has. Any rewrite must therefore
intercept the ordinary import machinery rather than replace it, and that is exactly what
pytest's ``AssertionRewritingHook`` does: a ``MetaPathFinder`` that delegates ``find_spec``
to ``importlib.machinery.PathFinder``, and — only for files it has been told to rewrite —
returns a spec whose loader is the hook itself.

The alternative considered and rejected was a ``.pyc`` swap: compile the rewritten module
and drop the result into ``__pycache__`` where CPython would find it. It is fewer moving
parts and it is wrong in a way that cannot be contained — that ``.pyc`` is keyed by source
mtime and nothing else, so a subsequent ``python -c "import test_foo"``, a ``pytest`` run,
or any other consumer of the same tree silently executes rustest's rewritten bytecode.
Rewritten modules must never escape the worker, and a hook confines them to the process
that installed it.

## The cache, and the key it rides on

Compiling is the expensive half — for the 5 000-test benchmark suite, parsing and compiling
500 rewritten modules costs an order of magnitude more than reading 500 ``.pyc``s — so the
compiled code object is cached at::

    <rootdir>/.rustest_cache/v2-assert/<path-tag>-<key>.pyc

where ``<path-tag>`` is a short digest of the source path — it makes the store bounded by
the tree's *contents* rather than by its history, see :func:`_cache_path` — and ``<key>`` is
**the Task 2 manifest cache key for that file**, handed down by the
orchestrator on the ``collect_file`` request (``src/v2/protocol.rs``). Reusing that key is
the point: it already covers the file's bytes, the resolved config, the conftest chain, the
stdlib shadow set and the rustest build, so every invalidation the manifest cache gets, the
bytecode cache gets for free and by construction.

Two things the manifest key cannot cover are added to the file's own header, because they
are properties of the *interpreter and the rewriter* rather than of the tree:

* ``importlib.util.MAGIC_NUMBER`` — bytecode is CPython-version-specific, and a ``.pyc``
  from another interpreter is not a stale answer but an unloadable one;
* :data:`REWRITE_EPOCH` — hand-bumped when the transform changes, exactly as
  ``TIER_S_EPOCH`` is for Tier S's extraction rules.

A header mismatch, a truncated file, an unmarshallable body: all are **misses**, never
errors. A cache that can fail a run is worse than no cache.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.machinery
import importlib.util
import itertools

# `marshal` is CPython's own bytecode container -- it is what `.pyc` files hold and what
# `importlib`'s `SourceLoader` uses, and it is what pytest's `_read_pyc`/`_write_pyc` use for
# this exact artefact. It is **not** a data format for untrusted input, and it is not used as
# one here: the only writer is `_write_cached` in this file, the only reader is
# `_read_cached`, and both address a file inside the tree's own `.rustest_cache/` -- the
# directory a user already deletes to reset rustest and which rustest already fills with
# executable-equivalent artefacts by virtue of running that tree's tests. The header check in
# `_read_cached` exists to reject a *stale or foreign* artefact (wrong interpreter, wrong
# rewriter epoch), never to make an attacker-supplied one safe. Anyone who can write into
# `.rustest_cache/` can already write into the test files it was derived from.
import marshal
import os
import struct
import sys
import types
from collections.abc import Iterable, Sequence
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from typing import Final

from . import _assertion as _assertion  # re-exported: generated code imports it by name

__all__ = [
    "REWRITE_EPOCH",
    "install_hook",
    "register",
    "rewrite_source",
]

#: Bump on **any** change to the transform or to the runtime helpers it emits calls to.
#:
#: The manifest cache key covers the tree and the build; it does not and cannot cover "the
#: rewriter now emits a different call", because a developer rebuilding this file a hundred
#: times never moves ``CARGO_PKG_VERSION``. Without this constant, a stale ``.pyc`` written
#: by the previous transform would be executed by the current runtime helpers — the one
#: failure mode of this cache that produces a *wrong message* rather than a slow run.
REWRITE_EPOCH: Final = 1

#: The name the generated code binds this module's runtime half to. Deliberately not a legal
#: Python identifier (``@``), so it cannot collide with anything the test module defines —
#: pytest uses ``@pytest_ar`` for the same reason.
_HELPER_MODULE_ALIAS: Final = "@rustest_ar"
_BUILTINS_ALIAS: Final = "@py_builtins"

#: Marks a cache file as this rewriter's. Read back and compared before the body is
#: unmarshalled, so a file that is a valid ``.pyc`` of something else is a miss.
_CACHE_MAGIC: Final = b"RSTA"

#: `ast.UnaryOp.op` is typed as the base `unaryop`, so the key type has to be the base
#: class; without the annotation the inferred key union rejects every lookup.
_UNARY_MAP: Final[dict[type[ast.AST], str]] = {
    ast.Not: "not %s",
    ast.Invert: "~%s",
    ast.USub: "-%s",
    ast.UAdd: "+%s",
}

#: Binary **and** comparison operators in one table, exactly as pytest's is: `visit_BinOp`
#: and `visit_Compare` both index it, and `ast.operator`/`ast.cmpop` share no base beyond
#: `ast.AST` — hence the key type. Same reasoning as `_UNARY_MAP`.
_BINOP_MAP: Final[dict[type[ast.AST], str]] = {
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.BitAnd: "&",
    ast.LShift: "<<",
    ast.RShift: ">>",
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%%",  # escaped for the % formatting the explanation goes through
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Pow: "**",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
    ast.MatMult: "@",
}


# ---------------------------------------------------------------------------
# registry — what the orchestrator told us to rewrite
# ---------------------------------------------------------------------------

#: ``normcased absolute path -> cache key (64 hex chars)``.
#:
#: Populated by :func:`register` from the ``assert_key`` field of each ``collect_file``
#: request, i.e. **only** for files the Rust static tier certified as statically analysable.
#: A file that is not in here is imported by the ordinary machinery and keeps its plain
#: asserts — that is the plan's "Tier D files keep plain asserts", enforced by the registry
#: being empty for them rather than by a check somewhere in the transform.
_REGISTRY: dict[str, str] = {}

#: Where the compiled artefacts go; set by :func:`install_hook`.  Lower-case because it is
#: reassigned at run time — a worker sets it once from the rootdir `init` carried, and the
#: unit tests set it to `None` to exercise the transform rather than a previous test's `.pyc`.
_cache_dir: str | None = None


def register(path: str, key: str) -> None:
    """Mark *path* as rewrite-eligible, with *key* as its bytecode cache key."""
    _REGISTRY[os.path.normcase(os.path.abspath(path))] = key


def registered_key(path: str) -> str | None:
    """The cache key for *path*, or ``None`` when it is not rewrite-eligible."""
    return _REGISTRY.get(os.path.normcase(os.path.abspath(path)))


def reset() -> None:
    """Forget every registration. For tests; a worker registers and never unregisters."""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# the import hook — adapted from `AssertionRewritingHook`
# ---------------------------------------------------------------------------


class RewriteHook(MetaPathFinder, Loader):
    """Finds registered test modules and loads them from rewritten bytecode.

    Adapted from ``_pytest/assertion/rewrite.py::AssertionRewritingHook``, minus everything
    that serves pytest's plugin/conftest rewriting: there is no ``_must_rewrite`` set, no
    ``_marked_for_rewrite_cache``, no session, and no fnmatch over ``python_files`` — the
    orchestrator has already decided, per file, and said so on the wire.

    ``find_spec`` delegates to ``PathFinder`` rather than reimplementing module lookup, so
    packages, namespace packages, ``sys.path`` order and the ``prepend`` import mode all
    keep behaving exactly as they do without the hook. Only the *loader* is swapped, and
    only for a path in :data:`_REGISTRY`.
    """

    def find_spec(
        self,
        name: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> ModuleSpec | None:
        spec = importlib.machinery.PathFinder.find_spec(name, path, target)
        if spec is None or spec.origin is None:
            return None
        if not isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            # An extension module, a namespace package, a zipimport entry: there is no
            # source to rewrite and no business pretending otherwise.
            return None
        if registered_key(spec.origin) is None:
            return None
        return importlib.util.spec_from_file_location(
            name,
            spec.origin,
            loader=self,
            submodule_search_locations=spec.submodule_search_locations,
        )

    def create_module(self, spec: ModuleSpec) -> types.ModuleType | None:
        return None  # default semantics

    def exec_module(self, module: types.ModuleType) -> None:
        assert module.__spec__ is not None
        assert module.__spec__.origin is not None
        fn = module.__spec__.origin
        key = registered_key(fn)
        assert key is not None, "find_spec only claims registered paths"

        code = _read_cached(fn, key)
        if code is None:
            code = _compile_rewritten(fn)
            _ = _write_cached(fn, key, code)
        exec(code, module.__dict__)

    def get_data(self, pathname: str | bytes) -> bytes:
        """Support ``pkgutil.get_data`` on a rewritten module, as pytest's hook does."""
        with open(pathname, "rb") as handle:
            return handle.read()


def install_hook(cache_dir: str | None) -> RewriteHook:
    """Put a :class:`RewriteHook` at the front of ``sys.meta_path`` and return it.

    Idempotent: a second call replaces nothing and returns the hook already installed, so a
    worker that is re-initialised does not accumulate finders.

    ``cache_dir`` of ``None`` disables the bytecode cache and rewrites on every import,
    which is what the unit tests use — they must exercise the transform, not a ``.pyc``
    written by a previous test.
    """
    global _cache_dir
    _cache_dir = cache_dir
    for finder in sys.meta_path:
        if isinstance(finder, RewriteHook):
            return finder
    hook = RewriteHook()
    sys.meta_path.insert(0, hook)
    return hook


# ---------------------------------------------------------------------------
# the bytecode cache
# ---------------------------------------------------------------------------


def _path_tag(fn: str) -> str:
    """A short, stable digest of *fn*, used to group one file's artefacts together.

    Not a security property and not a cache key — the key is the filename's second half. This
    exists only so :func:`_write_cached` can find and remove the artefacts a file's *previous*
    contents left behind.
    """
    normalised = os.path.normcase(os.path.abspath(fn)).encode("utf-8", "surrogateescape")
    return hashlib.blake2b(normalised, digest_size=8).hexdigest()


def _cache_path(fn: str, key: str) -> str | None:
    """``<tag>-<key>.pyc``: the file it came from, then the state that file was in.

    The two-part name is what makes pruning possible. Keyed by content alone, an edited test
    file would leave its previous artefact behind **forever** — one dead ``.pyc`` per edit,
    per file, growing without bound in a directory nobody looks at. The manifest cache does
    not have that problem because it stores one entry *per path*, overwritten in place
    (`src/v2/manifest_cache.rs`, "keyed by file name within the shard, so the store is bounded
    by the directory's contents rather than by its history"). This reproduces that property
    with a filename instead of a shard.
    """
    if _cache_dir is None:
        return None
    return os.path.join(_cache_dir, f"{_path_tag(fn)}-{key}.pyc")


def _header() -> bytes:
    """``MAGIC_NUMBER || "RSTA" || REWRITE_EPOCH`` — everything the key cannot carry."""
    return importlib.util.MAGIC_NUMBER + _CACHE_MAGIC + struct.pack("<I", REWRITE_EPOCH)


def _read_cached(fn: str, key: str) -> types.CodeType | None:
    """The cached code object for *fn* at *key*, or ``None`` for any reason at all.

    Every failure is a miss: a wrong header, a short file, a corrupt body, a permission
    error. The cost of a miss is one compile; the cost of raising here is a run that fails
    because of its cache.
    """
    path = _cache_path(fn, key)
    if path is None:
        return None
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError:
        return None
    header = _header()
    if not blob.startswith(header):
        return None
    try:
        code = marshal.loads(blob[len(header) :])
    except Exception:
        return None
    return code if isinstance(code, types.CodeType) else None


def _write_cached(fn: str, key: str, code: types.CodeType) -> bool:
    """Write *code* for *fn* under *key*, and drop that file's superseded artefacts.

    Written to a per-process temporary and renamed, for the reason
    ``src/v2/manifest_cache.rs`` documents at length: two workers of the same pool compile
    the same file only in the stem-collision case, but a half-written ``.pyc`` read by the
    other one would be a corrupt import rather than a miss. ``os.replace`` is atomic for
    readers on both POSIX and Windows.

    **Pruning is lazy, in the manifest cache's exact sense.** This runs only on a miss — that
    is, only when the file's content, config or conftest chain actually changed — and it
    removes only the artefacts of *that* file's earlier states. A run that hits every entry
    neither lists the directory nor unlinks anything, which is the trade
    `src/v2/manifest_cache.rs` makes ("paying a `stat` per cached file on every warm run to
    tidy it sooner is precisely the trade this cache exists to avoid").

    The residual is the manifest cache's too, and bounded the same way: the artefacts of a
    test file that has been **deleted** survive, because nothing recompiles a file that is
    gone. They are inert — a `.pyc` is only ever opened by the exact name a live path and key
    produce — and a user who wants them gone deletes `.rustest_cache`, which is already the
    documented reset for everything else in it.
    """
    path = _cache_path(fn, key)
    if path is None:
        return False
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as handle:
            _ = handle.write(_header())
            _ = handle.write(marshal.dumps(code))
        os.replace(tmp, path)
    except OSError:
        # A read-only tree, a full disk, or — on Windows — a scanner holding the destination
        # open. None of them is the user's problem on this path: the run has the code object
        # in hand and simply does not get to keep it.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    _prune_superseded(fn, keep=os.path.basename(path))
    return True


def _prune_superseded(fn: str, keep: str) -> None:
    """Remove *fn*'s artefacts other than *keep*. Best-effort and never raises."""
    if _cache_dir is None:
        return
    prefix = f"{_path_tag(fn)}-"
    try:
        names = os.listdir(_cache_dir)
    except OSError:
        return
    for name in names:
        if name == keep or not name.startswith(prefix):
            continue
        try:
            os.unlink(os.path.join(_cache_dir, name))
        except OSError:
            # Another worker in this pool may be reading it, or removing it concurrently.
            # Either way the entry is superseded and the next miss will try again.
            pass


# ---------------------------------------------------------------------------
# source -> code
# ---------------------------------------------------------------------------


def _read_source(fn: str) -> bytes:
    with open(fn, "rb") as handle:
        return handle.read()


def _compile_rewritten(fn: str) -> types.CodeType:
    """Parse *fn*, rewrite its asserts, and compile — or compile it untouched.

    A file the rewriter refuses (see :func:`rewrite_source`) still has to produce a working
    module, so the fallback compiles the *original* tree. A refusal costs message quality
    and never correctness, which is the invariant the whole feature rests on.
    """
    source = _read_source(fn)
    tree = ast.parse(source, filename=fn)
    if _should_rewrite(tree, source):
        AssertionRewriter(fn, source).run(tree)
    return compile(tree, fn, "exec", dont_inherit=True)


def rewrite_source(source: bytes, filename: str = "<test>") -> ast.Module:
    """Parse and rewrite *source*, returning the transformed module. For tests."""
    tree = ast.parse(source, filename=filename)
    if _should_rewrite(tree, source):
        AssertionRewriter(filename, source).run(tree)
    return tree


def _should_rewrite(tree: ast.Module, source: bytes) -> bool:
    doc = ast.get_docstring(tree)
    if doc is not None and "PYTEST_DONT_REWRITE" in doc:
        return False
    return not _uses_walrus_in_assert(tree)


def _uses_walrus_in_assert(tree: ast.Module) -> bool:
    """Does any ``assert`` in *tree* contain a ``:=``?

    pytest tracks walrus reassignment through ``variables_overwrite`` so the *later*
    mention of a rebound name reprs as its new value. That bookkeeping is not ported (see
    the module docstring), and without it the rewritten code would still be **correct** but
    could print a stale repr for the rebound name. Refusing the whole file is the honest
    trade: no rewriting, no wrong message.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            for inner in ast.walk(node):
                if isinstance(inner, ast.NamedExpr):
                    return True
    return False


# ---------------------------------------------------------------------------
# the rewriter — adapted from `_pytest/assertion/rewrite.py::AssertionRewriter`
# ---------------------------------------------------------------------------


def _traverse_node(node: ast.AST) -> Iterable[ast.AST]:
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _traverse_node(child)


class AssertionRewriter(ast.NodeVisitor):
    r"""Rewrite every ``assert`` in a module into "evaluate, keep, explain on failure".

    The visitor pattern is pytest's: each ``visit_*`` returns ``(expression, explanation)``
    where *expression* is an AST node that evaluates the original subexpression **exactly
    once** (usually a ``Name`` bound to a temporary) and *explanation* is a ``%``-format
    template referring to placeholders registered in the current format context.

    Single evaluation is not a nicety. ``assert next(it) == 3`` must consume one item, and
    a naive "evaluate again to build the message" rewrite would consume two and report a
    value the assertion never saw.

    State, all reset per ``assert``:

    * ``statements`` — the statements that replace the ``assert``;
    * ``variables`` — the temporaries created, cleared to ``None`` afterwards so the
      assertion does not extend any object's lifetime;
    * ``expl_stmts`` — the statements that run **only on failure**, which is what keeps the
      passing path close to the cost of the bare comparison;
    * ``stack`` / ``explanation_specifiers`` — the nested format contexts.
    """

    def __init__(self, module_path: str | None, source: bytes) -> None:
        super().__init__()
        self.module_path: str | None = module_path
        self.source: bytes = source
        self.statements: list[ast.stmt] = []
        self.variables: list[str] = []
        self.variable_counter: itertools.count[int] = itertools.count()
        self.stack: list[dict[str, ast.expr]] = []
        self.expl_stmts: list[ast.stmt] = []
        self.explanation_specifiers: dict[str, ast.expr] = {}

    # -- entry point ------------------------------------------------------

    def run(self, mod: ast.Module) -> None:
        """Insert the helper imports, then rewrite every ``assert`` in *mod*."""
        if not mod.body:
            return

        # The imports go after the docstring and any `__future__` import, because
        # `from __future__ import ...` must be the first statement and a docstring must
        # stay the docstring.
        pos = 0
        item: ast.stmt | None = None
        expect_docstring = True
        for item in mod.body:
            if (
                expect_docstring
                and isinstance(item, ast.Expr)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)
            ):
                expect_docstring = False
            elif (
                isinstance(item, ast.ImportFrom) and item.level == 0 and item.module == "__future__"
            ):
                pass
            else:
                break
            pos += 1

        # For a decorated function the reported line is the first decorator's, not the
        # `def`'s (pytest issue #4984) — the inserted imports must not claim a line inside
        # the decorator list.
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.decorator_list:
            lineno = item.decorator_list[0].lineno
        elif item is not None:
            lineno = item.lineno
        else:  # pragma: no cover - `mod.body` is non-empty, so `item` is bound
            lineno = 1

        aliases = [
            ast.alias("builtins", _BUILTINS_ALIAS, lineno=lineno, col_offset=0),
            ast.alias("rustest._assertion", _HELPER_MODULE_ALIAS, lineno=lineno, col_offset=0),
        ]
        mod.body[pos:pos] = [ast.Import([alias], lineno=lineno, col_offset=0) for alias in aliases]

        # Walk every statement list, replacing `Assert` nodes in place. Expressions are not
        # recursed into: an `assert` cannot appear inside one.
        nodes: list[ast.AST] = [mod]
        while nodes:
            node = nodes.pop()
            for name, field in ast.iter_fields(node):
                if isinstance(field, list):
                    new: list[ast.AST] = []
                    for child in field:  # pyright: ignore[reportUnknownVariableType]
                        if isinstance(child, ast.Assert):
                            new.extend(self.visit_Assert(child))
                        else:
                            new.append(child)  # pyright: ignore[reportUnknownArgumentType]
                            if isinstance(child, ast.AST):
                                nodes.append(child)
                    setattr(node, name, new)
                elif isinstance(field, ast.AST) and not isinstance(field, ast.expr):
                    nodes.append(field)

    # -- machinery --------------------------------------------------------

    def variable(self) -> str:
        """A fresh temporary name, using a character no Python identifier may contain."""
        name = "@py_assert" + str(next(self.variable_counter))
        self.variables.append(name)
        return name

    def assign(self, expr: ast.expr) -> ast.Name:
        """Bind *expr* to a fresh temporary and return a load of it."""
        name = self.variable()
        self.statements.append(ast.Assign([ast.Name(name, ast.Store())], expr))
        return ast.copy_location(ast.Name(name, ast.Load()), expr)

    def display(self, expr: ast.expr) -> ast.expr:
        return self.helper("_saferepr", expr)

    def helper(self, name: str, *args: ast.expr) -> ast.expr:
        attr = ast.Attribute(ast.Name(_HELPER_MODULE_ALIAS, ast.Load()), name, ast.Load())
        return ast.Call(attr, list(args), [])

    def builtin(self, name: str) -> ast.Attribute:
        return ast.Attribute(ast.Name(_BUILTINS_ALIAS, ast.Load()), name, ast.Load())

    def explanation_param(self, expr: ast.expr) -> str:
        """Register *expr* in the current format context and return its ``%(pyN)s`` slot."""
        specifier = "py" + str(next(self.variable_counter))
        self.explanation_specifiers[specifier] = expr
        return "%(" + specifier + ")s"

    def push_format_context(self) -> None:
        self.explanation_specifiers = {}
        self.stack.append(self.explanation_specifiers)

    def pop_format_context(self, expl_expr: ast.expr) -> ast.Name:
        """Emit ``@py_formatN = <expl_expr> % {...}`` and return a load of it."""
        current = self.stack.pop()
        if self.stack:
            self.explanation_specifiers = self.stack[-1]
        keys: list[ast.expr | None] = [ast.Constant(key) for key in current]
        format_dict = ast.Dict(keys, list(current.values()))
        form = ast.BinOp(expl_expr, ast.Mod(), format_dict)
        name = "@py_format" + str(next(self.variable_counter))
        self.expl_stmts.append(ast.Assign([ast.Name(name, ast.Store())], form))
        return ast.Name(name, ast.Load())

    def visit_expr(self, node: ast.expr) -> tuple[ast.expr, str]:
        """Dispatch to a ``visit_*`` and type the result.

        ``ast.NodeVisitor.visit`` is declared to return ``Any``, which would make every
        ``expression, explanation = self.visit(...)`` in this class untyped — and the whole
        visitor is built on that pair being exactly what comes back. Routing every internal
        dispatch through here states the contract once, and a visitor that forgot to return
        an explanation becomes a type error rather than a ``TypeError`` at failure time.
        """
        return self.visit(node)

    def generic_visit(self, node: ast.AST) -> tuple[ast.Name, str]:
        """Anything without a custom visitor: bind it, and show its repr."""
        assert isinstance(node, ast.expr)
        res = self.assign(node)
        return res, self.explanation_param(self.display(res))

    # -- the statement ----------------------------------------------------

    def visit_Assert(self, assert_: ast.Assert) -> list[ast.stmt]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Replace one ``assert`` with the statements that implement it."""
        self.statements = []
        self.variables = []
        self.variable_counter = itertools.count()
        self.stack = []
        self.expl_stmts = []
        self.push_format_context()

        top_condition, explanation = self.visit_expr(assert_.test)
        negation = ast.UnaryOp(ast.Not(), top_condition)

        body = self.expl_stmts
        self.statements.append(ast.If(negation, body, []))
        if assert_.msg:
            assertmsg = self.helper("_format_assertmsg", assert_.msg)
            # The `\n>` prefix is the marker `_format_lines` renders as an un-indented
            # continuation, which is why a custom message and the generated explanation end
            # up on two lines rather than one.
            explanation = "\n>assert " + explanation
        else:
            assertmsg = ast.Constant("")
            explanation = "assert " + explanation
        template = ast.BinOp(assertmsg, ast.Add(), ast.Constant(explanation))
        msg = self.pop_format_context(template)
        fmt = self.helper("_format_explanation", msg)
        exc = ast.Call(ast.Name("AssertionError", ast.Load()), [fmt], [])
        body.append(ast.Raise(exc, None))

        if self.variables:
            clear = ast.Assign(
                [ast.Name(name, ast.Store()) for name in self.variables], ast.Constant(None)
            )
            self.statements.append(clear)

        for stmt in self.statements:
            for node in _traverse_node(stmt):
                if getattr(node, "lineno", None) is None:
                    _ = ast.copy_location(node, assert_)
        return self.statements

    # -- expressions ------------------------------------------------------

    def visit_Name(self, name: ast.Name) -> tuple[ast.Name, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Show a name by value if it is local (or a value-ish global), else by name.

        ``assert x`` on a local prints ``assert []``; ``assert some_module`` prints
        ``assert some_module``, because the repr of a module is noise. The test is made at
        *runtime* — ``locals()`` — because whether a name is local is not always decidable
        from the AST of the expression alone.
        """
        locs = ast.Call(self.builtin("locals"), [], [])
        inlocs = ast.Compare(ast.Constant(name.id), [ast.In()], [locs])
        dorepr = self.helper("_should_repr_global_name", name)
        test = ast.BoolOp(ast.Or(), [inlocs, dorepr])
        expr = ast.IfExp(test, self.display(name), ast.Constant(name.id))
        return name, self.explanation_param(expr)

    def visit_BoolOp(self, boolop: ast.BoolOp) -> tuple[ast.Name, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """``and``/``or``, preserving short-circuit evaluation.

        Each operand after the first is evaluated inside an ``if`` guarded by the running
        result, so ``assert a and b()`` does not call ``b`` when ``a`` is falsy — and the
        explanation lists only the operands that were actually evaluated, which is why
        pytest prints ``assert (False and False)`` rather than inventing a value for an
        operand it never ran.
        """
        res_var = self.variable()
        expl_list = self.assign(ast.List([], ast.Load()))
        app = ast.Attribute(expl_list, "append", ast.Load())
        is_or = int(isinstance(boolop.op, ast.Or))
        body = save = self.statements
        fail_save = self.expl_stmts
        levels = len(boolop.values) - 1
        self.push_format_context()
        cond: ast.expr | None = None
        for i, v in enumerate(boolop.values):
            if i:
                fail_inner: list[ast.stmt] = []
                assert cond is not None, "set by the previous iteration"
                self.expl_stmts.append(ast.If(cond, fail_inner, []))
                self.expl_stmts = fail_inner
            self.push_format_context()
            res, expl = self.visit_expr(v)
            body.append(ast.Assign([ast.Name(res_var, ast.Store())], res))
            expl_format = self.pop_format_context(ast.Constant(expl))
            call = ast.Call(app, [expl_format], [])
            self.expl_stmts.append(ast.Expr(call))
            if i < levels:
                cond = res
                if is_or:
                    cond = ast.UnaryOp(ast.Not(), cond)
                inner: list[ast.stmt] = []
                self.statements.append(ast.If(cond, inner, []))
                self.statements = body = inner
        self.statements = save
        self.expl_stmts = fail_save
        expl_template = self.helper("_format_boolop", expl_list, ast.Constant(is_or))
        expl = self.pop_format_context(expl_template)
        return ast.Name(res_var, ast.Load()), self.explanation_param(expl)

    def visit_UnaryOp(self, unary: ast.UnaryOp) -> tuple[ast.Name, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        pattern = _UNARY_MAP[unary.op.__class__]
        operand_res, operand_expl = self.visit_expr(unary.operand)
        res = self.assign(ast.copy_location(ast.UnaryOp(unary.op, operand_res), unary))
        return res, pattern % (operand_expl,)

    def visit_BinOp(self, binop: ast.BinOp) -> tuple[ast.Name, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        symbol = _BINOP_MAP[binop.op.__class__]
        left_expr, left_expl = self.visit_expr(binop.left)
        right_expr, right_expl = self.visit_expr(binop.right)
        explanation = f"({left_expl} {symbol} {right_expl})"
        res = self.assign(ast.copy_location(ast.BinOp(left_expr, binop.op, right_expr), binop))
        return res, explanation

    def visit_Call(self, call: ast.Call) -> tuple[ast.Name, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """A call gets a ``where`` clause: ``assert 0`` / ``+  where 0 = f(0)``.

        The ``\\n{`` and ``\\n}`` in the returned template are
        :func:`rustest._assertion.format_explanation`'s markers for a nested explanation —
        they are what become the ``where``/``and`` lines.
        """
        new_func, func_expl = self.visit_expr(call.func)
        arg_expls: list[str] = []
        new_args: list[ast.expr] = []
        new_kwargs: list[ast.keyword] = []
        for arg in call.args:
            res, expl = self.visit_expr(arg)
            arg_expls.append(expl)
            new_args.append(res)
        for keyword in call.keywords:
            res, expl = self.visit_expr(keyword.value)
            new_kwargs.append(ast.keyword(keyword.arg, res))
            if keyword.arg:
                arg_expls.append(keyword.arg + "=" + expl)
            else:  # `**kwargs` has an `arg` of None
                arg_expls.append("**" + expl)

        expl = "{}({})".format(func_expl, ", ".join(arg_expls))
        new_call = ast.copy_location(ast.Call(new_func, new_args, new_kwargs), call)
        res = self.assign(new_call)
        res_expl = self.explanation_param(self.display(res))
        outer_expl = f"{res_expl}\n{{{res_expl} = {expl}\n}}"
        return res, outer_expl

    def visit_Starred(self, starred: ast.Starred) -> tuple[ast.Starred, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        res, expl = self.visit_expr(starred.value)
        return ast.Starred(res, starred.ctx), "*" + expl

    def visit_Attribute(self, attr: ast.Attribute) -> tuple[ast.expr, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        if not isinstance(attr.ctx, ast.Load):
            return self.generic_visit(attr)
        value, value_expl = self.visit_expr(attr.value)
        res = self.assign(ast.copy_location(ast.Attribute(value, attr.attr, ast.Load()), attr))
        res_expl = self.explanation_param(self.display(res))
        return res, "%s\n{%s = %s.%s\n}" % (res_expl, res_expl, value_expl, attr.attr)

    def visit_Compare(self, comp: ast.Compare) -> tuple[ast.expr, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Comparisons, including chains — the shape this whole feature exists for.

        ``a < b < c`` becomes two separate two-operand comparisons over shared temporaries,
        so ``b`` is evaluated once, and the runtime helper
        :func:`rustest._assertion._call_reprcompare` reports the **first failing link**:
        ``assert 1 < x < 10`` with ``x = 20`` says ``assert 20 < 10``.

        The parenthesisation of a nested ``Compare``/``BoolOp`` operand is pytest's and it
        is not cosmetic: ``assert (a == b) == c`` would otherwise render as
        ``assert a == b == c``, which is a different expression.
        """
        self.push_format_context()
        left_res, left_expl = self.visit_expr(comp.left)
        if isinstance(comp.left, (ast.Compare, ast.BoolOp)):
            left_expl = f"({left_expl})"
        res_variables = [self.variable() for _ in range(len(comp.ops))]
        load_names: list[ast.expr] = [ast.Name(v, ast.Load()) for v in res_variables]
        store_names = [ast.Name(v, ast.Store()) for v in res_variables]
        expls: list[ast.expr] = []
        syms: list[ast.expr] = []
        results: list[ast.expr] = [left_res]
        for i, op, next_operand in zip(range(len(comp.ops)), comp.ops, comp.comparators):
            next_res, next_expl = self.visit_expr(next_operand)
            if isinstance(next_operand, (ast.Compare, ast.BoolOp)):
                next_expl = f"({next_expl})"
            results.append(next_res)
            sym = _BINOP_MAP[op.__class__]
            syms.append(ast.Constant(sym))
            expls.append(ast.Constant(f"{left_expl} {sym} {next_expl}"))
            res_expr = ast.copy_location(ast.Compare(left_res, [op], [next_res]), comp)
            self.statements.append(ast.Assign([store_names[i]], res_expr))
            left_res, left_expl = next_res, next_expl
        expl_call = self.helper(
            "_call_reprcompare",
            ast.Tuple(syms, ast.Load()),
            ast.Tuple(load_names, ast.Load()),
            ast.Tuple(expls, ast.Load()),
            ast.Tuple(results, ast.Load()),
        )
        res: ast.expr = ast.BoolOp(ast.And(), load_names) if len(comp.ops) > 1 else load_names[0]
        return res, self.explanation_param(self.pop_format_context(expl_call))
