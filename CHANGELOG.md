# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
