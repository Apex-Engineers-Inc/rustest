# Project Structure and Import Paths

Test files have to be able to import the code they test. This page describes exactly which
directories rustest puts on `sys.path`, where they come from, and what to do when an import
fails.

## TL;DR

Set `pythonpath` in your pytest config and your imports work in both runners:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
```

<!--rustest.mark.skip-->
```python
# In your tests
from mypackage import my_function
```

## What Ends Up on sys.path

rustest uses pytest's default `prepend` import mode and nothing else. Three things put
directories on a worker's `sys.path`, in this order:

1. **Every `pythonpath` ini entry**, prepended before any test module or `conftest.py` is
   imported. This is where a `src/` layout gets configured.
2. **The package root of each test file**, inserted at `sys.path[0]` as the file is
   imported. The package root is the first directory above the file that does **not**
   contain an `__init__.py`. A file in a directory with no `__init__.py` at all therefore
   gets its own directory.
3. **The directory you ran rustest from.** Worker processes are launched as
   `python -m rustest._worker` and inherit the orchestrator's working directory, and
   `python -m` puts the current directory on `sys.path`.

Nothing else is inferred. rustest does not look for a `src/` directory, does not search
upward for a project root, and does not add one. If `src/` is on `sys.path`, it is because
`pythonpath` named it or because you ran from a directory that happens to contain it.

## Configuration with pythonpath

`pythonpath` is a pytest ini option, and rustest reads it from every config file pytest reads
it from:

```toml
# pyproject.toml
[tool.pytest.ini_options]
pythonpath = ["src"]
```

```ini
# pytest.ini, or .pytest.ini
[pytest]
pythonpath = src
```

```ini
# tox.ini
[pytest]
pythonpath = src
```

```ini
# setup.cfg
[tool:pytest]
pythonpath = src
```

Entries are resolved against the directory holding the config file, not against the rootdir.
When there is no config file at all, they resolve against the directory you invoked rustest
from. Both rules are pytest's.

A `[pytest]` section in `setup.cfg` is rejected with pytest's own message, `[pytest] section
in setup.cfg files is no longer supported, change to [tool:pytest] instead.` Use
`[tool:pytest]` there.

`-o`/`--override-ini` will not set `pythonpath` from the command line. It supports `addopts`
and refuses everything else rather than accepting the flag and quietly doing nothing.

### Multiple Paths

`pythonpath` takes a list, and every entry is prepended:

```toml
[tool.pytest.ini_options]
pythonpath = ["src", "lib", "vendor"]
```

### Which Config File Wins

rustest resolves the rootdir and the ini file with pytest's algorithm, so the answer matches
the `rootdir:` and `configfile:` lines in pytest's own header. Walking up from the common
ancestor of your path arguments, the first *qualifying* file wins, checked in this order
within each directory:

| File | Qualifies when |
|---|---|
| `pytest.ini` | always, even when empty |
| `.pytest.ini` | it has a `[pytest]` section |
| `pyproject.toml` | it has a `[tool.pytest.ini_options]` table |
| `tox.ini` | it has a `[pytest]` section |
| `setup.cfg` | it has a `[tool:pytest]` section |

A `pyproject.toml` with only a `[project]` table does not stop the search, which is why a
packaging-only `pyproject.toml` above a `tox.ini` still lets the `tox.ini` supply the config.

## How Path Discovery Works, Step by Step

### Step 1: Locate the config file

Starting from the common ancestor of the paths you passed on the command line, rustest walks
**up** the directory tree, testing each directory against the table above:

```text
tests/unit/test_module1.py  <- Start here
    |
tests/unit/                 Any qualifying config file?
    |
tests/                      Any qualifying config file?
    |
myproject/                  Found pyproject.toml with [tool.pytest.ini_options]
```

The directory holding that file becomes the rootdir. When no config file qualifies anywhere
up the tree, rustest falls back to pytest's rules: the nearest ancestor containing a
`setup.py`, and failing that the common ancestor itself.

### Step 2: Prepend the pythonpath entries

Each `pythonpath` entry is made absolute against the config file's own directory and
prepended to `sys.path`. This happens in every worker before a single test module or
`conftest.py` is imported:

```toml
[tool.pytest.ini_options]
pythonpath = ["src", "lib"]
```

That puts `/path/to/myproject/src` and `/path/to/myproject/lib` at the front.

This step is skipped entirely when `pythonpath` is unset, which is the common case. There is
no fallback behind it: nothing takes its place, and no directory is guessed.

### Step 3: Insert each test file's package root

As a file is imported, rustest walks up from it while each directory has an `__init__.py`,
and inserts the first directory that does not at `sys.path[0]`:

```text
myproject/                  <- No __init__.py, so this is the package root
    |
mypackage/                  Has __init__.py, keep going up
    |
