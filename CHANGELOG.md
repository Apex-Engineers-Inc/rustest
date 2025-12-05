# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.16.1] - 2025-12-05

### Added

- **Collection Feedback**: Visual feedback during test collection process
  - Shows live spinner with progress updates as files are discovered
  - Displays collection summary with file count and duration
  - Helps users understand that rustest is working when scanning large codebases
  - New collection events: `CollectionStartedEvent`, `CollectionProgressEvent`, `CollectionCompletedEvent`

### Fixed

- **Fixture Discovery**: Fixed named fixture discovery in conftest.py files
  - Fixtures with `name` parameter on `@fixture` decorator are now correctly registered under their custom name
  - Applied `extract_fixture_name()` consistently across all fixture loading functions
  - Improved error messages for unknown fixtures to list all available fixtures alphabetically

- **Test Cancellation**: Fixed Ctrl+C responsiveness during test execution
  - Added signal checking at strategic points in Rust execution loop
  - Tests can now be cancelled with Ctrl+C at any time during execution
  - Graceful termination with proper KeyboardInterrupt propagation

### Changed

- **Collection Performance**: Optimized test collection speed with parallel discovery
  - Parallel file discovery using rayon for concurrent directory traversal
  - Consolidated conftest discovery to single parallel pass (eliminates redundant walks)
  - Optimized Python introspection using `__code__.co_varnames` instead of `inspect.signature()`
  - Optimized function type detection using `__code__.co_flags` for faster checks

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
  - Thread-safe fixture registry access with proper locking

- **External Fixture Module Loading**: Support for loading fixtures from external Python modules
  - `rustest_fixtures` field in conftest.py (preferred, clear naming)
  - `pytest_plugins` field for backwards compatibility
  - Import fixtures from separate Python files for better organization
  - Supports all fixture types (sync, async, scoped, parametrized)
  - NOT a full plugin system - just simple Python module imports

- **Dynamic Marker Application**: Implemented `request.applymarker()` for runtime marker application
  - Apply markers conditionally based on fixture values or runtime conditions
  - Supports skip, skipif, xfail, and custom markers
  - Proper exception handling for test control flow (Skipped, XFailed, Failed)
  - Enables ~52 previously failing tests in production codebases

- **Class-level Parametrization**: Full support for `@parametrize` decorator on test classes
  - Parametrize all test methods in a class with the same parameters
  - Cartesian product expansion when combined with method-level parametrization
  - Matches pytest behavior for class and method parameter combinations

- **Documentation Enhancements**: Comprehensive fixture organization guides
  - New section on loading fixtures from external modules
  - Clear comparison between rustest_fixtures (preferred) and pytest_plugins (compat)
  - Clarification that this is NOT about pytest's plugin ecosystem
  - Examples showing directory structure and practical usage patterns

### Changed

- Skipped tests are now correctly counted as "skipped" instead of "failed"
  - Improved UX with accurate test categorization
  - Proper detection of both `rustest.decorators.Skipped` and `pytest.skip.Exception`

### Fixed

- **Error Message Display**: Fixed critical bug where error messages weren't shown for failing tests
  - Used `MultiProgress::suspend()` to properly display errors while progress bars are active
  - Errors now show: test name, file location, error type, code context, expected vs actual values
  - Users can now see WHY tests failed, not just that they failed

- **Async Fixture Support**: Implemented full async fixture support in pytest-compat mode
  - Async coroutine fixtures properly awaited using `asyncio.run()`
  - Async generator fixtures use `anext()` instead of `__next__()`
  - Async test functions automatically detected and awaited
  - Proper teardown handling for both sync and async generators
  - Mixed sync/async fixture dependency chains fully supported

- **Markdown Code Block Discovery**: Disabled markdown file discovery in pytest-compat mode
  - Prevents syntax errors from documentation examples
  - Markdown code blocks only discovered in rustest native mode
  - Improves compatibility with pytest test suites

- **@patch Decorator Handling**: Auto-skip tests using `@patch` decorator in pytest-compat mode
  - Clear skip message: "@patch decorator not supported. Use monkeypatch fixture instead."
  - Prevents confusing "Unknown fixture" errors
  - Points users to the monkeypatch alternative with migration examples

## [0.13.0] - 2025-11-22

### Added

- **pytest-mock Compatible Mocker Fixture**: Comprehensive mocking support built-in, no pytest-mock dependency needed
  - `mocker.patch()` - Patch objects and modules
  - `mocker.patch.object()` - Patch object attributes
  - `mocker.patch.multiple()` - Patch multiple attributes
  - `mocker.patch.dict()` - Patch dictionaries
  - `mocker.spy()` - Spy on method calls while preserving behavior
  - `mocker.stub()` and `mocker.async_stub()` - Create stub functions
  - Direct access to `Mock`, `MagicMock`, `AsyncMock`, etc.
  - Automatic cleanup of all patches after test completion
  - Full pytest-mock API compatibility for easy migration

