# Documentation Code Block Execution

Status: approved by Jeffrey 2026-08-04 (via decision questions). Supersedes the current
markdown collection behaviour described in `src/v2/collect.rs` and
`python/rustest/_v2_worker.py::collect_markdown`.

## Problem

Documentation code blocks are collected and executed, but a `def test_*` inside a block is
defined and never called, so its assertions never run. The mechanism is
`_codeblock_callable`: the block body is indented into `def run_codeblock():` and that
function becomes the test. Any function the block defines is a local of `run_codeblock`,
created and discarded when it runs.

Measured on this repository, 2026-08-04, before the documentation review:

- 386 python fences across `README.md` and `user_guide/*.md`
- 252 of them (65%) keep their assertions inside a `def test_*`
- 109 test functions fail when actually invoked, while CI reports every page green

Concrete examples that passed CI while being broken: three `cache` examples raising
`TypeError` on contact, a `mocker.resetall()` example documented backwards, a
`monkeypatch.chdir` example asserting a restore that cannot hold inside the same test, and
a `--cov` invocation that silently measures a different tree than it runs.

A second consequence: `collect_markdown` states that "a code block requests no fixtures",
so the closure is the autouse set only. Fixture-taking examples cannot execute at all,
which is most of what a testing framework's documentation contains.

`CLAUDE.md` asserts that "All Python code blocks in documentation are tested as executable
code in CI". That claim is what the documentation was written against, so correcting the
pages without correcting the mechanism restarts the drift immediately.

## Locked decisions (user-approved)

1. **Codeblock collection is off by default.** It is currently on, with `--no-codeblocks`
   to disable. The default inverts.
2. **One node per inner test.** A block defining test functions reports each as its own
   test with its own node id, so `-k` and `-m` can target an individual example.
3. **One switch, real execution always.** Enabling codeblocks means inner test functions
   really execute. There is no weaker "collect but do not run" level, because a mode whose
   value is a known-misleading check is not worth documenting.
4. **The setting is readable from both config homes.** A dedicated `[tool.rustest]`
   section and pytest's own ini section both work, so the key can live in whichever place
   a project already keeps configuration.

## Decisions made by Claude (veto anytime)

- **`--no-codeblocks` is retained**, as the off half of a normal flag pair rather than as
  "restore pytest's answer". Once config can enable the feature, a per-run override to
  disable it is genuinely useful: `rustest --no-codeblocks tests/` during a tight loop.
- **Blocks with no test functions keep today's shape**: a single node,
  `codeblock_N_line_M`, passing if the body executes cleanly. Most blocks are imports,
  config snippets and one-liners, and they should not grow a node id segment.
- **A block is collected as a synthetic test module** rather than by special-casing
  `test_*`. See below.

## The switch

Precedence, highest first:

| Source | Spelling | Files |
| --- | --- | --- |
| CLI | `--codeblocks` / `--no-codeblocks` | n/a |
| rustest config | `[tool.rustest]` `codeblocks = true` | `pyproject.toml` |
| pytest config | `codeblocks = true` in pytest's ini section | `pyproject.toml`, `pytest.ini`, `.pytest.ini`, `tox.ini`, `setup.cfg` |

Within a single file, `[tool.rustest]` beats the pytest section. Across files, the existing
`CONFIG_NAMES` precedence in `src/v2/config.rs` is unchanged.

### `[tool.rustest]` is read out-of-band

**This is the correction to an earlier draft of this spec, which would have made the
dedicated section silently useless to most of the projects it exists for.**

`load_config_dict_from_file` returns `None` for a `pyproject.toml` with no
`[tool.pytest.ini_options]` table (`config.rs:783-797`), and `locate_config` then keeps
walking `CONFIG_NAMES`, where `pytest.ini` outranks `pyproject.toml` **in the same
directory** (`config.rs:19-25`). A project that keeps its settings in `pytest.ini` and adds
`[tool.rustest]` to `pyproject.toml` would therefore never have that table read, because
`pyproject.toml` never becomes the config file.

The resolution: `[tool.rustest]` is a **separate lookup against the `pyproject.toml`
nearest the rootdir**. It does not participate in config-file discovery and it does not
affect rootdir resolution.

The rejected alternative was letting `[tool.rustest]` make `pyproject.toml` authoritative
for discovery. That would change **rootdir** for the whole run, so adding the key could
move every node id in the suite, and `config.rs` exists to reproduce pytest's rootdir rules
byte for byte.

The cost, stated plainly: `codeblocks` is the one setting that does not follow pytest's
config-file precedence, and there are two lookup paths to document.

`config.rs` has no boolean getter today (`getini_string` / args / paths only,
`config.rs:849-907`), so one is needed, along with a decision on accepted spellings. Use
TOML's native `true` / `false` for `[tool.rustest]`, and pytest's ini boolean parsing for
the ini-section spelling.