mypackage/tests/            Has __init__.py, keep going up
    |
mypackage/tests/test_a.py   Start here
```

The file is then imported as `mypackage.tests.test_a`. The walk also stops at a directory
whose name is not a valid Python identifier, since `my-tests` could never appear in a dotted
module name.

A file with no `__init__.py` beside it gets its own directory as the package root and its
bare stem as the module name.

## Supported Project Layouts

### Src Layout (Recommended for Libraries)

The src layout keeps the package out of the directory you run from, so tests import the
package the same way a user would.

```text
myproject/
├── pyproject.toml      # pythonpath = ["src"]
├── src/
│   └── mypackage/
│       ├── __init__.py
│       ├── module1.py
│       └── module2.py
├── tests/
│   ├── test_module1.py
│   └── test_module2.py
└── README.md
```

This layout **requires** the `pythonpath` setting. Without it, `src/` is on no path and the
import fails:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
```

With it, `myproject/src/` is prepended in every worker and your tests can import:

<!--rustest.mark.skip-->
```python
from mypackage import module1
from mypackage.module2 import SomeClass
```

### Flat Layout (Simpler Projects)

Common for applications that are not published as packages.

```text
myproject/
├── mypackage/
│   ├── __init__.py
│   ├── module1.py
│   └── module2.py
├── tests/
│   ├── test_module1.py
│   └── test_module2.py
└── README.md
```

<!--rustest.mark.skip-->
```python
from mypackage import module1
from mypackage.module2 import SomeClass
```

These imports resolve because `myproject/` is the directory you ran rustest from, and that
directory is on the worker's `sys.path`. Run `rustest .` from inside `tests/` instead and
they stop resolving, because then the working directory is `tests/`. Setting `pythonpath` in
the config makes the layout work from anywhere:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
```

### Nested Package Tests

Tests can live inside the package:

```text
myproject/
├── mypackage/
│   ├── __init__.py
│   ├── module1.py
│   ├── module2.py
│   └── tests/
│       ├── __init__.py
│       ├── test_module1.py
│       └── test_module2.py
└── README.md
```

With `__init__.py` in `tests/`, the `__init__.py` chain runs `tests` → `mypackage` and stops
at `myproject/`, so `myproject/` becomes the package root and goes on `sys.path`. The test
module is imported as `mypackage.tests.test_module1`, and `from mypackage import module1`
works with no configuration at all.

Drop that `__init__.py` and the chain stops immediately: the package root becomes
`myproject/mypackage/tests`, the module is imported as bare `test_module1`, and `mypackage`
is importable only through rule 3, the directory you ran from.

## Common Patterns

### Multiple Source Directories

Several packages under one `src/`:

```text
myproject/
├── src/
│   ├── package1/
│   │   └── __init__.py
│   ├── package2/
│   │   └── __init__.py
│   └── package3/
│       └── __init__.py
└── tests/
```

One `pythonpath` entry covers all of them, because `src/` itself is what goes on the path:

<!--rustest.mark.skip-->
```python
from package1 import module
from package2 import another
from package3 import yet_another
```

### Tests Scattered Across Directories

```text
myproject/
├── src/
│   └── mypackage/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
```

`pythonpath` is resolved from the config file, not from the paths you name, so every one of
these picks up the same `src/`:

```bash
rustest tests/unit/
rustest tests/integration/
rustest tests/
```

### Monorepo with Multiple Projects

```text
monorepo/
├── project1/
│   ├── src/
│   │   └── package1/
│   └── tests/
└── project2/
    ├── src/
    │   └── package2/
    └── tests/
