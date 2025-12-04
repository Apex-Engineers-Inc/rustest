# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Test Collection Feedback**: Real-time visual feedback during test discovery
  - Animated spinner with "Collecting tests" message during the discovery phase
  - Live progress updates showing file and test counts as they're discovered (e.g., "52 files, 893 tests")
  - Collection summary after discovery completes (e.g., "✓ Collected 893 tests from 52 files (531ms)")
  - Supports ASCII mode with `[OK]` instead of `✓` for terminals without Unicode
  - "No tests collected" warning when no tests are found
  - New event types for custom renderers: `CollectionStartedEvent`, `CollectionProgressEvent`, `CollectionCompletedEvent`

## [0.16.0] - 2025-12-03

### Added

- **Parallel Async Test Execution**: Revolutionary performance improvement for async tests with shared event loop scopes
  - Tests that share the same event loop scope (class, module, or session) now run concurrently using `asyncio.gather()`
  - Provides significant speedups for I/O-bound async tests (10x+ in many cases)
  - Automatic batching of tests by loop scope for optimal parallelization
  - Respects fixture scopes and boundaries (shared fixtures resolved once, function fixtures per-test)
  - Graceful error handling with `return_exceptions=True` for fault tolerance
  - Falls back to sequential execution in fail-fast mode
  - Comprehensive test suite with 40+ tests covering edge cases, error isolation, and concurrent fixture access

- **Per-Test Timeout Support**: Built-in timeout functionality for async tests without external plugins
  - New `timeout` parameter for `@mark.asyncio()` decorator: `@mark.asyncio(timeout=5.0)`
  - Each test's timeout is independent - timeouts don't affect other parallel tests
  - Implemented via `asyncio.wait_for()` for clean cancellation handling
  - Works with both sequential and parallel async execution
  - Supports integer and float timeout values
  - Comprehensive validation with clear error messages for invalid values (negative, zero, non-numeric)
  - Combines seamlessly with loop_scope parameter: `@mark.asyncio(loop_scope="module", timeout=10.0)`

- **Enhanced Test Coverage**: Expanded test suite for critical functionality
  - New `tests/test_regression_bugs.py` for tracking and preventing bug regressions
  - New `tests/test_fixture_dependency_chains.py` for complex fixture dependency testing
  - New `tests/test_parametrize_data_types.py` for parametrization edge cases
  - Expanded `python/tests/test_builtin_fixtures.py` with edge case coverage
  - Expanded `python/tests/test_cli.py` with return code and edge case tests
  - Total test count increased to 850+ tests

- **Comprehensive Async Testing Documentation**: Major documentation overhaul for async testing
  - New "What is Async?" section for beginners with clear explanations
  - Detailed "Built-in Timeout Support" section highlighting feature advantages
  - Timeout best practices and common pitfalls documentation
  - Performance comparison tables showing advantages over pytest-asyncio
  - Migration guide with before/after examples for pytest-asyncio users
  - Updated best practices recommending timeout usage for all async tests

### Fixed

- **Thread-Local Safety**: Improved ACTIVE_RESOLVER thread-local safety with pointer verification
  - Added release-mode assertion for resolver stack integrity (upgraded from debug_assert to assert)
  - Prevents potential memory safety issues in multi-threaded contexts

- **Parametrized Fixture Bounds Checking**: Added bounds checking for parametrized fixture resolution
  - Prevents panics when accessing fixture parameters out of range
  - More robust error handling for edge cases in fixture parametrization

- **Class Cache Management**: Clear class cache on package boundary changes
  - Ensures proper cache invalidation when moving between test packages
  - Prevents stale fixture data from affecting subsequent tests

- **MonkeyPatch Validation**: Fixed `MonkeyPatch.setattr()` to validate dotted paths
  - Now correctly rejects paths without at least one dot
  - Provides clear error messages for invalid attribute paths

- **Type Comparison Flexibility**: Relaxed `approx.py` type strictness
  - Allows list vs tuple comparison for better pytest compatibility
  - More forgiving numeric comparison behavior

### Changed

- Async test execution strategy now prioritizes parallel execution for shared loop scopes
- Error handling in async batch execution now uses `BaseException` instead of `Exception` for proper cancellation support
- Documentation now emphasizes parallel execution and timeout as key differentiators from pytest-asyncio

## [0.15.0] - 2025-12-02

### Added

