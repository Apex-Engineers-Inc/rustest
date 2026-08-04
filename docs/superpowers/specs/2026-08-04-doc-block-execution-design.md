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
  -> exec at module level into a fresh module object
  -> conftest_registry(md_path, rootdir)     # builtins + the markdown file's conftest chain
  -> parse_fixturedefs(module, baseid)       # the block's own @fixture definitions
  -> collect_module(module, md_path, rootdir, naming, registry, asyncio_config)
  -> zero or more CollectedTest, each with parts prefixed by codeblock_N_line_M
```

Note `build_registry` is **not** the reuse point: it takes a `Path` and imports the file,
whereas a block's source is already in hand. The two halves it composes,
`conftest_registry` and `parse_fixturedefs`, are what a block needs, and `collect_module`
is the enumerator. `collect_markdown` already loads the conftest chain for its autouse
handling, so the chain is available at the call site.

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

Module-level execution must still succeed. A block that raises while executing is a
collection error for that block, which is what catches missing imports today.

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
`::`, so a third segment parses correctly with no change to `src/v2/nodeid.rs`. The
existing docstring in `collect_markdown` objects that a second `::` "would claim the block
is a class member"; that objection is semantic, not structural, and the class chain and the
block chain are already distinguishable by the `codeblock_` prefix. Update that docstring
rather than working around it.

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

## Testing

Rust (`src/v2/collect.rs`):

- the default is off, and a markdown argument is a usage error without the flag
- `--codeblocks` enables; `--no-codeblocks` disables
- config precedence: CLI over `[tool.rustest]` over the pytest section
- the pytest-section spelling is honoured in all five config file names
- a directory walk still finds no markdown regardless of the setting

Python (`python/tests/`):

- a block defining test functions yields one node per function
- a block defining none yields a single `codeblock_N_line_M` node
- an inner test resolves a fixture from a conftest
- `@parametrize` inside a block expands
- a `Test*` class inside a block collects
- a block that raises at module level reports a collection error
- skip markers suppress execution

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
