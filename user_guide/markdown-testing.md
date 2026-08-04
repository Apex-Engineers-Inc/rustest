# Markdown Code Block Testing

rustest can run the Python code blocks in your markdown files as tests, so a documentation
example that stops working fails the build instead of sitting there misleading people. This
is rustest's own extension: pytest collects nothing from a `.md` file.

The feature covers the same ground as pytest-codeblocks, including its skip markers, and
needs no plugin.

**It is off by default.** A block that defines `def test_*` functions runs each as its own
test, with its own node id — not a dead-code example that looks tested and isn't. Read
"Enabling It" first; every other section on this page assumes the setting is on.

## Enabling It

Nothing named `.md` collects until you turn this on. Highest precedence first:

| Source | Spelling | Files |
| --- | --- | --- |
| CLI | `--codeblocks` / `--no-codeblocks` | n/a |
| rustest config | `[tool.rustest]` `codeblocks = true` | `pyproject.toml` |
| pytest config | `codeblocks = true` in the pytest ini section | `pyproject.toml`, `pytest.ini`, `.pytest.ini`, `tox.ini`, `setup.cfg` |

```toml
# pyproject.toml
[tool.rustest]
codeblocks = true
```

or, if you keep settings in pytest's own section instead:

```ini
# pytest.ini / tox.ini / setup.cfg
[pytest]
codeblocks = true
```

Within one `pyproject.toml`, `[tool.rustest]` wins over `[tool.pytest.ini_options]`. The
pytest-section spelling is what lets `pytest.ini`, `tox.ini` and `setup.cfg` carry the
setting too, since those files have no `[tool.rustest]` table to read — its cost is that a
rustest-only key sits in pytest's namespace, so a real pytest run with `--strict-config`
rejects it. Prefer `[tool.rustest]` for a `pyproject.toml`-based project.

`[tool.rustest]` is read from **exactly** `<rootdir>/pyproject.toml`, with no upward or
downward walk. A `[tool.rustest]` table in a subdirectory's `pyproject.toml`, or in one
above the repository, is not honoured.

Without any of the above, naming a `.md` file is a usage error, exit 4 with
`found no collectors for <path>` — pytest's own answer for the same argument:

```bash
rustest README.md    # ERROR: found no collectors for README.md   (no config, no flag)
```

`--codeblocks` turns collection on for one run without touching config:

```bash
rustest README.md --codeblocks
```

`--no-codeblocks` is the off half of the same pair, and it beats config: it is what lets you
suppress the tier for one run even when `[tool.rustest] codeblocks = true` is set, which is
genuinely useful during a tight non-doc iteration loop:

```bash
rustest --no-codeblocks tests/
```

This repository enables the setting for itself in `pyproject.toml`, which is what makes
`rustest README.md user_guide/*.md` — the exact line CI runs — a real gate rather than one
that passes vacuously because nothing was collected.

## Naming the Files

**Markdown files must be named as arguments.** A directory argument collects none, whatever
the setting above says. Run from this repository, the two lines below do very different
things:

```bash
rustest README.md user_guide/fixtures.md   # runs the python fences in both files
rustest user_guide/                        # "no tests ran": a walk never collects .md
```

This matches what pytest does with the same tree. A directory walk that picked up `.md`
would mean `rustest tests/` running tests `pytest tests/` never sees, which is how a repo
with fences in its docs ends up with a green pytest run and a red rustest one.

Shell globs are the usual way to name a whole directory of pages. rustest's own CI line is:

```bash
rustest README.md user_guide/*.md
```

## Markdown File Example

Create a markdown file (e.g., `example.md`):

````markdown
# Example Documentation

## Basic Addition

```python
x = 1 + 1
assert x == 2
```

## String Operations

```python
text = "hello world"
assert text.startswith("hello")
assert "world" in text
```

## Using Imports

```python
from datetime import datetime

now = datetime.now()
assert isinstance(now, datetime)
```
````

Run rustest with the setting enabled:

```bash
rustest example.md --codeblocks
```

Output:

```
3 passed in 0.45s
```

A block with no test function is one node, named by its index and the line the opening
fence sits on, so a failure points at the right place in the page:

