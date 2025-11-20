# Pytest Plugin Support - Quick Summary

## TL;DR

**Question**: Should rustest support pytest plugins?

**Answer**: **NO - Not worth the cost**

**Better Alternative**: Implement built-in alternatives for the top 5 most popular plugins

---

## The Numbers

| Approach | Effort | Performance Impact | Maintenance Burden |
|----------|--------|-------------------|-------------------|
| **Full Plugin Support** | 14-19 weeks | 🔴 High (FFI overhead) | 🔴 High |
| **Built-in Alternatives** | 5-7 weeks | 🟢 None (native Rust) | 🟡 Medium |
| **Minimal Hooks** | 6-8 weeks | 🟡 Medium | 🟡 Medium |

---

## Why NOT Full Plugin Support?

1. **Too much work**: 3.5-4.5 months of development
2. **Kills performance**: 15,000+ Python↔Rust calls for 1,000 tests
3. **Architectural mismatch**: Conflicts with Rust-first design
4. **High maintenance**: Must track pytest hook API changes
5. **No guarantees**: Many plugins use private pytest APIs anyway
6. **Adds baggage**: New dependencies, complex codebase

---

## Recommended Approach: Built-in Alternatives

Implement rustest-native versions of the top 5 plugins:

1. **Coverage** (replace pytest-cov) - 2-3 weeks
2. **Parallel execution** (enhance existing Rayon) - 1-2 weeks
3. **Mocking** (replace pytest-mock) - 3-5 days
4. **Timeout** (native implementation) - 1 week
5. **Better async** (enhance pytest-asyncio compat) - 1 week

**Total**: 5-7 weeks (65% faster than full plugin support)

**Benefits**:
- ✅ Covers 90%+ of real-world use cases
- ✅ No performance regression (native Rust)
- ✅ Simpler codebase
- ✅ Better reliability

---

## What About Migration?

**Current State**: Already easy for most projects!

### Projects WITHOUT plugins (majority)
```bash
# Just change the command
pytest tests/          # Before
rustest --pytest-compat tests/  # After
```

### Projects WITH common plugins
```bash
# Before
pytest --cov=src --cov-report=html -n 4 tests/

# After (with built-in alternatives)
rustest --coverage=src --coverage-html -j 4 tests/
```

Migration tool can convert automatically.

### Projects with 5+ plugins or custom hooks
- Accept these are edge cases (<10% of projects)
- Provide migration guide
- They can stick with pytest (nothing wrong with that!)

---

## Top Pytest Plugins (by downloads, Oct 2025)

1. **pytest-cov** (87.7M) - Coverage → **Built-in alternative**
2. **pytest-xdist** (60.3M) - Parallel → **Already have Rayon, enhance CLI**
3. **pytest-asyncio** (58.9M) - Async → **Already have basic support, enhance**
4. **pytest-mock** (50.7M) - Mocking → **Built-in fixture**
5. **pytest-metadata** (20.7M) - Metadata → **Low priority**
6. **pytest-timeout** (20.0M) - Timeout → **Built-in alternative**
7. **pytest-rerunfailures** (19.6M) - Retry → **Future enhancement**

Top 4 can be addressed with 5-7 weeks of work.

---

## How Pytest Plugins Work (Technical)

### Plugin Discovery
- Entry points (`pytest11` in setuptools)
- conftest.py files (auto-discovered)
- PYTEST_PLUGINS environment variable

### Hook System
- **~60 hooks** across 9 categories
- Powered by **pluggy** library
- Collection, execution, reporting, fixtures, etc.
- Complex execution model (ordering, wrappers, result collection)

### Why It's Complex for rustest
- Rust core owns execution → Need Python callbacks
- Every hook = Rust↔Python FFI call (expensive)
- Need to expose Rust state to Python
- Bidirectional synchronization
- Plugin interactions can be subtle

---

## Decision Matrix

### Choose Built-in Alternatives If:
- ✅ You want to maintain rustest's performance advantage
- ✅ You want to cover 90%+ of real-world use cases
- ✅ You want a cleaner, more maintainable codebase
- ✅ You have 5-7 weeks for implementation

### Choose Minimal Hooks If:
- ⚠️ conftest.py customization is critical for your users
- ⚠️ You're okay with 6-8 weeks of work
- ⚠️ You accept some performance impact
- ⚠️ You can clearly document limitations

### Choose Full Plugin Support If:
- ❌ You have 3.5-4.5 months to spare
- ❌ Performance is not a priority
- ❌ You're okay with ongoing maintenance burden
- ❌ **Recommendation: DON'T DO THIS**

---

## Next Steps (If Pursuing Built-in Alternatives)

### Phase 1: Research & Planning (1 week)
- Survey users: Which plugins do they actually use?
- Design CLI interface for built-in alternatives
- Create detailed implementation specs

### Phase 2: Core Implementation (4-6 weeks)
- Coverage integration (2-3 weeks)
- Enhanced parallelism (1-2 weeks)
- Mock fixture (3-5 days)
- Timeout support (1 week)
- Async improvements (1 week)

### Phase 3: Migration Tools (2-3 weeks)
- Plugin detection tool
- Auto-migration script
- Documentation and guides

### Phase 4: Testing & Polish (1-2 weeks)
- Integration tests
- Performance benchmarks
- User documentation

**Total**: 8-12 weeks from start to finish

---

## Conclusion

**Don't implement full plugin support.** It's not worth the cost.

Instead, invest in:
1. ✅ Built-in alternatives for top plugins (5-7 weeks)
2. ✅ Migration tools (2-3 weeks)
3. ✅ Clear documentation on rustest vs pytest

This achieves the goal (easy migration for most projects) without adding baggage or compromising performance.

**Be the best fast test runner for 90% of Python projects**, not a perfect pytest clone for 100% of projects.

---

For detailed analysis, see: `PYTEST_PLUGIN_SUPPORT_ANALYSIS.md`
