# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.11.0] - 2025-11-16

### Added

- **Rust-Based Output Formatting**: Complete rewrite of output formatting system using Rust for enhanced performance and responsiveness
  - Real-time file spinner output with progress indicators during test execution
  - All output formatting now implemented in Rust for faster rendering
  - Phase 1: Real-time spinner output for file processing feedback
  - Phase 2 & 3: Complete error formatting pipeline in Rust with Python cleanup

- **Pytest Compatibility Mode**: Enhanced pytest compatibility for running tests
  - Improved compatibility with pytest's test discovery and execution patterns

### Changed

- Output formatting pipeline is now entirely Rust-based for improved performance
- Reorganized and optimized project structure with improved documentation
- Cleaned up temporary exploration files and proof-of-concept examples

### Fixed

- Fixed tests and linting issues following output formatting implementation

## [0.10.0] - 2025-11-12

### Added

- **Enhanced Error Message Formatting**: Dramatically improved test failure output with human-readable error presentation
  - Clear error headers showing exception type and message with visual indicators (red arrows)
  - vitest-style Expected/Received output with color coding for better clarity
  - pytest-style error formatting with code context showing 3 lines of surrounding code
  - Automatic frame introspection to extract actual vs expected values from Python assertions
  - Value substitution in assertion output (e.g., `assert result == expected` becomes `assert 42 == 100`)
  - Support for multiple error message patterns and comparison operators
  - Clickable file links in error messages (path:line format)

- **Improved Test Failure Reporting**: New verbose mode enhancements
  - FAILURES summary section at the end of verbose output showing all failures together
  - Inline failure display during test execution for immediate feedback
  - Better visual hierarchy with color-coded output

### Changed

- Error formatting now parses Python tracebacks to present failures in a more debuggable format
- Rust code now inspects Python frames before they're lost to extract detailed error context

## [0.9.1] - 2025-11-12

### Added

- **Pytest-Compatible Directory Exclusion**: Test discovery now exactly mimics pytest's behavior for excluding directories, preventing tests from being discovered in virtual environments and build artifacts.
  - Implements pytest's default `norecursedirs` patterns: `*.egg`, `.*`, `_darcs`, `build`, `CVS`, `dist`, `node_modules`, `venv`, `{arch}`
  - Intelligent virtualenv detection via marker files:
    - `pyvenv.cfg` for standard Python virtual environments (PEP 405)
    - `conda-meta/history` for conda environments
  - Pattern matching compatible with pytest's fnmatch-style behavior
  - Excludes hidden directories (starting with `.`) automatically
  - Comprehensive test suite with 21 tests covering all exclusion scenarios

### Fixed

- Test discovery no longer finds tests in `venv`, `.venv`, and other virtual environment directories when running `rustest` without a path argument
- Hidden directories (`.git`, `.pytest_cache`, etc.) are now properly excluded from test discovery

## [0.9.0] - 2025-11-12

### Added

- **Autouse Fixtures**: Implement pytest-compatible autouse fixture support, allowing fixtures to automatically execute for all tests in their scope without explicit request.
  - Autouse fixtures work across all scopes (function, class, module, session)
  - Support fixture dependencies for autouse fixtures
  - Comprehensive documentation with examples for common use cases
  - Fully compatible with yield (setup/teardown) fixtures

### Changed

- Optimized CLI report batching to improve performance when processing large test suites

## [0.8.3] - 2025-11-12

### Fixed

- Prevented `mark.parametrize` from treating argument names as missing fixtures when values are provided directly, restoring expected behavior for fixture-using tests.

### Changed

- Clarified the accompanying regression tests with straightforward arithmetic scenarios and fixture usage so the decorator behavior is easier to follow.

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