The pytest-section spelling is what makes the setting available in `pytest.ini`, `tox.ini`
and `setup.cfg`, which a `[tool.rustest]` table cannot reach. Its cost is that a
rustest-only key sits in pytest's namespace, so real pytest run with `--strict-config`
errors on it. Document that tradeoff and recommend `[tool.rustest]` for pyproject-based
projects.

## Execution model

Replace `_codeblock_callable`'s wrap-in-a-function approach with module-level execution,
then hand the resulting module to `collect_module`, the same enumerator a `.py` test file
goes through.

```
block source
  -> exec at module level into a fresh module object   (at COLLECT time, as .py does)
  -> conftest_registry(md_path, rootdir)               # builtins + the conftest chain
  -> register xunit setup_module/setup_function hooks  # BEFORE the block's own fixtures
  -> parse_factories(module, baseid)                   # the block's own @fixture defs
  -> _register_declared_plugins(module, registry)      # a pytest_plugins in the block
  -> collect_module(module, md_path, rootdir, naming, registry, asyncio_config,
                    block_segment="codeblock_N_line_M")
  -> zero or more CollectedTest, each carrying the block segment in its id and
     qualname, and NOT in its class_name
```

The four registration steps are `build_registry` minus the import, in its order. The
`block_segment` argument is new and is threaded to `_collect_function`; see "The block
segment must not become a class" for why it cannot be a `parts` prefix.

Note `build_registry` is **not** the reuse point: it takes a `Path` and imports the file,
whereas a block's source is already in hand. `collect_module` is the enumerator, and
`collect_markdown` already loads the conftest chain for its autouse handling, so the chain
is available at the call site.

**What the block needs is `build_registry` minus the import, and that is four steps, not
two.** An earlier draft of this spec sketched `conftest_registry` + `parse_fixturedefs` and
was wrong twice over: `parse_fixturedefs` only *returns* defs (`_v2_worker.py:2149`), so
that sketch registers nothing, and `build_registry` does two further things between its
calls in an order its own comment calls load-bearing (`_v2_worker.py:3899-3914`):

1. `conftest_registry(md_path, rootdir)` for builtins plus the conftest chain
2. **xunit `setup_module` / `setup_function` hooks registered as autouse fixtures**, before
   the module's own fixtures, because the registration order is the shadowing rule
3. `parse_factories` for the block's own `@fixture` definitions
4. `_register_declared_plugins` for a `pytest_plugins` declaration inside the block

Dropping 2 means a documented xunit-style `setup_function` example silently never runs,
which is the "behaves exactly as in a test file" claim quietly failing. Either perform all
four, or state the exclusions explicitly. This spec performs all four.

### When a block executes

At **collection time**, which is when a `.py` test module's body runs too: `build_registry`
imports the file during `collect_file`, so module-level code in a test file has always
executed during collection. Doc blocks executing at collect is therefore consistent with
existing semantics rather than a new hazard, and the same consequences already apply to
`.py` files:

- `--v2-collect-only` runs module-level code
- a module-level `sys.exit()` ends the worker, because `collect_file` deliberately lets
  `SystemExit` through (`_v2_worker.py:6357-6363`) while `ABORT_EXCEPTIONS` excludes it
  from the execute path

Skip-marked blocks are not executed at all, so a block that must not run has an existing
mechanism. For the record, all six `run(paths=...)` blocks in `user_guide/python-api.md`
are skip-marked today, so the "collect-only spawns a nested run" scenario does not arise on
this repository.

**The no-test block's node reports the real outcome of that collect-time execution.** It
does not re-execute the body at run time, which would double side effects, and it does not
report a vacuous pass, which locked decision 3 forbids as a known-misleading check.

`collect_module` iterates the module `__dict__` in definition order, and its own docstring
records that this "is exactly why a function nested inside another function is invisible".
That sentence describes today's bug precisely: wrapping the block in `run_codeblock` makes
every test the block defines a nested function. Executing at module level is what makes
them visible to the enumerator that was always able to find them.

This is deliberately not a special case for `test_*` functions. Reusing the existing
collection path means the following work inside documentation blocks with no additional
code:

- `@parametrize`, `@fixture` and `@mark.*` behave exactly as in a test file
- `Test*` classes collect, honouring `python_classes`
- inner tests resolve fixtures through the conftest chain, which fixes the
  "requests no fixtures" limitation directly

Each block still gets its own module and therefore its own namespace, preserving the
current rule that a block cannot see another block's names and must be self-contained.

### A failing block is a failing test, not a collection error