- **Event Stream Architecture with Rich Terminal Rendering**: Complete redesign of test output system for beautiful, real-time feedback
  - Event-based architecture enabling multiple output consumers (terminal, VS Code, JSON, etc.)
  - Beautiful terminal output using the rich library with progress bars
  - Real-time file-level progress indicators showing test execution status
  - Compact progress display with green checkmarks (✓) for passing files, red (✗) for failures
  - Live progress percentage and test counts with duration tracking
  - Automatic terminal width adaptation for responsive display
  - Foundation for parallel test execution and IDE integrations

- **Smart Async Event Loop Detection**: Intelligent automatic event loop management for async tests and fixtures
  - Automatic loop scope detection based on fixture dependency analysis
  - Tests automatically use the widest async fixture scope (function → class → module → session)
  - Eliminates "Task got Future attached to a different loop" errors
  - Session-scoped async fixtures seamlessly work with function-scoped tests
  - Explicit control available via `@mark.asyncio(loop_scope="...")` when needed
  - Pytest-asyncio compatible with better automatic defaults
  - Comprehensive beginner-friendly documentation in `docs/async-event-loops.md`

- **Native Fixture Resolution Enhancements**: Deep integration of fixture resolution into Rust execution engine
  - `request.getfixturevalue()` now calls directly into Rust backend for improved performance
  - `@mark.usefixtures` eagerly resolves specified fixtures in Rust execution layer
  - Generator fixtures fetched via `getfixturevalue` correctly trigger teardown logic
  - Enhanced fixture request with nodeid and marks support
  - Thread-safe fixture resolver activation for concurrent access

- **Nested Conftest Discovery**: Robust fixture discovery for complex project structures
  - Automatically loads ancestor conftest.py files for nested test directories
  - Async autouse fixture support in nested conftest files
  - Proper fixture scope resolution across directory hierarchies
  - `RUSTEST_RUNNING` environment variable for detecting rustest execution context

- **Advanced CLI Output Control**: Flexible output formatting options for different environments
  - `--color` flag with three modes:
    - `auto` (default): Colors ON locally, OFF in CI environments
    - `always`: Force colors ON everywhere
    - `never`: Force colors OFF everywhere
  - Automatic CI detection across all major providers (GitHub Actions, GitLab CI, CircleCI, Travis CI, Jenkins, etc.)
  - `--ascii` flag for ASCII-only output (PASS/FAIL/SKIP instead of Unicode symbols)
  - Clean, readable logs in CI without manual configuration

- **VHS Demo Recording Infrastructure**: Automated terminal demo generation for documentation
  - VHS tape files for generating beautiful terminal recordings
  - Automated regeneration in CI when output code changes
  - Multiple output formats (GIF, PNG, WebM)
  - Task runner integration (`poe demos`)

### Changed

- Pytest compatibility banner now uses rich Panel formatting for consistent styling
- Event loop creation strategy changed from isolated per-test to scope-based sharing
- `@mark.asyncio` decorator no longer wraps functions; only applies metadata for Rust execution layer
- Output rendering moved from Python to event-stream architecture for better performance
- Improved test output formatting with better error display during progress tracking

### Fixed

- **Multiple @parametrize Cross-Products**: Fixed cartesian product expansion when multiple `@parametrize` decorators are applied
  - Multiple decorators now correctly create cross-products of all parameter combinations
  - Example: `@parametrize("a", [1,2,3])` + `@parametrize("b", [4,5])` now creates 6 tests (3×2)
  - Previously only parameters from one decorator would be used
  - Indirect parameter lists from multiple decorators properly merged

- **Async Fixture Event Loop Isolation**: Fixed critical event loop mismatch bugs
  - Session-scoped async fixtures no longer cause "different loop" errors with function tests
  - Function-scoped async fixtures correctly reuse session event loops when needed
  - Class-based tests with `@mark.asyncio` no longer override smart loop detection
  - Fixed `@mark.asyncio` defaulting to `loop_scope="function"` inappropriately

- **skipif String Condition Evaluation**: `@mark.skipif` now correctly evaluates string conditions using module globals
  - String expressions like `skipif("sys.platform == 'win32'")` now work correctly
  - Condition evaluation uses proper module context

- **Fixture Name Extraction**: Corrected fixture name extraction from `@mark.usefixtures` arguments
  - Fixed regression in argument parsing for usefixtures decorator
  - Proper handling of fixture name lists

- **Rust Deprecation Warnings**: Updated to modern PyO3 APIs
  - Replaced deprecated `Python::with_gil` with `Python::attach`
  - Removed unnecessary `.clone()` on Copy types

- **Test Skip Detection**: Improved pytest-only test detection for rustest-native features
  - Loop scope detection tests properly skip when running with pytest
  - Integration tests correctly detect execution context
  - No more collection errors in pytest-compat mode

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
