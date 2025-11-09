# Changelog

All notable changes to rustest will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2025-11-09

### Changed
- Bumped package version from 0.4.0 to 0.5.0

## [0.4.0] - 2025-01-08

### Added
- Python markdown block support - test Python code blocks in markdown files
- Built-in markdown testing similar to pytest-codeblocks but faster
- CLI flag `--no-codeblocks` to disable markdown testing
- Python API parameter `enable_codeblocks` for programmatic control

### Changed
- Bumped package version from 0.3.0 to 0.4.0
- Enhanced conftest.py documentation

### Fixed
- Updated uv.lock with correct version

## [0.3.0] - Previous Release

### Added
- Test class support with fixture methods
- Class-scoped fixtures
- Nested conftest.py support
- Yield fixtures with setup/teardown
- Custom marks system (`@mark.slow`, `@mark.integration`, etc.)
- Mark arguments support (`@mark.timeout(seconds=30)`)
- Exception testing with `raises()`
- Floating-point comparison with `approx()`
- Collection comparison support in `approx()`
- Complex number support in `approx()`

### Changed
- Improved fixture resolution performance
- Enhanced test discovery speed
- Better error messages and tracebacks

### Fixed
- Various bug fixes and stability improvements

## [0.2.0] - Early Release

### Added
- Basic test discovery
- Fixture support with scopes (function, class, module, session)
- Parametrization with `@parametrize`
- Test skipping with `@skip`
- CLI with pattern filtering (`-k`)
- Output capture control (`--no-capture`)
- Python API with `run()` function
- Test reporting with `RunReport` and `TestResult`

### Performance
- ~2x faster than pytest on integration suite
- Rust-native test discovery
- Optimized fixture resolution

## [0.1.0] - Initial Release

### Added
- Initial proof of concept
- Basic pytest compatibility
- Rust-powered test execution
- Simple fixture support
- Command-line interface

---

## Upcoming Features

### [Unreleased]

Planned features for future releases:

- **Mark-based filtering**: Filter tests by marks with `-m` option
- **Parallel execution**: Run tests across multiple cores
- **JUnit XML output**: Generate JUnit-compatible test reports
- **HTML reports**: Generate HTML test reports
- **Fixture parametrization**: Parametrize fixtures like pytest
- **Better error messages**: More helpful assertion failure messages

See our [GitHub issues](https://github.com/Apex-Engineers-Inc/rustest/issues) for the full roadmap.

## Migration Guide

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
