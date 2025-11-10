# Project Structure and Import Paths

Understanding how rustest discovers and configures Python import paths is essential for organizing your test projects effectively.

## TL;DR

**Rustest automatically sets up `sys.path` so your tests can import project code**, just like pytest. You don't need to manually set `PYTHONPATH` or configure import paths.

```python
# In your tests - this just works!
from mypackage import my_function
```

## How Path Discovery Works

When you run rustest, it automatically:

1. **Finds your project root** by walking up from your test files
2. **Detects if you're using a `src/` layout**
3. **Adds the appropriate directories to `sys.path`**
4. **Makes your code importable from tests**

This happens automatically before any tests run, so imports work seamlessly.

## Supported Project Layouts

### Src Layout (Recommended for Libraries)

This is the recommended layout for Python packages that will be published. It prevents accidentally importing from the local source directory instead of the installed package.

```text
myproject/
├── src/
│   └── mypackage/
│       ├── __init__.py
│       ├── module1.py
│       └── module2.py
├── tests/
│   ├── test_module1.py
│   └── test_module2.py
├── pyproject.toml
└── README.md
```

**What gets added to `sys.path`:**
- `myproject/` (project root)
- `myproject/src/` (automatically detected)

**Your tests can import:**
```python
from mypackage import module1
from mypackage.module2 import SomeClass
```

### Flat Layout (Simpler Projects)

This layout is common for applications and simpler projects that won't be published as packages.

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

**What gets added to `sys.path`:**
- `myproject/` (project root)

**Your tests can import:**
```python
from mypackage import module1
from mypackage.module2 import SomeClass
```

### Nested Package Tests

You can also place tests inside your package structure:

```text
myproject/
├── mypackage/
│   ├── __init__.py
│   ├── module1.py
│   ├── module2.py
│   └── tests/
│       ├── test_module1.py
│       └── test_module2.py
└── README.md
```

**What gets added to `sys.path`:**
- `myproject/mypackage/` (parent of tests directory)

## How Path Discovery Algorithm Works

Understanding the algorithm helps debug import issues:

### Step 1: Find the Base Directory

Starting from your test file or directory, rustest walks **up** the directory tree:

```text
tests/unit/test_module1.py  ← Start here
    ↓
tests/unit/                 Has __init__.py? → Keep going up
    ↓
tests/                      Has __init__.py? → Keep going up
    ↓
myproject/                  No __init__.py? → This is the base!
```

The **parent** of the first directory without `__init__.py` becomes the project root.

### Step 2: Check for Src Layout

From the project root, rustest checks if a `src/` directory exists:

```text
myproject/
├── src/          ← Found src/ directory!
└── tests/
```

If found, `src/` is also added to `sys.path`.

### Step 3: Update sys.path

Directories are prepended to `sys.path` (added to the beginning):

```python
sys.path = [
    '/path/to/myproject/src',  # Added first if src/ exists
    '/path/to/myproject',       # Project root added
    # ... other paths
]
```

## Common Patterns and Solutions

### Pattern: Multiple Source Directories

If you have multiple packages in `src/`:

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

**This works!** Since `src/` is added to `sys.path`, you can import any package:

```python
from package1 import module
from package2 import another
from package3 import yet_another
```

### Pattern: Tests Scattered Across Directories

```text
myproject/
├── src/
│   └── mypackage/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
```

**This works!** All test directories under `tests/` will use the same project root and `src/` directory.

```bash
# All of these work correctly
rustest tests/unit/
rustest tests/integration/
rustest tests/
```

### Pattern: Monorepo with Multiple Projects

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

**Each project is independent.** Run tests from each project's directory:

```bash
# Test project1
rustest project1/tests/

# Test project2
rustest project2/tests/
```

## Troubleshooting Import Issues

### Problem: `ModuleNotFoundError: No module named 'mypackage'`

**Check your project structure:**

1. **Is there an `__init__.py`?**
   ```bash
   # For src layout
   ls src/mypackage/__init__.py

   # For flat layout
   ls mypackage/__init__.py
   ```

2. **Are you using the right import?**
   ```python
   # Correct for src/mypackage/module.py
   from mypackage.module import function

   # Incorrect - missing package name
   from module import function
   ```

3. **Check what's in sys.path:**
   ```python
   def test_debug_path():
       import sys
       print("sys.path:", sys.path)
       # Look for your project directory
   ```

### Problem: Imports work in pytest but not rustest

This is likely because pytest has different path discovery rules or you have pytest configuration. Check:

1. **pytest.ini or pyproject.toml:**
   ```ini
   [tool.pytest.ini_options]
   pythonpath = ["custom/path"]
   ```

   Rustest doesn't read pytest configuration. Use the standard layouts above instead.

2. **`conftest.py` with path manipulation:**
   ```python
   # conftest.py
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).parent / "custom"))
   ```

   This will also work in rustest since `conftest.py` files are executed.

### Problem: Tests pass when run from project root but fail from test directory

This suggests you're relying on the current working directory instead of proper imports:

```python
# Bad - depends on current directory
import sys
sys.path.append('.')  # Don't do this!

# Good - use proper imports
from mypackage import module
```

## Best Practices

### ✅ DO: Use Standard Layouts

Stick to the src-layout or flat-layout patterns shown above. These work with rustest, pytest, and other tools.

### ✅ DO: Use Absolute Imports

```python
# Good
from mypackage.module import function

# Avoid
from .module import function  # Relative imports can be tricky
```

### ✅ DO: Keep Tests Separate

```text
myproject/
├── src/mypackage/     # Production code
└── tests/             # Test code (separate)
```

### ❌ DON'T: Manipulate sys.path Manually

```python
# Don't do this in test files
import sys
sys.path.append('../src')
```

Rustest handles this automatically. Manual path manipulation is error-prone.

### ❌ DON'T: Use Relative Paths

```python
# Don't do this
import sys
sys.path.append('../../src')
```

This breaks when tests are run from different directories.

### ✅ DO: Use Package Namespaces

If you have shared test utilities, make them importable:

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

Then import them:
```python
from tests.helpers.utils import helper_function
```

## Migration from pytest

If you're migrating from pytest, **most projects will just work** without changes:

1. ✅ Standard src-layout: Works automatically
2. ✅ Flat layout: Works automatically
3. ✅ conftest.py files: Fully supported
4. ⚠️ pytest.ini `pythonpath` setting: Not read by rustest (use standard layouts instead)
5. ⚠️ Custom pytest plugins modifying sys.path: Won't work (use standard layouts)

## Advanced: Understanding the Implementation

For those interested in the technical details:

**When does path setup happen?**
- During test discovery, before any test modules are loaded
- Only once per rustest invocation

**What if I run tests from different locations?**
- Path discovery is relative to the test file location, not your current directory
- Tests work the same regardless of where you run `rustest` from

**Can I see what paths were added?**
```python
def test_show_paths():
    import sys
    print("Added paths:", [p for p in sys.path if 'myproject' in p])
```

**Is this the same as pytest's prepend mode?**
- Yes! Rustest mimics pytest's default "prepend" import mode
- Directories are added to the beginning of sys.path
- Your project code takes precedence over system packages

## Summary

- **Rustest automatically configures sys.path** - no manual setup needed
- **Use standard layouts** (src-layout or flat-layout) for best results
- **Don't manipulate sys.path manually** - let rustest handle it
- **Use absolute imports** in your tests
- **Keep tests separate** from production code

If you follow these guidelines, imports will "just work" in rustest, just like they do in pytest!