```
example.md::codeblock_0_line_3 PASSED                                   [ 33%]
example.md::codeblock_1_line_8 PASSED                                   [ 66%]
example.md::codeblock_2_line_13 PASSED                                  [100%]

3 passed in 0.45s
```

Every block is compiled with the markdown file as its filename, so a traceback names the
page and the line rather than an anonymous `<string>`.

## How a Block Executes

A block's source runs at **collect time**, exactly when a `.py` test module's own top-level
code runs — `collect_file` imports a `.py` file during collection, and a doc block goes
through the same collector (`collect_module`) once its source has been exec'd into a fresh
module. This is not a special case built for `test_*`; it is the ordinary module-collection
path, reused.

Three consequences follow, and they are not new hazards — they are the same consequences a
`.py` file has always had:

- **A `def test_*` inside a block really runs**, as its own test with its own node id. There
  is no wrapper hiding it inside a function that is defined and never called.
- **`--v2-collect-only` runs the body.** Collecting is importing, for a block exactly as for
  a file.
- **Deselecting a block does not stop its body from running.** `-k`, `-m "not codeblock"`
  and `--lf` decide which *tests* execute, not which module-level code does — a block's
  statements outside any `def` already ran by the time selection is applied, identical to
  a `.py` module's imports and assignments. This is an accepted, deliberate consequence of
  reusing `.py` semantics, not a bug to route around.

### Each block gets a fresh namespace

Names defined in one block do not exist in the next, imports included. There is no way to
continue a block; each one is its own module.

### Fixtures, `@parametrize` and classes work inside a block

Because a block is collected the same way a `.py` module is, everything that works in a test
file works inside a block, with no special casing:

```python
from rustest import fixture


@fixture
def sample():
    return "test"


def test_uses_a_fixture(sample):
    assert sample == "test"
```

A block can request a fixture from a `conftest.py` above the markdown file, define its own
`@fixture`, use `@parametrize`, define `Test*` classes, and use xunit-style `setup_function`/
`setup_module` hooks — all of it resolves through the same conftest chain a `.py` file gets.

**One exception, and it is the sharpest edge on this page: autouse fixtures reach a block's
inner tests, but not the block's own top-level statements.** The old mechanism ran the whole
block body inside a fixture closure, so a conftest's `@fixture(autouse=True)` applied to
bare, non-`def` code in the block. That is no longer true — the body now executes at
collect, before any fixture closure exists — and it is consistent with `.py` semantics,
where module-level code has never had autouse fixtures applied either. A doc example that
relied on an autouse fixture at block top level needs to move that dependency into a
`def test_*`.

## Two Node Shapes

**A block defining one or more `def test_*` functions produces one node per test**, each
addressable on its own:

```
user_guide/fixtures.md::codeblock_3_line_88::test_uses_tmp_path PASSED
user_guide/fixtures.md::codeblock_3_line_88::test_cleanup PASSED
```

**A block defining no test function keeps a single node**, passing if the body executes
cleanly:

```
user_guide/quickstart.md::codeblock_7_line_204 PASSED
```

Most documentation blocks are imports, config snippets and one-line assertions, and stay in
this second shape without growing an extra id segment.

```python
def double(x: int) -> int:
    return x * 2


# No test function here: this is the single-node shape, and the assertion below is what
# it passes or fails on.
assert double(21) == 42
```

## A Failing Block Is a Failing Test

A block that raises is a **failing test**, carrying the traceback — not a file-level
collection error. Siblings on the same page are unaffected:

````markdown
```python
import no_such_module
```

```python
def test_sibling():
    assert True
```
````

The first block fails; `test_sibling` still collects and passes. This changes the exit code
too: a page with a broken block now exits **1** (failing tests), not **2** (a collection
error), because the failure is attributed to the block that actually broke rather than to
the whole file.

### A block that raises after defining some tests

This is the one case with real nuance, so it is worth stating in full. Given:

````markdown
```python
def test_reached():
    assert True


raise RuntimeError("boom")


def test_never_defined():
    assert True
```
````

- `test_reached` was already defined when the raise happened, so it is collected and runs on
  its own merits.
- The block itself gets its own `codeblock_N_line_M` node, carrying the exec failure. This is
  the one situation where a test-defining block still gets a block-level node.
