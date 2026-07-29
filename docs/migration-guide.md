# Migration Guide

This page includes migration guides, upcoming features, and historical context for upgrading rustest.

For the complete changelog with all version history, see the [Changelog](CHANGELOG.md).

## Upcoming Features

Planned features for future releases:

- **JUnit XML output**: Generate JUnit-compatible test reports
- **HTML reports**: Generate HTML test reports
- **Test timeouts**: Built-in timeout support with `@mark.timeout()`

Three entries that used to be on this list have shipped: **parallel execution control** is
`-n` / `--workers` (never `-j`, which this page listed and rustest has never had);
**coverage integration** is `--cov` / `--cov-report`, which needs the `cov` extra; and
**better error messages** arrived as assertion rewriting, so a failed `assert` now reports
the values (`AssertionError: assert 41 == 42`).

See our [GitHub issues](https://github.com/Apex-Engineers-Inc/rustest/issues) for the full roadmap.

## Version Upgrades

### Migrating from 0.4.x to 0.5.0

No breaking changes! The 0.5.0 release is fully backward compatible with 0.4.x.

**New features:**

1. **Mark-based filtering**: You can now filter tests by marks using the `-m` flag:

```bash
# Run only slow tests
rustest -m "slow"

# Skip slow tests
rustest -m "not slow"

# Complex expressions
rustest -m "(slow or fast) and not integration"
```

2. **Standard pytest marks**: New standard marks are available:

```python
from rustest import mark

@mark.skipif(condition, reason="Skipped because...")
def test_example():
    pass

@mark.xfail(reason="Expected to fail")
def test_failing():
    pass

@mark.usefixtures("setup_fixture")
def test_with_fixture():
    pass
```

3. **Documentation**: Full documentation is now available at https://apex-engineers-inc.github.io/rustest

### Migrating from 0.3.x to 0.4.0

No breaking changes! The 0.4.0 release is fully backward compatible with 0.3.x.

**New feature:** Markdown testing is now enabled by default. If you don't want to test markdown files:

```bash
# Disable markdown testing
rustest --no-codeblocks
```

<!--rustest.mark.skip-->
```python
# Or in Python API
from rustest import run
report = run(paths=["tests"], enable_codeblocks=False)
```

### Migrating from pytest

Most pytest code works with minimal changes:

```python
# Change imports from pytest to rustest
from pytest import fixture, parametrize, mark, approx, raises
# to
from rustest import fixture, parametrize, mark, approx, raises

# Everything else stays the same!
```

**Using pytest plugins?** See our comprehensive [Pytest Plugin Migration Guide](advanced/pytest-plugins.md) for concrete steps to migrate from the top 10 most popular pytest plugins.

See [Comparison with pytest](advanced/comparison.md) for details.

## Links

- [GitHub Repository](https://github.com/Apex-Engineers-Inc/rustest)
- [Issue Tracker](https://github.com/Apex-Engineers-Inc/rustest/issues)
- [PyPI Package](https://pypi.org/project/rustest/)
