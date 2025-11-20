# Pytest Plugin Support Analysis for rustest

## Executive Summary

**Bottom Line**: Full pytest plugin support would be **extremely complex** and add significant baggage to rustest, fundamentally changing its architecture and potentially compromising its core value proposition (performance). However, there are **pragmatic alternatives** that can achieve the migration goal without full plugin support.

**Recommendation**: **Do NOT implement full plugin support**. Instead, pursue a targeted compatibility strategy that:
1. Continues improving the existing pytest-compat layer
2. Implements built-in alternatives for the top 3-5 most critical plugins
3. Provides clear migration guides for plugin-heavy codebases

---

## Current State: What rustest Already Has

### Excellent pytest API Compatibility
rustest already provides strong pytest API compatibility through `python/rustest/compat/pytest.py`:

**Supported Features**:
- ✅ `@pytest.fixture()` with scopes (function/class/module/session)
- ✅ `@pytest.mark.*` decorators
- ✅ `@pytest.mark.parametrize()`
- ✅ `@pytest.mark.skip()` and `@pytest.mark.skipif()`
- ✅ `@pytest.mark.asyncio` (basic support)
- ✅ `pytest.raises()`
- ✅ `pytest.approx()`
- ✅ `pytest.warns()` and `pytest.deprecated_call()`
- ✅ `pytest.importorskip()`
- ✅ Built-in fixtures: `tmp_path`, `tmpdir`, `monkeypatch`, `capsys`, `capfd`, `request`
- ✅ Fixture parametrization with `@pytest.fixture(params=[...])`

**What's Missing for Plugins**:
- ❌ Hook system (pluggy integration)
- ❌ Plugin discovery and loading (entry points)
- ❌ Dynamic plugin registration
- ❌ Hook execution and result collection

---

## Pytest Plugin Architecture: Deep Dive

### How Pytest Plugins Work

Pytest's plugin system is built on **pluggy**, a sophisticated hook-based plugin framework. Here's what makes it complex:

#### 1. Plugin Discovery (3 mechanisms)
- **Entry points**: Plugins register via `setuptools` entry points (`pytest11`)
- **conftest.py**: Auto-discovered files in test directories
- **Environment variables**: `PYTEST_PLUGINS` env var

#### 2. Hook System (~60+ hooks across 9 categories)

**Initialization Hooks** (4 hooks):
- `pytest_addhooks` - Register new hooks
- `pytest_plugin_registered` - Plugin registration callback
- `pytest_addoption` - Add command-line options
- `pytest_configure` - Initial configuration

**Collection Hooks** (12+ hooks):
- `pytest_collection` - Execute collection phase
- `pytest_collection_modifyitems` - Filter/reorder tests
- `pytest_ignore_collect` - Skip paths
- `pytest_collect_file` - Custom file collectors
- `pytest_pycollect_makeitem` - Custom Python collectors
- `pytest_generate_tests` - Dynamic parametrization
- etc.

**Test Execution Hooks** (10+ hooks):
- `pytest_runtestloop` - Main test loop
- `pytest_runtest_protocol` - Test execution protocol
- `pytest_runtest_setup/call/teardown` - Test phases
- `pytest_runtest_makereport` - Create test reports
- etc.

**Reporting Hooks** (8+ hooks):
- `pytest_report_header` - Terminal header
- `pytest_terminal_summary` - Summary section
- `pytest_report_teststatus` - Status reporting
- etc.

**Fixture Hooks** (2 hooks):
- `pytest_fixture_setup` - Fixture execution
- `pytest_fixture_post_finalizer` - Fixture cleanup

**Additional Categories**:
- Session hooks (4+)
- Assertion hooks (5+)
- Warning hooks (2+)
- Debugging hooks (6+)

#### 3. Hook Execution Model

Hooks support complex execution patterns:
- **1:N calls**: Multiple plugins can implement the same hook
- **Ordering control**: `@hookimpl(tryfirst=True)`, `@hookimpl(trylast=True)`
- **Wrappers**: `@hookimpl(wrapper=True)` for wrapping other hooks
- **Result handling**: Some hooks use `firstresult=True`
- **Dynamic argument pruning**: Future-compatible argument passing

---

## Most Popular Pytest Plugins (by downloads)

### Top 7 Plugins (as of Oct 2025)

