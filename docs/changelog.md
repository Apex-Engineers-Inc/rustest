# Changelog

For the complete, authoritative changelog with all version history and details, see the [main CHANGELOG.md](CHANGELOG.md).

This page includes migration guides and historical context for upgrading rustest.

## Upcoming Features

Planned features for future releases:

- **Parallel execution**: Run tests across multiple cores
- **JUnit XML output**: Generate JUnit-compatible test reports
- **HTML reports**: Generate HTML test reports
- **Fixture parametrization**: Parametrize fixtures like pytest
- **Better error messages**: More helpful assertion failure messages

See our [GitHub issues](https://github.com/Apex-Engineers-Inc/rustest/issues) for the full roadmap.

## Migration Guide

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

See [Comparison with pytest](advanced/comparison.md) for details.

## Links

- [GitHub Repository](https://github.com/Apex-Engineers-Inc/rustest)
- [Issue Tracker](https://github.com/Apex-Engineers-Inc/rustest/issues)
- [PyPI Package](https://pypi.org/project/rustest/)
