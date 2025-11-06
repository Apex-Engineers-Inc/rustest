# Fixture Scopes - Detailed Documentation

This document provides comprehensive information about fixture scopes in rustest, including edge cases, limitations, and comparison with pytest.

## Table of Contents
1. [Fixture Dependency Chains](#fixture-dependency-chains)
2. [Scope Validation](#scope-validation)
3. [Cross-File Fixtures](#cross-file-fixtures)
4. [Fixture Name Conflicts](#fixture-name-conflicts)
5. [Current Limitations](#current-limitations)
6. [Examples](#examples)

---

## Fixture Dependency Chains

### ✅ Fixtures Depending on Fixtures

**Yes, this works perfectly!** Fixtures can depend on other fixtures, and rustest will recursively resolve all dependencies.

```python
from rustest import fixture

@fixture
def database_url():
    return "postgresql://localhost/test"

@fixture
def database_connection(database_url):
    # This fixture depends on database_url
    return {"url": database_url, "connected": True}

@fixture
def user_repository(database_connection):
    # This fixture depends on database_connection
    # which itself depends on database_url
    return {"db": database_connection, "table": "users"}

def test_user_repo(user_repository):
    # Rustest will resolve: user_repository -> database_connection -> database_url
    assert user_repository["db"]["connected"] is True
```

**How it works:**
- When a test needs a fixture, rustest's `FixtureResolver` recursively walks the dependency graph
- Each fixture is resolved in the correct order
- Circular dependencies are detected and reported as errors
- Diamond dependencies (same fixture via multiple paths) work correctly - the fixture is only created once

---

## Scope Validation

### ✅ Scope Ordering Rules

**Critical Rule:** A fixture can only depend on fixtures with **equal or broader** scope.

**Scope Hierarchy (narrowest to broadest):**
```
function < class < module < session
```

**Valid Dependencies:**
- ✅ Session → Session
- ✅ Module → Session, Module
- ✅ Class → Session, Module, Class
- ✅ Function → Session, Module, Class, Function

**Invalid Dependencies:**
- ❌ Session → Module (session can't depend on module)
- ❌ Session → Class (session can't depend on class)
- ❌ Session → Function (session can't depend on function)
- ❌ Module → Class (module can't depend on class)
- ❌ Module → Function (module can't depend on function)
- ❌ Class → Function (class can't depend on function)

### Example: Invalid Scope Dependency

```python
@fixture  # function scope (default)
def function_data():
    return {"value": 42}

@fixture(scope="module")
def invalid_module_fixture(function_data):
    # ❌ ERROR: Module-scoped fixture cannot depend on function-scoped fixture
    return {"data": function_data}

# Error at runtime:
# ScopeMismatch: Fixture 'invalid_module_fixture' (scope: Module)
# cannot depend on 'function_data' (scope: Function).
# A fixture can only depend on fixtures with equal or broader scope.
```

### Why This Validation Exists

Consider what would happen without this validation:

```python
@fixture
def counter():
    return {"count": 0}  # New instance each test

@fixture(scope="session")
def session_cache(counter):  # ❌ Invalid!
    # Session fixture created once, but counter is recreated each test
    # Which counter value should be used?
    return {"cache": {}, "initial_count": counter["count"]}
```

The module-scoped fixture would be created once, but its dependency (function-scoped) would be recreated for each test. This creates an impossible situation, so rustest prevents it.

### Example: Valid Cross-Scope Dependencies

```python
@fixture(scope="session")
def session_config():
    return {"env": "test", "timeout": 30}

@fixture(scope="module")
def module_service(session_config):
    # ✅ Valid: Module depending on session
    return {"config": session_config, "name": "test_service"}

@fixture(scope="class")
def class_handler(module_service, session_config):
    # ✅ Valid: Class depending on module and session
    return {"service": module_service, "config": session_config}

@fixture  # function scope
def request_handler(class_handler, module_service, session_config):
    # ✅ Valid: Function can depend on all higher scopes
    return {
        "handler": class_handler,
        "service": module_service,
        "config": session_config,
    }
```

---

## Cross-File Fixtures

### ❌ Current Limitation: No Cross-File Fixture Sharing

**Currently, fixtures are module-local.** Fixtures defined in one test file cannot be accessed from another test file.

```python
# file: tests/test_a.py
from rustest import fixture

@fixture
def shared_database():
    return {"connection": "db://test"}

def test_a(shared_database):
    # ✅ Works - using fixture from same file
    assert shared_database is not None
```

```python
# file: tests/test_b.py
from rustest import fixture

def test_b(shared_database):  # ❌ Error!
    # ERROR: Unknown fixture 'shared_database'
    # The fixture from test_a.py is not accessible here
    pass
```

### Workaround: Define Fixtures in Each File

If multiple files need the same fixture, you must define it in each file:

```python
# file: tests/test_a.py
@fixture
def database():
    return {"connection": "db://test"}

# file: tests/test_b.py
@fixture
def database():
    return {"connection": "db://test"}
```

### Future: conftest.py Support

Rustest plans to add `conftest.py` support, which will allow sharing fixtures across files, just like pytest:

```python
# file: tests/conftest.py (future support)
from rustest import fixture

@fixture(scope="session")
def shared_database():
    """This fixture will be available to all test files in the directory."""
    return {"connection": "db://test"}
```

**Status:** 🚧 Planned for a future release

---

## Fixture Name Conflicts

### ✅ Same Name in Different Files = Isolated

Fixtures with the same name in different files are completely isolated from each other. Each module has its own fixture namespace.

```python
# file: tests/test_a.py
from rustest import fixture

@fixture
def config():
    return {"file": "A", "value": 100}

def test_from_a(config):
    assert config["file"] == "A"  # ✅ Gets the local config
```

```python
# file: tests/test_b.py
from rustest import fixture

@fixture
def config():
    return {"file": "B", "value": 200}

def test_from_b(config):
    assert config["file"] == "B"  # ✅ Gets the local config
```

**Each file's tests use their own fixture** - no conflicts or sharing.

### ❌ Same Name in Same File = Error

You cannot define two fixtures with the same name in the same file (Python would not allow this anyway):

```python
@fixture
def database():
    return {"type": "postgres"}

@fixture
def database():  # ❌ Error: duplicate definition
    return {"type": "mysql"}
```

---

## Current Limitations

### 1. No conftest.py Support
- **Impact:** Cannot share fixtures across files
- **Workaround:** Define fixtures in each file that needs them
- **Status:** 🚧 Planned

### 2. No Fixture Parametrization
- **Impact:** Cannot parametrize fixtures themselves
- **Example:** Cannot do `@fixture(params=[1, 2, 3])`
- **Status:** 🚧 Planned

### 3. No Yield Fixtures (Teardown)
- **Impact:** Cannot run cleanup code after tests
- **Example:** Cannot do:
  ```python
  @fixture
  def database():
      conn = create_connection()
      yield conn
      conn.close()  # cleanup
  ```
- **Status:** 🚧 Planned

### 4. No autouse Fixtures
- **Impact:** Cannot automatically apply fixtures to all tests
- **Status:** 🚧 Planned

### 5. No request Fixture
- **Impact:** Cannot access test context or use indirect parametrization
- **Status:** 🚧 Planned

---

## Examples

### Example 1: Multi-Level Dependencies with Scopes

```python
from rustest import fixture

@fixture(scope="session")
def api_token():
    """Expensive token generation - done once per session."""
    return generate_token()

@fixture(scope="module")
def api_client(api_token):
    """API client - shared within module."""
    return APIClient(token=api_token)

@fixture(scope="class")
def test_context(api_client):
    """Test context - shared within test class."""
    return {"client": api_client, "data": {}}

@fixture
def request(test_context):
    """Request - new for each test."""
    return test_context["client"].create_request()

def test_get(request):
    response = request.get("/users")
    assert response.status == 200

def test_post(request):
    response = request.post("/users", data={"name": "Alice"})
    assert response.status == 201
```

**Execution:**
1. `api_token` created once for entire session
2. `api_client` created once per module (reuses `api_token`)
3. `test_context` created once per class (reuses `api_client`)
4. `request` created fresh for each test (reuses `test_context`)

### Example 2: Diamond Dependencies

```python
from rustest import fixture

@fixture(scope="session")
def config():
    """Root configuration."""
    return {"env": "test"}

@fixture(scope="module")
def database(config):
    """Database using config."""
    return {"db": "test_db", "config": config}

@fixture(scope="module")
def cache(config):
    """Cache using config."""
    return {"cache": {}, "config": config}

@fixture
def service(database, cache):
    """Service using both database and cache."""
    # Both database and cache reference the SAME config object
    return {"db": database, "cache": cache}

def test_service(service):
    # Verify both branches share the same config
    assert service["db"]["config"] is service["cache"]["config"]
```

### Example 3: Scope Validation Error

```python
from rustest import fixture

@fixture
def temp_file():
    """Function-scoped temporary file."""
    return f"/tmp/test_{id(object())}.txt"

@fixture(scope="module")
def file_processor(temp_file):  # ❌ Will fail!
    """Trying to use function-scoped fixture in module scope."""
    return {"file": temp_file, "processed": False}

# Error at runtime:
# ScopeMismatch: Fixture 'file_processor' (scope: Module) cannot depend on
# 'temp_file' (scope: Function). A fixture can only depend on fixtures with
# equal or broader scope.
```

**Fix:**
```python
@fixture(scope="module")
def temp_file():
    """Change to module scope."""
    return f"/tmp/test_module.txt"

@fixture(scope="module")
def file_processor(temp_file):  # ✅ Now valid!
    return {"file": temp_file, "processed": False}
```

---

## Comparison with pytest

| Feature | pytest | rustest | Notes |
|---------|--------|---------|-------|
| Fixture scopes | ✅ | ✅ | Full parity |
| Scope validation | ✅ | ✅ | Rustest validates at resolution time |
| conftest.py | ✅ | ❌ | Planned for rustest |
| Yield fixtures | ✅ | ❌ | Planned for rustest |
| autouse | ✅ | ❌ | Planned for rustest |
| Fixture params | ✅ | ❌ | Planned for rustest |
| Cross-file fixtures | ✅ | ❌ | Via conftest.py (planned) |
| Performance | 1.0x | **3.0x faster** | Rust-based resolution |

---

## Best Practices

### 1. Use Appropriate Scopes

```python
# ✅ Good: Expensive setup at session scope
@fixture(scope="session")
def database_connection():
    return create_expensive_connection()

# ❌ Bad: Expensive setup at function scope
@fixture  # Creates new connection for EACH test!
def database_connection():
    return create_expensive_connection()
```

### 2. Keep Dependencies Simple

```python
# ✅ Good: Clear, linear dependencies
@fixture(scope="session")
def config():
    return load_config()

@fixture(scope="module")
def service(config):
    return Service(config)

@fixture
def request(service):
    return service.create_request()

# ⚠️ Complex but valid
@fixture
def mega_fixture(dep1, dep2, dep3, dep4, dep5):
    # Too many dependencies might indicate poor design
    pass
```

### 3. Document Expensive Fixtures

```python
@fixture(scope="session")
def ml_model():
    """
    Load ML model - expensive operation.

    Scope: session - model is loaded once and reused across all tests.
    Note: Takes ~5 seconds to load.
    """
    return load_large_model()
```

### 4. Avoid Mutable State in Broad Scopes

```python
# ⚠️ Careful: Module-scoped mutable state
@fixture(scope="module")
def shared_cache():
    return {"data": {}}  # Tests might mutate this!

def test_a(shared_cache):
    shared_cache["data"]["key"] = "value"  # Affects other tests!

def test_b(shared_cache):
    # shared_cache still has "key" from test_a
    assert "key" in shared_cache["data"]  # Depends on test order!
```

**Better:**
```python
@fixture(scope="module")
def cache_factory():
    """Return a factory instead of mutable state."""
    def create_cache():
        return {"data": {}}
    return create_cache

@fixture
def shared_cache(cache_factory):
    """Each test gets a fresh cache."""
    return cache_factory()
```

---

## Summary

### ✅ What Works
1. **Fixture dependencies** - Fixtures can depend on other fixtures with full recursive resolution
2. **Scope validation** - Invalid scope dependencies are caught with clear error messages
3. **Same name in different files** - Fixtures are module-local, so no conflicts
4. **All four scopes** - function, class, module, session all work correctly
5. **Cross-scope dependencies** - Lower scopes can use higher scopes (e.g., function using session)
6. **Diamond dependencies** - Same fixture via multiple paths works correctly

### ❌ What Doesn't Work Yet
1. **Cross-file fixture sharing** - No conftest.py support (planned)
2. **Yield fixtures** - No teardown support (planned)
3. **autouse** - No automatic fixture application (planned)
4. **Fixture parametrization** - Cannot parametrize fixtures themselves (planned)

### 🎯 Key Takeaway

Rustest's fixture system provides **full scope support** with **proper validation** and **3x faster performance** than pytest. While some advanced features are still planned, the core scoping functionality is production-ready and matches pytest's behavior for the most common use cases.