An earlier draft said a block that raises is "a collection error for that block". **The
protocol cannot express that.** `collect_file` carries exactly one of `tests` / `error` per
**file** (`_v2_worker.py:6344-6346`), `CollectionErrorEntry` is per-path
(`manifest.rs:88-93`), and any exception during module exec errors the whole file
(`_v2_worker.py:6434`). One broken block would erase every sibling block's tests on that
page, and on first enablement here the 109 known failures would surface as a handful of
file-level collection errors instead of 109 located test failures. That is strictly worse
than the status quo it replaces.

So: **a block that raises while executing yields a failing test**, carrying the traceback.
The block is caught at its own boundary and its failure is recorded as the outcome of the
node or nodes it owns, leaving sibling blocks collected and reported normally. No schema
change, siblings survive, and a broken example reports as what it is.

A block that raises before any test function is defined yields a single failing node for
the block. A block that raises partway through, after defining some tests, reports the
failure and does not invent results for tests whose definitions were never reached.

## Node ids

A block defining test functions produces one node per test:

```
user_guide/fixtures.md::codeblock_3_line_88::test_uses_tmp_path
user_guide/fixtures.md::codeblock_3_line_88::test_cleanup
```

A block defining none keeps the current single node:

```
user_guide/quickstart.md::codeblock_7_line_204
```

`build_nodeid` takes an arbitrary `parts` chain and `split_nodeid` cuts at the **first**
`::` then splits the rest (`nodeid.rs:117-133`), so a third segment parses correctly with
no change to `src/v2/nodeid.rs`. `-k` derives its nodes from `qualname` split on `.`
(`selection.rs:596-601`), so the block segment is `-k`-addressable for free.

### The block segment must not become a class

**This is the hazard that nearly went into the implementation, and it is the reason the
node id needs a dedicated field rather than a `parts` prefix.**

`_build_entry` sets `class_name = ".".join(parts[:-1])` whenever `len(parts) > 1`
(`_v2_worker.py:3990-3991`), and `ExecutionPlan.class_name` follows the same rule
(`_v2_worker.py:2341`). Simply prefixing `parts` with `codeblock_3_line_88` therefore gives
every module-level test in a block a **phantom class of that name**.

`class_name` is not cosmetic. `FixtureRunner.note_test_boundary` (`_v2_worker.py:2789`)
uses it as the class-scope teardown boundary, and its docstring states the rule it
implements: "**a module-level test always ends class scope**", which is not the same rule
as "the class changed". Two module-level tests in one block would look like two tests in
the same class, so a class-scoped fixture they request would be built once and reused
instead of torn down per test. That is a silently wrong value handed to a test, the exact
failure mode the same docstring records as having bitten fourteen tests of rustest's own
suite.

The requirement: the block segment enters **node-id construction and the `-k` qualname
only**, through a dedicated field, and is explicitly excluded from `class_name` and from
`note_test_boundary`'s input. A module-level test inside a block must keep
`class_name is None`.

There is also no existing mechanism to prefix parts: `_make_items` starts from `parts=()`
(`_v2_worker.py:4565`) and both the wire entry and the plan are built inside
`_collect_function` (`_v2_worker.py:4132-4163`). The field must be threaded through
`collect_module` to `_make_items` to `_collect_function`, which is the natural place to
keep it out of `class_name`.

The existing `collect_markdown` docstring objects that a second `::` "would claim the block
is a class member". Read as a display concern that objection is minor; read as a statement
about `class_name` it was right, and this section is what it was pointing at. Update the
docstring to say which.

The `codeblock` mark continues to be attached to every collected node, so `-m codeblock`
and `-m "not codeblock"` keep working, now at inner-test granularity.

Skip markers (`<!--rustest.mark.skip-->` and the pytest-compatible spellings) keep their
current meaning: the block is not executed.

## Breaking changes

All three need a `CHANGELOG.md` entry.

1. **`rustest README.md` collects nothing unless enabled.** Every project relying on the
   old default must set the config key or pass the flag.
2. **This repository must set `codeblocks = true` in `pyproject.toml`.** Without it the
   CI line `rustest README.md user_guide/*.md` passes vacuously, which is a worse failure
   than the one this work exists to fix.
3. **Node ids gain a segment** for blocks with inner tests, so `-k` selections against
   documentation tests may need updating.
4. **Projects that enable it will see failures that were always present.** This repository
   went from zero reported failures to 109 under real execution. That is the feature
   working, not a regression, and the changelog should say so plainly.
5. **The Python API default flips too.** `run(codeblocks: bool = True)`
   (`core.py:322,736`) becomes `codeblocks: bool | None = None`, meaning "config decides",
   matching the CLI. `user_guide/markdown-testing.md:553-561` documents
   `run(paths=["README.md"])` and `run(..., codeblocks=False)`; after the flip the first
   collects nothing and the second is a no-op, on the very page this spec rewrites.