- `test_never_defined` never came into being — its `def` was never reached — so no node for
  it exists at all; it does not appear as failing, skipped, or anything else.
- Output the block printed before raising is captured on the block node's result, not
  discarded.
- The block node **replays** the outcome decided at collect; it does not re-run the body a
  second time at execute, which would double any side effect.

## `-k` and the `codeblock` Mark

Every collected node — the single-node shape and every inner test — carries a `codeblock`
mark, so `-m` can select or exclude doc examples at either granularity:

```bash
rustest README.md user_guide/*.md -m codeblock       # only doc examples
rustest README.md tests/ -m "not codeblock"          # everything except them
```

`-k` reaches both the block and the individual test inside it:

```bash
rustest user_guide/fixtures.md -k codeblock_3_line_88   # every test in that one block
rustest user_guide/fixtures.md -k test_uses_tmp_path    # that one test, in whichever block defines it
```

## Skipping Code Blocks

An HTML comment above a fence stops that block from being executed at all — not deselected,
not collected-then-skipped, never even compiled:

```markdown
<!--rustest.mark.skip-->
```python
# This example won't be executed
result = some_external_api()
```
```

Keep the marker on the line directly above the opening fence. One line may sit between them
and the marker still applies, but anything more and it is forgotten, so a marker separated
from its fence by a paragraph does nothing.

The skipped block reports as `SKIPPED (Skipped via HTML comment marker)`.

!!! warning "A skip marker is permanent, and it is silent"
    Skipping does not just exempt a block from this run. It exempts it from every future run,
    so nothing will ever tell you when the code in it stops being correct. A skipped example
    that calls an API you later rename, or passes an argument you later reject, keeps sitting
    on the page looking authoritative while CI stays green.

    Reach for the marker when a block genuinely cannot execute: it needs a service, a package
    you will not depend on, or it is deliberately incomplete. When a block is skipped only
    because it needs a little setup, write the setup instead — a fixture-taking `def test_*`
    is a full test now, not a dead end. A stub of five lines is cheaper than an example that
    quietly goes stale.

    Skipped blocks are worth re-reading by hand whenever the API around them changes, because
    no tool is going to do it for you.

!!! note "pytest compatibility"
    For compatibility with pytest-codeblocks, `<!--pytest.mark.skip-->` and `<!--pytest-codeblocks:skip-->` also work.

## Language Filtering

Only Python code blocks are tested. The language is the text after the opening fence,
lowercased, so ```` ```Python ```` counts and ```` ```pycon ```` does not. Other languages
are ignored:

````markdown
# Documentation

```python
# This will be tested
assert 1 + 1 == 2
```

```javascript
// This is ignored
console.log("Hello");
```

```bash
# This is ignored
echo "Hello"
```
````

## Code Block Sharing State

The fresh-namespace rule above is what this looks like in practice. The second block here
fails, because `x` belongs to the first block's namespace and nothing carries it over:

````markdown
# Example

```python
# Block 1
x = 10
```

```python
# Block 2 - FAILS! x is not defined here
assert x == 10  # NameError: name 'x' is not defined
```
````

If you need shared state, put it in one code block:

````markdown
```python
# All in one block - this works
x = 10
y = 20
assert x + y == 30
```
````

## Handling Expected Failures

If you want to show code that deliberately fails, use text blocks or describe the failure:

````markdown
# Error Handling

This code demonstrates an error:

```text
# This would raise an error (shown as text, not tested)
result = 1 / 0  # ZeroDivisionError
```

The correct way to handle it:

```python
from rustest import raises

with raises(ZeroDivisionError):
    1 / 0
```
````

## Best Practices

### Prefer a real assertion over a defined-but-uncalled function

The old mechanism made this a hard rule: a `def test_*` in a block never ran, so any block
whose checks lived only inside one was untested despite looking tested. Blocks execute for
real now, so this is a style preference rather than a correctness requirement — but it is
still worth keeping documentation examples short and their assertions visible at a glance:

```python
# Good - the assertion is right there, and it genuinely runs either way
assert "hello".upper() == "HELLO"
```

If the example's whole point is to show the shape of a test function, write it as one; it
will be checked as one:

