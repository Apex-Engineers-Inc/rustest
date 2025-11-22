# Coverage Integration

Good news: **coverage.py works seamlessly with rustest**. No plugins, no special configuration—just install and run.

## Quick Start

Install coverage.py:

```bash
pip install coverage
```

Run your tests with coverage:

```bash
coverage run -m rustest tests/
coverage report
```

That's it! You'll see a coverage report like this:

```
Name                      Stmts   Miss  Cover
---------------------------------------------
myapp/__init__.py            2      0   100%
myapp/auth.py               45      3    93%
myapp/database.py           67     12    82%
myapp/utils.py              23      0   100%
---------------------------------------------
TOTAL                      137     15    89%
```

---

## Common Workflows

### HTML Report

Generate a browsable HTML coverage report:

```bash
coverage run -m rustest tests/
coverage html
```

Open `htmlcov/index.html` in your browser to see detailed line-by-line coverage.

### Terminal Report with Missing Lines

```bash
coverage run -m rustest tests/
coverage report -m
```

Output:
```
Name                      Stmts   Miss  Cover   Missing
-------------------------------------------------------
myapp/auth.py               45      3    93%   23-25
myapp/database.py           67     12    82%   45, 67-78
```

Shows exactly which lines aren't covered!

### Fail if Coverage is Too Low

```bash
coverage run -m rustest tests/
coverage report --fail-under=80
```

Exits with error code 1 if coverage is below 80%.

---

## Configuration

Create a `.coveragerc` or `pyproject.toml` to configure coverage:

### Using .coveragerc

```ini
# .coveragerc
[run]
source = src/myapp
omit =
    */tests/*
    */migrations/*
    */__pycache__/*

[report]
exclude_lines =
    pragma: no cover
    def __repr__
    raise AssertionError
    raise NotImplementedError
    if __name__ == .__main__.:

precision = 2

[html]
directory = htmlcov
```

### Using pyproject.toml

```toml
# pyproject.toml
[tool.coverage.run]
source = ["src/myapp"]
omit = [
    "*/tests/*",
    "*/migrations/*",
    "*/__pycache__/*",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
precision = 2

[tool.coverage.html]
directory = "htmlcov"
```

---

## Measuring Specific Paths

### Cover Specific Package

```bash
coverage run --source=myapp -m rustest tests/
coverage report
```

### Cover Multiple Packages

```bash
coverage run --source=myapp,mylib -m rustest tests/
coverage report
```

### Exclude Test Files

```bash
# In .coveragerc
[run]
omit = */tests/*
```

Or on the command line:

```bash
coverage run --omit="*/tests/*" -m rustest tests/
coverage report
```

---

## CI/CD Integration

### GitHub Actions

```yaml
name: Tests with Coverage

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -e .
          pip install rustest coverage

      - name: Run tests with coverage
        run: |
          coverage run -m rustest tests/
          coverage report
          coverage html

      - name: Upload coverage report
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: htmlcov/

      - name: Fail if coverage too low
        run: coverage report --fail-under=80
```

### GitLab CI

```yaml
test:
  image: python:3.11
  script:
    - pip install -e .
    - pip install rustest coverage
    - coverage run -m rustest tests/
    - coverage report
    - coverage html
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    paths:
      - htmlcov/
    expire_in: 1 week
```

The `coverage: '/TOTAL.*\s+(\d+%)$/'` regex extracts the coverage percentage for GitLab's coverage badge.

---

## Combining with Coverage Services

### Codecov

```bash
pip install codecov
coverage run -m rustest tests/
coverage xml
codecov
```

Or in GitHub Actions:

```yaml
- name: Run tests with coverage
  run: |
    coverage run -m rustest tests/
    coverage xml

- name: Upload to Codecov
  uses: codecov/codecov-action@v3
  with:
    files: ./coverage.xml
    fail_ci_if_error: true
```

### Coveralls

```bash
pip install coveralls
coverage run -m rustest tests/
coveralls
```

---

## Advanced: Branch Coverage

Track both line coverage and branch coverage:

```bash
coverage run --branch -m rustest tests/
coverage report
```

This measures if all code branches (if/else) are tested:

```python
def check_age(age):
    if age >= 18:        # Branch 1
        return "adult"
    else:                # Branch 2
        return "minor"
```

