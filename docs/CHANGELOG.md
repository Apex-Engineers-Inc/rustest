# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.14.0] - 2025-11-24

### Added

- **Request Fixture Value Support**: Implemented `request.getfixturevalue()` for dynamic fixture resolution
  - Global fixture registry for runtime fixture lookup
  - Supports fixture dependencies and per-test caching
  - Clear error messages for async fixtures (use direct injection instead)
  - Fixes ~250 test failures in production pytest codebases

- **External Fixture Module Loading**: Support for loading fixtures from external Python modules
  - `rustest_fixtures` field in conftest.py (preferred, clear naming)
  - `pytest_plugins` field for backwards compatibility
  - Import fixtures from separate Python files for better organization
  - NOT a full plugin system - just simple Python module imports

- **Dynamic Marker Application**: Implemented `request.applymarker()` for runtime marker application
  - Apply markers conditionally based on fixture values or runtime conditions
  - Supports skip, skipif, xfail, and custom markers
  - Enables ~52 previously failing tests in production codebases

- **Class-level Parametrization**: Full support for `@parametrize` decorator on test classes
  - Parametrize all test methods in a class with the same parameters
  - Cartesian product expansion when combined with method-level parametrization

### Fixed

- **Error Message Display**: Fixed critical bug where error messages weren't shown for failing tests
  - Errors now show: test name, file location, error type, code context, expected vs actual values

- **Async Fixture Support**: Implemented full async fixture support in pytest-compat mode
  - Async coroutine fixtures properly awaited using `asyncio.run()`
  - Async generator fixtures use `anext()` instead of `__next__()`
  - Mixed sync/async fixture dependency chains fully supported

- **Markdown Code Block Discovery**: Disabled markdown file discovery in pytest-compat mode
  - Prevents syntax errors from documentation examples

- **@patch Decorator Handling**: Auto-skip tests using `@patch` decorator in pytest-compat mode
  - Clear skip message pointing users to monkeypatch alternative

- Skipped tests now correctly counted as "skipped" instead of "failed"

## [0.13.0] - 2025-11-22

### Added

- **Fixture `name` parameter**: Fixtures can now be registered under a different name than their function name
  - Use `@fixture(name="client")` to make `client_fixture()` accessible as `client` in tests

- **Full indirect parametrization support**: Complete implementation of pytest's `indirect` parameter
  - Support for `indirect=["param1", "param2"]` (list of parameter names)
  - Support for `indirect=True` (all parameters)
  - Enables fixture-based parametrization without `pytest-lazy-fixtures` plugin

- **pytest-mock Compatible Mocker Fixture**: Comprehensive mocking support built-in
  - `mocker.patch()`, `mocker.spy()`, `mocker.stub()`, etc.
  - Full pytest-mock API compatibility

### Fixed

- Type checking error with unnecessary type ignore comment in builtin_fixtures.py

## [0.8.2] - 2025-11-11

### Fixed

- Further fixing of auto path discovery to further mimic pytest behavior

## [0.8.1] - 2025-11-11

### Added

- **`pyproject.toml` pythonpath configuration support**
  - Automatically reads `tool.pytest.ini_options.pythonpath` from pyproject.toml
  - Makes rustest work identically to pytest for import path configuration
  - No more manual PYTHONPATH setup or wrapper scripts needed
  - Falls back to automatic detection if no configuration present
  - Example: Add `pythonpath = ["src"]` to your pyproject.toml

### Changed

- Import path discovery now prioritizes pyproject.toml configuration over auto-detection
- Enhanced project root detection to locate pyproject.toml files accurately

### Fixed

- Library root detection to properly find project root and apply pythonpath configuration

## [0.8.0] - 2025-11-10

### Added

- **Pytest Builtin Fixtures**: Added support for pytest's built-in fixtures including:
  - `tmp_path` and `tmp_path_factory` for temporary directory management with pathlib
  - `tmpdir` and `tmpdir_factory` for py.path compatibility
  - `monkeypatch` fixture for patching attributes, environment variables, and sys.path
  - Full fixture scope support (function, session)

- **Enhanced Benchmark Suites**: Generate richer benchmark suites with support for advanced pytest features and more comprehensive performance testing

### Changed

- Improved documentation with project logo and branding
- Enhanced test fixtures infrastructure for better pytest compatibility

## [0.7.0] - 2025-11-10

### Added

- **PYTHONPATH Discovery**: Automatic sys.path setup that mimics pytest's behavior. Eliminates the need for manual `PYTHONPATH="src"` configuration when working with projects using src-layout or flat-layout patterns.
  - Walks up from test files to find the project root (first directory without `__init__.py`)
  - Automatically detects and adds `src/` directories for projects using src-layout pattern
  - Path setup is integrated into the test discovery pipeline before module loading
  - Works transparently with both standard and src-layout project structures

- **Last-Failed Workflow Options**:
  - `--lf` / `--last-failed`: Rerun only tests that failed in the last run
  - `--ff` / `--failed-first`: Run failed tests first, then all other tests
  - `-x` / `--exitfirst`: Exit instantly on first error or failed test
  - These pytest-compatible options maintain full API compatibility while leveraging Rust-based caching

### Changed

- Integrated Rust-based caching system (`.rustest_cache/`) for fast test result tracking
- Enhanced test discovery pipeline to support filtering and reordering based on cache data
- Improved CLI argument parsing to support new workflow options

### Fixed

- Package import errors in src-layout and regular project structures by implementing automatic PYTHONPATH discovery
- Pytest fixture compatibility in integration tests by updating pytest discovery configuration

## [0.6.0] - 2025-11-10

(See previous releases for earlier changelog entries)