1. **pytest-cov** (87.7M downloads/month)
   - **Purpose**: Code coverage reporting
   - **Hooks used**: `pytest_configure`, `pytest_terminal_summary`, `pytest_sessionfinish`
   - **Complexity**: High (integrates with coverage.py, xdist support)

2. **pytest-xdist** (60.3M downloads/month)
   - **Purpose**: Parallel/distributed testing
   - **Hooks used**: Multiple collection, execution, and reporting hooks
   - **Complexity**: Very High (changes fundamental execution model)

3. **pytest-asyncio** (58.9M downloads/month)
   - **Purpose**: Asyncio test support
   - **Hooks used**: `pytest_configure`, `pytest_pycollect_makeitem`, `pytest_pyfunc_call`
   - **Complexity**: Medium (rustest has basic built-in support)

4. **pytest-mock** (50.7M downloads/month)
   - **Purpose**: Mocking fixture wrapper
   - **Hooks used**: Minimal (mostly fixture-based)
   - **Complexity**: Low (could be implemented as built-in fixture)

5. **pytest-metadata** (20.7M downloads/month)
   - **Purpose**: Session metadata collection
   - **Hooks used**: `pytest_configure`, `pytest_report_header`
   - **Complexity**: Low

6. **pytest-timeout** (20.0M downloads/month)
   - **Purpose**: Timeout handling for tests
   - **Hooks used**: `pytest_runtest_protocol` or wrapper hooks
   - **Complexity**: Medium

7. **pytest-rerunfailures** (19.6M downloads/month)
   - **Purpose**: Re-run failed tests
   - **Hooks used**: `pytest_runtest_protocol`, reporting hooks
   - **Complexity**: Medium

### Other Notable Plugins
- **pytest-sugar**: UI enhancement (reporting hooks)
- **pytest-django**: Django integration (collection, execution hooks)
- **pytest-picked**: Run tests on changed code (collection hooks)

---

## Technical Requirements for Full Plugin Support

To support pytest plugins, rustest would need to implement:

### 1. Pluggy Integration (~2-3 weeks)
- Add `pluggy` as a core dependency
- Create a `PluginManager` instance
- Define all ~60 hook specifications
- Implement hook calling infrastructure

### 2. Plugin Discovery (~1 week)
- Scan for `pytest11` entry points
- Auto-discover `conftest.py` files
- Support `PYTEST_PLUGINS` environment variable
- Handle plugin registration and initialization

### 3. Hook Implementation in Rust Core (~4-6 weeks)
This is the **hardest part**. The Rust execution engine would need to:
- Call out to Python hook implementations at 20+ points
- Collect and process hook results
- Handle hook execution ordering (tryfirst, trylast, wrappers)
- Support dynamic argument injection
- **Performance impact**: Each hook call is a Rust→Python FFI boundary crossing

### 4. Rust-Python Bridge Extensions (~2 weeks)
- Expose Rust internal state to Python hooks (Config, Session, Items, Reports)
- Create Python-compatible data structures for all hook arguments
- Handle bidirectional communication (hooks can modify Rust state)

### 5. Built-in Hook Implementations (~2-3 weeks)
- Implement rustest's own behavior as hooks (for consistency)
- Ensure hooks are called at the right points in execution
- Handle interaction between built-in and external hooks

### 6. Testing and Compatibility (~3-4 weeks)
- Test with top 10 plugins
- Handle plugin incompatibilities
- Write documentation
- Debug edge cases

**Total Estimated Effort**: **14-19 weeks** (3.5-4.5 months of full-time work)

---

## Implementation Complexity Assessment

### Major Challenges

#### 1. **Architectural Mismatch** ⚠️ CRITICAL
**Problem**: rustest's core value is its Rust-powered performance. Plugins require frequent Rust↔Python calls.

**Impact**:
- Every hook call crosses the FFI boundary (expensive)
- Collection phase: 10+ hook calls per test file
- Execution phase: 5+ hook calls per test
- For 1000 tests: Potentially 15,000+ FFI calls
- **Could negate performance benefits**

#### 2. **State Management Complexity** ⚠️ HIGH
**Problem**: Pytest plugins expect to interact with pytest's internal state (Config, Session, Items).

**Challenges**:
- Rust owns the state, Python needs to modify it
- Need bidirectional synchronization
- Mutable state sharing across FFI is complex
- Potential for subtle bugs

#### 3. **Plugin Ecosystem Compatibility** ⚠️ HIGH
**Problem**: Many plugins make assumptions about pytest internals.