```

Each project needs its own config file, and each run resolves the rootdir from the paths you
give it. Run them separately:

```bash
rustest project1/tests/
rustest project2/tests/
```

## Troubleshooting Import Issues

### ModuleNotFoundError: No module named 'mypackage'

Work through these in order.

1. **Is `pythonpath` set?** A src layout needs it. So does a flat layout you run from
   anywhere but the project root.

   ```toml
   [tool.pytest.ini_options]
   pythonpath = ["src"]
   ```

2. **Is rustest reading the config file you think it is?** rustest resolves the rootdir and
   ini file from the *paths you pass on the command line*, so `rustest ../other/tests` can
   land on a different config file than a bare `rustest`. Check the table above, and check
   that a `pyproject.toml` you are relying on actually has a
   `[tool.pytest.ini_options]` table.

3. **Does the package have the `__init__.py` you think it does?** A directory without one is
   a namespace package, and it resolves by different rules.

   ```bash
   ls src/mypackage/__init__.py
   ```

4. **Is the import spelled from the right root?** `src/` goes on the path, not
   `src/mypackage/`.

<!--rustest.mark.skip-->
   ```python
   # Correct for src/mypackage/module.py
   from mypackage.module import function

   # Wrong: missing the package name
   from module import function
   ```

5. **Print the path from a test.** Fastest way to see what a worker actually has:

<!--rustest.mark.skip-->
   ```python
   def test_debug_path():
       import sys
       print("sys.path:", sys.path)
   ```

   Run it with `-s` so the output is not captured.

### Imports work in pytest but not rustest

Both runners read the same `pythonpath` from the same files, so the usual cause is something
pytest does through a plugin. rustest has no hook system: a module named in `pytest_plugins`
contributes its **fixtures**, and any hook it defines, including
`pytest_configure` code that edits `sys.path`, is inert.

`conftest.py` is imported normally, so path manipulation there does work:

<!--rustest.mark.skip-->
```python
# conftest.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "custom"))
```

`--import-mode` is accepted and ignored, with a line on stderr saying so. rustest implements
`prepend` only.

### Tests pass from the project root but fail from the test directory

This is rule 3 doing the work: your imports are resolving through the directory you ran
from. Name that directory in `pythonpath` and the run stops depending on where it started.

## Best Practices

### Set pythonpath explicitly

Even when a layout happens to work without it, an explicit `pythonpath` is what makes the
run independent of the working directory, and it is read identically by pytest.

### Use a standard layout

Src layout or flat layout. Both are well understood by packaging tools, editors, and type
checkers, and neither needs anything from rustest beyond `pythonpath`.

### Use absolute imports

<!--rustest.mark.skip-->
```python
# Good
from mypackage.module import function

# Fragile: depends on the module's own package identity
from .module import function
```

A relative import only resolves when the test module was imported as part of a package,
which depends on the `__init__.py` chain described above.

### Don't append to sys.path from a test file

<!--rustest.mark.skip-->
```python
# Don't do this in a test file
import sys
sys.path.append('../src')
```

Relative entries are resolved against the working directory, so this breaks as soon as
anyone runs the suite from somewhere else. Configuration is the equivalent that does not.

### Keep tests separate from production code

```text
myproject/
├── src/mypackage/     # Production code
└── tests/             # Test code
```

### Make shared test helpers a package

```text
myproject/
├── src/mypackage/
└── tests/
    ├── __init__.py        # Makes tests a package
    ├── conftest.py        # Shared fixtures
    └── helpers/
        ├── __init__.py
        └── utils.py       # Shared utilities
```

The `__init__.py` files put `myproject/` on the path as the package root, which is what makes
this import resolve:

<!--rustest.mark.skip-->
```python
from tests.helpers.utils import helper_function
```

## Advanced: Understanding the Implementation

**When does path setup happen?** The `pythonpath` entries go on before any test module or
`conftest.py` is imported. Each test file's package root goes on at the moment that file is
imported.

**How many times?** Once per **worker**, not once per run. rustest executes tests in a pool
of worker subprocesses, and each one builds its own `sys.path`. A `conftest.py` that appends
to `sys.path` runs in every worker that loads it.

**What if I run tests from different locations?** The `pythonpath` entries do not move,
because they are resolved from the config file rather than from your shell. Package roots do
not move either, because they depend only on the `__init__.py` chain. The third source, the
directory you ran from, *does* move, which is why a layout relying on it behaves differently
from a different starting directory.

**Can I see what paths were added?**

<!--rustest.mark.skip-->
```python
def test_show_paths():
    import sys
    print("sys.path:", sys.path)
```

Run it with `-s`, since a passing test's output is captured otherwise.

**Is this the same as pytest's prepend mode?** Yes. `prepend` is pytest's default and the
only mode rustest implements. Directories go at the front of `sys.path`, so your project code
takes precedence over an installed package of the same name. `--import-mode` is accepted and
ignored, with a line on stderr saying so.

## Migration from pytest

| Feature | Status |
|---|---|
| `pythonpath` in `pyproject.toml`, `pytest.ini`, `.pytest.ini`, `tox.ini`, `setup.cfg` | Read, with pytest's file precedence |
| rootdir and ini resolution | pytest's `determine_setup` algorithm |
| `prepend` import mode | The only mode; it is pytest's default |
| `conftest.py`, including `sys.path` edits | Imported from the rootdir down |
| Fixtures from a `pytest_plugins` module | Registered |
| Hooks from a `pytest_plugins` module | Inert; rustest has no hook system |
| `--import-mode=importlib` / `append` | Accepted, ignored, reported on stderr |
| `-o pythonpath=...` | Refused; only `addopts` can be overridden |

## Summary

- Set `pythonpath` in your pytest config. It is the one setting that makes imports
  independent of where the run started.
- The other two path sources are the package root of each test file, decided by the
  `__init__.py` chain, and the directory you ran from.
- There is no `src/` auto-detection. A src layout without `pythonpath` will not import.
- `conftest.py` runs; plugin hooks do not.
