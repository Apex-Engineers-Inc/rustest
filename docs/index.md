# rustest

<div align="center" style="margin-bottom: 2rem;">
  <img src="assets/logo.svg" alt="rustest logo" style="height: 300px; width: 300px;">
</div>

<div align="center">

**Fast, Familiar Python Testing**
Rust-powered test runner with pytest-compatible API

```bash
pip install rustest
```

</div>

---

## Which describes you?

<div class="grid cards" markdown>

-   :seedling: **New to Testing**

    ---

    **Learn why automated testing helps you write better code**

    Testing catches bugs before your users do. We'll show you how to:

    - Write your first test in 5 minutes
    - Catch errors automatically
    - Make code changes with confidence
    - Build better software faster

    **No experience needed.** We'll teach you everything from scratch in a friendly, approachable way.

    [:octicons-arrow-right-24: Start Learning](new-to-testing/why-test.md)

-   :rocket: **Coming from pytest**

    ---

    **Speed up your test suite — 8.5× faster on average**

    Same decorators you know. Dramatically faster execution.

    **What you get:**

    - ✅ Drop-in compatible: `@fixture`, `@parametrize`, `@mark`
    - ✅ Built-in async support (no pytest-asyncio plugin)
    - ✅ Built-in mocking (no pytest-mock plugin)
    - ✅ Simple coverage integration (no plugin dance)
    - ✅ 5-minute migration for most projects

    **Try it now:**
    ```bash
    rustest --pytest-compat tests/
    ```

    [:octicons-arrow-right-24: See Feature Comparison](from-pytest/comparison.md){ .md-button .md-button--primary }
    [:octicons-arrow-right-24: Quick Migration Guide](from-pytest/migration.md){ .md-button }

</div>

---

## :thought_balloon: Why Rustest Exists

!!! quote ""
    **Short version:** Python testing is too slow. We can do better.

**Longer version:** I love pytest—the API is elegant, fixtures are powerful, and good tests make better code. But if you've used **vitest** or **bun test** in JavaScript/TypeScript, you know what fast testing feels like:

- Tests run in milliseconds, not seconds
- You get instant feedback on every save
- TDD becomes enjoyable, not tedious
- You stay in flow instead of context-switching

**Why doesn't Python have this?**

Rustest brings that experience to Python. Same pytest API you know, backed by Rust's performance. Fast tests aren't just convenient—they change how you develop.

!!! success "Our Philosophy"
    **Pytest nailed the API. Rustest brings the speed.**

---

## See It In Action

```python
from rustest import fixture, parametrize, mark

@fixture
def database():
    db = Database()
    yield db
    db.close()

@parametrize("username,expected", [
    ("alice", "alice@example.com"),
    ("bob", "bob@example.com"),
])
def test_user_email(database, username, expected):
    user = database.get_user(username)
    assert user.email == expected

@mark.asyncio
async def test_async_api():
    response = await fetch_data()
    assert response.status == 200
```

**Run with:** `rustest`

**Output:**
```
✓✓✓

✓ 3/3 3 passing (15ms)
```

---

## :chart_with_upwards_trend: Performance That Scales

| Suite Size | Speedup |
|-----------|---------|
| **Small** (< 20 tests) | **3-4× faster** |
| **Medium** (100-500 tests) | **5-8× faster** |
| **Large** (1,000+ tests) | **11-19× faster** |

[:octicons-arrow-right-24: View Full Performance Analysis](advanced/performance.md)

---

## Production Ready

<div class="grid cards" markdown>

-   :white_check_mark: MIT Licensed
-   :white_check_mark: Python 3.10-3.14
-   :white_check_mark: Active Development

-   :white_check_mark: Built-in async support
-   :white_check_mark: Built-in mocking
-   :white_check_mark: No plugin dependencies

-   :white_check_mark: pytest-compatible
-   :white_check_mark: Crystal-clear errors
-   :white_check_mark: Markdown testing

</div>

---

## Choose Your Path

=== "For Beginners"

    **Start from the beginning and learn testing fundamentals:**

    - [Why Automated Testing?](new-to-testing/why-test.md) — Learn the fundamentals
    - [Your First Test](new-to-testing/first-test.md) — Get started in 5 minutes
    - [Testing Basics](new-to-testing/testing-basics.md) — Core concepts explained
    - [Making Tests Reusable](new-to-testing/fixtures.md) — Introduction to fixtures
    - [Testing Multiple Cases](new-to-testing/parametrization.md) — Parametrization made simple
    - [Organizing Your Tests](new-to-testing/organizing.md) — Structure and best practices

=== "For pytest Users"

    **Get up to speed quickly:**

    - [Feature Comparison](from-pytest/comparison.md) — Complete feature compatibility table
    - [5-Minute Migration](from-pytest/migration.md) — Get running in minutes
    - [Plugin Replacement Guide](from-pytest/plugins.md) — Built-in alternatives to pytest plugins
    - [Coverage Integration](from-pytest/coverage.md) — Simple coverage.py integration
    - [Known Limitations](from-pytest/limitations.md) — What's not supported (yet)

=== "For Everyone"

    **Complete reference documentation:**

    - [Writing Tests](guide/writing-tests.md) — Test functions, classes, and structure
    - [Fixtures](guide/fixtures.md) — Complete fixtures reference
    - [Parametrization](guide/parametrization.md) — Advanced parametrization techniques
    - [Marks & Filtering](guide/marks.md) — Organizing and filtering tests
    - [Assertions](guide/assertions.md) — Assertion helpers and best practices
    - [CLI Reference](guide/cli.md) — Command-line options
    - [API Reference](api/overview.md) — Complete API documentation

---

## Community & Contributing

<div class="grid cards" markdown>

-   :material-github: **GitHub Repository**

    ---

    Star us, report issues, contribute code

    [:octicons-arrow-right-24: Apex-Engineers-Inc/rustest](https://github.com/Apex-Engineers-Inc/rustest)

-   :material-bug: **Issue Tracker**

    ---

    Found a bug? Have a feature request?

    [:octicons-arrow-right-24: Report an Issue](https://github.com/Apex-Engineers-Inc/rustest/issues)

-   :material-book-open-variant: **Contributing Guide**

    ---

    Help make rustest even better

    [:octicons-arrow-right-24: Development Guide](advanced/development.md)

-   :material-license: **License**

    ---

    MIT License — Free and open source

    [:octicons-arrow-right-24: View License](https://github.com/Apex-Engineers-Inc/rustest/blob/main/LICENSE)

</div>