**Reality Check**:
- Some plugins use private pytest APIs
- Plugin interactions are complex (pytest-cov + pytest-xdist)
- Bugs in plugins would affect rustest's reputation
- **Not all plugins will work**, even with hook support

#### 4. **Maintenance Burden** ⚠️ MEDIUM-HIGH
**Problem**: Need to keep up with pytest's hook API changes.

**Ongoing Cost**:
- Pytest adds/changes hooks in new versions
- Plugin ecosystem evolves
- Need to maintain compatibility matrix
- Documentation and support overhead

#### 5. **Dependency Bloat** ⚠️ MEDIUM
**Problem**: Need to add `pluggy` and potentially other dependencies.

**Impact**:
- Increases installation size
- More dependencies to maintain
- Potential version conflicts

---

## Trade-offs Analysis

### Option 1: Full Plugin Support ❌ NOT RECOMMENDED

**Pros**:
- Maximum pytest compatibility
- Supports all existing plugins
- "Drop-in replacement" for pytest

**Cons**:
- **3.5-4.5 months of development time**
- **Significant performance regression** (FFI overhead)
- **High maintenance burden**
- **Architectural complexity** (conflicts with Rust-first design)
- **No guarantee all plugins will work** (internal API dependencies)
- **Adds baggage**: pluggy dependency, complex codebase
- **Diverts from core mission**: Fast test execution

**Verdict**: The costs far outweigh the benefits. This would fundamentally change rustest's identity.

---

### Option 2: Targeted Built-in Alternatives ✅ RECOMMENDED

**Approach**: Implement built-in rustest equivalents for the top 3-5 most critical plugins.

**Target Plugins**:
1. **Coverage** (`pytest-cov` replacement)
   - Integrate with Rust coverage tools (llvm-cov, tarpaulin)
   - Or wrap coverage.py directly without plugin system
   - Effort: 2-3 weeks

2. **Parallel Execution** (`pytest-xdist` replacement)
   - rustest already uses Rayon for parallelism
   - Extend with better CLI options (--numprocesses, etc.)
   - Effort: 1-2 weeks

3. **Mocking** (`pytest-mock` replacement)
   - Add built-in `mock` fixture wrapping unittest.mock
   - Effort: 3-5 days

4. **Timeout** (`pytest-timeout` replacement)
   - Implement in Rust for better reliability
   - Effort: 1 week

5. **Enhanced async** (better `pytest-asyncio` compat)
   - Improve existing `@mark.asyncio` support
   - Effort: 1 week

**Total Effort**: **5-7 weeks** (vs 14-19 weeks for full plugin support)

**Pros**:
- **Much faster to implement** (35-50% of plugin support effort)
- **No performance regression** (native Rust implementation)
- **Better reliability** (controlled, tested implementations)
- **Smaller codebase** (no pluggy, simpler architecture)
- **Covers 90%+ of use cases** (top plugins cover most needs)

**Cons**:
- Not 100% pytest compatible
- Niche plugins won't work
- Need to maintain built-in implementations

**Verdict**: Best balance of compatibility and maintainability.

---

### Option 3: Enhanced Migration Tools ✅ COMPLEMENTARY

**Approach**: Build tools to make migration easier, even without plugin support.

**Components**:
1. **Plugin Detection Tool**
   - Scan `pyproject.toml`/`setup.py` for pytest plugins
   - Analyze `conftest.py` for custom hooks
   - Generate migration report: "You use pytest-cov → Use rustest --coverage"

2. **Auto-migration Script**
   - Convert `pytest.ini` → rustest config
   - Suggest built-in alternatives for plugins
   - Rewrite imports: `import pytest` → `import rustest as pytest`

3. **Compatibility Shims**
   - Create rustest-compatible versions of common plugins
   - Example: `rustest-mock` that works without hooks

**Effort**: **2-3 weeks**

**Pros**:
- **Smooth migration experience**
- **Educational** (helps users understand the differences)
- **Scalable** (one tool helps many projects)
- Works well with Option 2

**Cons**:
- Doesn't solve plugin compatibility
- Requires documentation

**Verdict**: Excellent complement to Option 2.

---

### Option 4: Minimal Hook System (Hybrid) ⚠️ POSSIBLE BUT RISKY

**Approach**: Implement only the 5-10 most critical hooks for conftest.py support.