Without `--branch`: 100% coverage if you test with `age=20`
With `--branch`: Only 50% coverage because you didn't test the `else` branch!

Configure in `.coveragerc`:

```ini
[run]
branch = True
```

---

## Troubleshooting

### Coverage Shows 0%

**Problem:** Coverage report shows 0% or very low coverage.

**Solution:** Make sure `--source` points to your code, not tests:

```bash
# ❌ WRONG
coverage run -m rustest tests/  # Measures tests/ coverage

# ✅ CORRECT
coverage run --source=myapp -m rustest tests/  # Measures myapp/ coverage
```

Or configure in `.coveragerc`:

```ini
[run]
source = myapp
```

### Import Errors with Coverage

**Problem:** Tests pass with `rustest` but fail with `coverage run -m rustest`.

**Solution:** Coverage changes how Python imports work. Try:

```bash
# Add current directory to PYTHONPATH
PYTHONPATH=. coverage run -m rustest tests/
```

Or install your package in editable mode:

```bash
pip install -e .
coverage run -m rustest tests/
```

### Missing Coverage for Imports

**Problem:** Module imports aren't measured.

**Solution:** Use the `coverage run` option `--concurrency`:

```bash
coverage run --concurrency=multiprocessing -m rustest tests/
```

Or in `.coveragerc`:

```ini
[run]
concurrency = multiprocessing
```

---

## Excluding Code from Coverage

### Using Comments

```python
def debug_only():  # pragma: no cover
    print("Debug information")
    return None
```

The `# pragma: no cover` comment tells coverage to ignore this function.

### Using Configuration

```ini
# .coveragerc
[report]
exclude_lines =
    pragma: no cover
    def __repr__
    if TYPE_CHECKING:
    if __name__ == .__main__.:
    raise NotImplementedError
    @abstractmethod
```

---

## Coverage Best Practices

### ✅ Aim for Meaningful Coverage

High coverage doesn't guarantee good tests. Focus on testing:

- Critical business logic
- Edge cases and error conditions
- Complex algorithms
- Public APIs

Don't obsess over:

- Trivial getters/setters
- `__repr__` methods
- Debug-only code

### ✅ Use Coverage to Find Gaps

Coverage shows **untested code**, not **well-tested code**. Use it to:

- Identify missing test cases
- Find dead code (0% coverage = might be unused!)
- Guide test writing, not as a goal itself

### ✅ Set Realistic Targets

- 80%+ coverage is great for most projects
- 100% coverage is rarely worth the effort
- Focus on critical paths first

### ✅ Combine with Other Quality Metrics

Coverage is one metric. Also use:

- Code reviews
- Static analysis (ruff, mypy, basedpyright)
- Integration tests
- Manual testing for UX

---

## Example: Complete Setup

Here's a complete example for a typical project:

**Directory structure:**

```
my_project/
├── src/
│   └── myapp/
│       ├── __init__.py
│       ├── auth.py
│       └── database.py
├── tests/
│   ├── test_auth.py
│   └── test_database.py
├── pyproject.toml
└── .coveragerc
```

**.coveragerc:**

```ini
[run]
source = src/myapp
branch = True
omit =
    */tests/*
    */__pycache__/*

[report]
precision = 2
exclude_lines =
    pragma: no cover
    if TYPE_CHECKING:
    if __name__ == .__main__.:

[html]
directory = htmlcov
```

**Run tests with coverage:**

```bash
coverage run -m rustest tests/
coverage report
coverage html
```

**Output:**

```
Name                      Stmts   Miss Branch BrPart  Cover
-----------------------------------------------------------
src/myapp/__init__.py        2      0      0      0   100.00%
src/myapp/auth.py           45      3     12      2    91.30%
src/myapp/database.py       67     12     18      4    78.95%
-----------------------------------------------------------
TOTAL                      114     15     30      6    84.21%
```

Open `htmlcov/index.html` to see detailed coverage!

---

## What's Next?

Coverage is set up! Now explore:

- [Feature Comparison](comparison.md) — See all rustest features
- [Migration Guide](migration.md) — Finish migrating from pytest
- [Performance Details](../advanced/performance.md) — Optimize your tests
- [Core Testing Guide](../guide/writing-tests.md) — Master testing patterns

**Happy testing with confidence!** 🎉