```python
from rustest import fixture


@fixture
def sample():
    return "test"


def test_example(sample):
    assert sample == "test"
```

### Keep Code Blocks Focused

```python
# Good - single concept per block
assert "hello".upper() == "HELLO"
```

```python
# Less ideal - too much in one block
assert "hello".upper() == "HELLO"
assert "world".lower() == "world"
assert "Python".capitalize() == "Python"
assert "test".replace("t", "T") == "TesT"
# ... many more assertions
```

### Use Realistic Examples

```python
# Good - realistic usage
from datetime import datetime, timedelta

tomorrow = datetime.now() + timedelta(days=1)
assert tomorrow > datetime.now()
```

```python
# Less helpful - trivial example
x = 1
assert x == 1
```

### Include Setup When Needed

```python
# Good - shows complete example
from pathlib import Path
import tempfile

# Create temporary file
with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
    f.write("test data")
    filepath = f.name

# Verify it exists
assert Path(filepath).exists()

# Cleanup
Path(filepath).unlink()
```

## Integration with Documentation Workflow

### During Development

Test your documentation as you write it:

```bash
# Test README while editing
rustest README.md --codeblocks --no-capture

# Watch for changes (with external tool)
# while true; do rustest README.md --codeblocks; sleep 2; done
```

### In CI/CD

Name the pages, and let the shell expand a glob over the directory holding them. This is
rustest's own workflow step, testing its landing page and every page of this guide. The
setting is enabled through this repository's `pyproject.toml`, so the command line itself
needs no flag:

```yaml
# .github/workflows/ci.yml
- name: Test documentation examples
  run: python -m rustest README.md user_guide/*.md
```

If your project has not set `[tool.rustest] codeblocks = true` (or the pytest-ini
spelling), add `--codeblocks` to the line above, or the step passes with nothing collected.

Point the glob at whatever directory holds your pages. A bare directory argument in that
position silently tests nothing, and the step still goes green.

### Pre-commit Hook

pre-commit passes the changed filenames to the hook by default, which is exactly the naming
the collector needs. Restrict it to markdown with `files`:

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: test-docs
      name: Test documentation examples
      entry: rustest --codeblocks
      language: system
      files: \.md$
```

## Programmatic Usage

`rustest.run()` is keyword-only and returns pytest's exit code as an `int`. `codeblocks` is
tri-state and defaults to `None`, meaning "config decides", matching the CLI: it does **not**
default to `True` the way it once did, so `run(paths=["README.md"])` with no config set
collects nothing, and `run(..., codeblocks=False)` is a no-op unless something had turned the
setting on. Pass `report_json=` when you want counts:

<!--rustest.mark.skip-->
```python
import json
from pathlib import Path

from rustest import run

# codeblocks=True is explicit here because this snippet has no pyproject.toml of its own.
exit_code = run(paths=["README.md"], codeblocks=True)

# Several pages, with a machine-readable report written alongside.
run(paths=["README.md", "user_guide/fixtures.md"], codeblocks=True, report_json="docs-report.json")
summary = json.loads(Path("docs-report.json").read_text())["summary"]
print(f"{summary['passed']} passed, {summary['failed']} failed")

# Explicitly off, overriding any config that turned it on.
run(paths=["tests/"], codeblocks=False)
```

## Limitations

- Blocks do not share state, and there is no continuation syntax across them
- Only Python fences are collected
- A block must be a complete, compilable Python fragment unless it is marked skipped
- Interactive console transcripts are not recognised; render them as `text` if you need them
- Autouse fixtures reach a block's inner tests but never the block's own top-level
  statements — see "How a Block Executes" above
- Assertion rewriting does not apply inside a block, the same as any other non-Tier-S `.py`
  file: a failing `assert` is a bare `AssertionError`, without the rewritten
  `assert 41 == 42`-style comparison
- `-n` distributes work by **file**, so a page with many blocks serializes on one worker
  while separate pages parallelize across workers

## Next Steps

- [CLI Usage](cli.md) - Learn about --codeblocks, --no-codeblocks and other options
- [Python API](python-api.md) - Control markdown testing programmatically
- [Writing Tests](writing-tests.md) - Learn about regular Python tests