**Target Hooks**:
- `pytest_configure` - Configuration
- `pytest_collection_modifyitems` - Filter/reorder tests
- `pytest_fixture_setup` - Custom fixture behavior
- `pytest_terminal_summary` - Custom reporting
- `pytest_addoption` - Custom CLI options

**Effort**: **6-8 weeks**

**Pros**:
- Supports `conftest.py` customization
- More flexible than built-in alternatives
- Less work than full plugin support

**Cons**:
- **Still significant complexity**
- **Partial plugin support** (confusing: "some plugins work, others don't")
- **Maintenance burden** (need to document which hooks are supported)
- **Performance impact** (still requires FFI calls)
- **Slippery slope** (users will request more hooks)

**Verdict**: Middle ground, but adds complexity without full compatibility. Could work if there's strong demand for conftest.py support.

---

## Migration Path: Achieving the Goal Without Plugins

The stated goal is:
> "Make it very easy for an existing project to simply migrate over from pytest. I.e., for most common projects, it's as simple as changing the import statements from pytest to rustest."

**Good news**: This is **already mostly achieved** without plugin support!

### Current Migration Path

For a typical pytest project:

```python
# Before (pytest)
import pytest

@pytest.fixture
def database():
    return Database()

@pytest.mark.parametrize("value", [1, 2, 3])
def test_values(value, database):
    assert value > 0
```

**Migration Steps**:
1. Install rustest: `pip install rustest`
2. Change nothing in test code
3. Run: `rustest --pytest-compat tests/`

**That's it!** For projects without plugins, migration is already trivial.

### Projects with Plugins

For projects using popular plugins, provide built-in alternatives:

```bash
# Before
pytest --cov=src --cov-report=html -n 4 tests/

# After (with Option 2 implemented)
rustest --coverage=src --coverage-html -j 4 tests/
```

Migration script can handle this conversion automatically.

### Projects with Heavy Plugin Usage

For projects using 5+ plugins or custom conftest.py hooks:
- **Accept that these are edge cases** (probably <10% of projects)
- **Provide migration guide**: "For plugin-heavy projects, consider gradual migration or staying with pytest"
- **Focus on the 90% use case**, not the 10%

---

## Recommendations

### Primary Recommendation: Option 2 + Option 3 ✅

**Implement**:
1. **Built-in alternatives** for top 5 plugins (5-7 weeks)
   - Coverage integration
   - Enhanced parallelism options
   - Mock fixture
   - Timeout support
   - Better async support

2. **Migration tools** (2-3 weeks)
   - Plugin detection
   - Auto-migration script
   - Compatibility checker

**Total Effort**: **7-10 weeks**

**Benefits**:
- ✅ Covers 90%+ of real-world use cases
- ✅ Maintains rustest's performance advantage
- ✅ Keeps codebase clean and maintainable
- ✅ Achieves migration goal for most projects
- ✅ Clear value proposition: "Faster pytest for most projects"

### Alternative: Option 4 (If conftest.py support is critical)

If user research shows that `conftest.py` customization is a major blocker, consider:
- Implement **5-10 critical hooks** (6-8 weeks)
- Focus on hooks that enable test customization, not plugins
- Document clearly: "For conftest.py only, not for third-party plugins"

**Warning**: This is a slippery slope. Once you have partial hooks, users will want full hooks.

### Do NOT Implement: Full Plugin Support (Option 1) ❌

**Reasons**:
- Too much effort (3.5-4.5 months)
- Compromises core value proposition (performance)
- High maintenance burden
- Doesn't guarantee compatibility anyway
- Adds unnecessary baggage

---

## Conclusion

**Full pytest plugin support is not worth the cost.** It would:
- Take 3.5-4.5 months to implement
- Add significant complexity and dependencies
- Likely negate rustest's performance benefits
- Create ongoing maintenance burden
- Not even guarantee full compatibility

**Better approach**: Invest 7-10 weeks in:
1. Built-in alternatives for top 5 plugins
2. Migration tools to ease the transition
3. Clear documentation on when to use rustest vs pytest

This achieves the migration goal (easy for most projects) without adding baggage, while maintaining rustest's core value proposition: **A fast, clean, pytest-compatible test runner for the 90% use case.**

**The 10% of projects with heavy plugin usage** can:
- Continue using pytest (nothing wrong with that!)
- Do a gradual migration (some tests in rustest, some in pytest)
- Wait for the ecosystem to mature

Focus on being **the best fast test runner for most Python projects**, not a perfect pytest clone for all projects.