- **Indirect Parametrization**: Full support for fixture-based parametrization without pytest-lazy-fixtures plugin
  - `indirect` parameter accepts: `False` (default), `True`, `["param1", "param2"]`, or `"param"`
  - When `indirect=True`, parameter values are treated as fixture names and resolved
  - Enables clean patterns for fixture-based test variations
  - Example: `@parametrize("data", ["fixture1", "fixture2"], indirect=True)`

- **Fixture Renaming**: Support for `name` parameter in `@fixture()` decorator
  - Register fixtures under different names: `@fixture(name="client") def client_fixture(): ...`
  - Access fixture as "client" in tests instead of "client_fixture"
  - Full pytest compatibility for fixture naming patterns

- **Comprehensive Documentation Restructure**: Dual-audience documentation for beginners and pytest users
  - **New to Testing** section: Beginner-friendly progressive guides (why test, first test, basics, fixtures, parametrization, organizing)
  - **Coming from pytest** section: Migration guides, feature comparison, plugin replacements, coverage integration, limitations
  - Enhanced home page with split-column Material Design cards for each audience
  - Coverage.py integration guide with CI/CD examples
  - Plugin replacement guide showing built-in alternatives
  - Honest assessment of unsupported features and trade-offs

### Changed

- **README Simplification**: Streamlined README to provide a concise, high-level overview
  - Condensed verbose sections into brief bullet points
  - Simplified performance benchmarks - reduced from 4 detailed tables to 1 summary table with link to full analysis
  - Streamlined Quick Start section with focused examples
  - Replaced detailed learning path lists with 4 key documentation links
  - Reduced overall README length by ~60% while maintaining all essential information
  - All detailed content remains available in comprehensive documentation site

## [0.12.0] - 2025-11-21

### Added

- **Fixture Parametrization**: Full pytest-compatible fixture parametrization support
  - `@fixture(params=[...])` decorator parameter for parametrized fixtures
  - Custom IDs with `pytest.param()` for better test identification
  - Callable `ids` parameter for dynamic ID generation
  - Cartesian product expansion when multiple parametrized fixtures are used
  - Support for all fixture scopes (function, class, module, package, session)
  - Works with yield fixtures and fixture dependency chains
  - `request.param` attribute for accessing current parameter value

- **Enhanced pytest Compatibility**: Comprehensive compatibility improvements achieving 91.5% pass rate on real-world pytest test suites
  - `pytest.skip()` function for dynamic test skipping at runtime
  - `pytest.xfail()` function for marking expected failures
  - `pytest.fail()` function for explicit test failures
  - Fixed `pytest.mark.skipif()` signature to accept both positional and keyword `reason` argument
  - Enhanced `@mark.asyncio` to accept non-async functions for pytest compatibility
  - Support for `argvalues` parameter name in `parametrize()` (pytest standard)

- **Built-in Fixtures**: Essential pytest fixtures for comprehensive test support
  - `caplog` fixture for capturing and asserting on logging output
    - Access to `records`, `messages`, `text`, and `record_tuples` properties
    - `set_level()` and `at_level()` methods for log level control
    - Context manager support for temporary level changes
  - `cache` fixture for persistent data storage between test runs
    - JSON-based storage in `.rustest_cache/` directory
    - Dict-style access with `get()` and `set()` methods
    - `mkdir()` for creating cache directories

- **Request Object Enhancements**: Advanced fixture metadata and configuration access
  - `request.node` object for test metadata and marker access
    - `node.name` and `node.nodeid` for test identification
    - `node.get_closest_marker(name)` for marker retrieval
    - `node.add_marker(marker)` for dynamic marker addition
    - `node.keywords` and `node.listextrakeywords()` for marker inspection
  - `request.config` object for configuration access
    - `config.getoption(name, default)` for command-line option access
    - `config.getini(name)` for ini configuration values
    - `config.option` namespace for attribute-style option access
    - `config.rootpath` for project root directory path

- **Markdown Testing Improvements**: Enhanced documentation testing with better error messages
  - Line numbers in codeblock test names (e.g., `codeblock_0_line_14`)
  - pytest-style display names for markdown tests (e.g., `file.md::codeblock_0::line_14`)
  - Clear error location display (e.g., "at file.md:L14:7")
  - Improved traceback formatting with descriptive filenames
  - HTML comment skip markers: `<!--rustest.mark.skip-->` (primary), `<!--pytest.mark.skip-->` and `<!--pytest-codeblocks:skip-->` (compatibility)
  - Comprehensive documentation testing guide in CLAUDE.md

### Changed

- Improved pytest compatibility from ~70% to ~85% based on real-world testing
- Enhanced error messages for markdown code block failures with precise location information
- Simplified CI markdown testing configuration using bash brace expansion

### Fixed

- Fixed `pytest.mark.skipif()` signature mismatch that blocked pydantic and other projects
- Fixed infinite loop when testing markdown files containing `run()` examples
- Fixed documentation code blocks to be executable and validated in CI

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