6. **`--lf` entries from before the change go stale once.** Old
   `...md::codeblock_N_line_M` ids never match the new inner-test ids, so a user with
   cached doc failures gets one empty `--lf` selection. Harmless, but it belongs in the
   changelog. `cache.rs:95-106` matches exact id strings, so there is no crash.

### Mechanical notes for the implementation

- **The CLI pair needs a tri-state default.** `enable_codeblocks` defaults to `True` at
  `cli.py:478` and is CLI-only today. With a config layer beneath it, `store_false` with a
  concrete default cannot express "not passed"; the default must be `None` so config can
  decide.
- **Per-block module names.** The current synthetic module is per-file and never registered
  in `sys.modules` (`_v2_worker.py:4703-4706`). Blocks need distinct names within a file.
  Keep them unregistered, consistent with today, and accept the known consequence that
  pickling a class defined inside a block will not work; a doc example that needs pickling
  is out of scope.
- **Traceback filenames.** Keep the `{path}:L{line}` compile-filename convention
  (`_v2_worker.py:4665`) for module-level exec so a traceback points at the markdown
  source, and set `module.__file__` accordingly.
- **Assertion rewriting does not apply.** `.md` never enters Tier S
  (`static_collect.rs:614-621`) and `collect_file` registers for rewriting only on a Tier S
  `assert_key` (`_v2_worker.py:6369-6372`), so failures in blocks are bare `AssertionError`
  without introspection. That matches Tier D `.py` files, but for a feature whose
  deliverable is failure messages in documentation CI it should be stated, and revisited if
  the messages prove too thin.
- **`-n` distributes by file** (`execute.rs:542-546`), so a 60-block page serializes on one
  worker while pages parallelize. Acceptable; worth noting in the performance docs.

## Testing

The happy-path shape tests below are necessary but would pass even with the hazards in this
spec implemented wrongly. The **pinning tests** are the ones that matter; each names the
hazard it exists to catch.

Rust (`src/v2/collect.rs`, `src/v2/config.rs`):

- the default is off, and a markdown argument is a usage error without the flag
- `--codeblocks` enables; `--no-codeblocks` disables
- config precedence: CLI over `[tool.rustest]` over the pytest section
- the pytest-section spelling is honoured in all five config file names
- a directory walk still finds no markdown regardless of the setting
- **pinning:** `pytest.ini` present as the config file **and** `[tool.rustest]` in
  `pyproject.toml`, with the table honoured. This is the case that was silently broken
- **pinning:** rootdir is identical with and without `[tool.rustest]` present
- boolean coercion for both spellings, since `config.rs` has no boolean getter today

Python (`python/tests/`):

- a block defining test functions yields one node per function
- a block defining none yields a single `codeblock_N_line_M` node
- an inner test resolves a fixture from a conftest
- `@parametrize` inside a block expands
- a `Test*` class inside a block collects
- skip markers suppress execution
- **pinning:** a **class-scoped** conftest fixture requested by two module-level tests in
  the same block is torn down per test. This catches the phantom-class hazard; the generic
  "resolves a fixture from a conftest" case does not
- **pinning:** one broken block does **not** erase sibling blocks' tests in the same file
- **pinning:** a mixed block, both passing module-level code with a failing inner test, and
  failing module-level code with inner tests defined after the failure
- **pinning:** two blocks in one file defining a same-named test produce distinct ids
- a block calling `sys.exit()`: outcome classification, and the worker's fate, asserted
  rather than discovered
- `--v2-collect-only` on a `.md` whose block has a visible side effect
- `-k codeblock_3_line_88` selects that block's tests; `-k` by inner test name works
- `run()` default behaviour after the flip

Integration (`tests/`): a fixture `.md` exercising each shape above, run under both
runners where meaningful.

Integration (`tests/`): a fixture `.md` exercising each shape above, run under both
runners where meaningful.

Dogfooding: this repository enables the setting, so `rustest README.md user_guide/*.md`
becomes a real gate.

## Documentation

- `user_guide/markdown-testing.md`: rewrite. It is the page that documents this mechanism,
  and it is currently the page most wrong about it.
- `user_guide/cli.md`: the `--codeblocks` / `--no-codeblocks` pair, the default flip, the
  config key.
- `CLAUDE.md` L329, L338, L412: the "tested as executable code" claim becomes true once
  this ships, so these lines change from a walk-back to a description of real behaviour.
  Note that L331 to L333, the fresh-namespace rule, is already correct and stays.

## Out of scope

- Changing how non-python fences are treated. They remain uncollected.
- A doc-block coverage report.
- Running skip-marked blocks in any mode.
- The separate CI harness previously considered. This design subsumes it: if the collector
  runs blocks for real, a parallel verification script is redundant.
